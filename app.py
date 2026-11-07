"""
LU Brain — Lady Up team AI ops assistant (MVP)

Run: streamlit run app.py
Requires env var: GEMINI_API_KEY (see .env.example)
"""
import datetime
import base64
from io import BytesIO
import json
import os
import re
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).parent
BRAIN_DIR = BASE / "sst_brain"
ASSETS = BASE / "assets"

BRAIN_FILES = {
    "Overview": "00_overview.md",
    "Long-term": "01_longterm.md",
    "Protocols": "02_protocols_workflows.md",
    "Projects": "03_active_projects.md",
    "Members": "04_members.md",
    "Tracking": "05_tracking.md",
}

STARTERS = [
    "Who am I on the team?",
    "What projects can I join right now?",
    "What are the deadlines for my project?",
    "What are my next 3 steps?",
]

# ---------- GitHub sync ----------
def github_push_file(rel_path: str, branch: str = "brain-updates") -> tuple:
    """Push one brain file to GitHub so runtime edits survive redeploys.

    Uses GITHUB_TOKEN from env / Streamlit Secrets (needs Contents: read+write
    on the repo). Pushes to a side branch, NOT main, so the live Streamlit app
    doesn't reboot on every save. Merge the branch to main on github.com when
    ready — that redeploys the app with the merged brain.
    """
    import base64, json, urllib.request, urllib.error
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return False, "no token"
    repo = os.getenv("GITHUB_REPO", "riley-wangdaiwei/LU-agent")
    base = f"https://api.github.com/repos/{repo}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json",
               "User-Agent": "lu-brain"}

    def req(method, url, data=None):
        r = urllib.request.Request(
            url, method=method,
            data=json.dumps(data).encode() if data else None,
            headers={**headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except Exception:
                body = {}
            return e.code, body

    # ensure the side branch exists (created from main on first use)
    status, _ = req("GET", f"{base}/branches/{branch}")
    if status == 404:
        status, main = req("GET", f"{base}/branches/main")
        if status != 200:
            return False, "cannot read main"
        status, _ = req("POST", f"{base}/git/refs",
                        {"ref": f"refs/heads/{branch}",
                         "sha": main["commit"]["sha"]})
        if status not in (200, 201):
            return False, "cannot create branch"
    elif status != 200:
        return False, "branch check failed"

    content = base64.b64encode((BASE / rel_path).read_bytes()).decode()
    for _ in range(2):  # one retry on SHA conflict
        status, cur = req("GET", f"{base}/contents/{rel_path}?ref={branch}")
        sha = cur.get("sha") if status == 200 else None
        payload = {"message": f"Sync {rel_path} from LU Brain app",
                   "content": content, "branch": branch}
        if sha:
            payload["sha"] = sha
        status, _ = req("PUT", f"{base}/contents/{rel_path}", payload)
        if status in (200, 201):
            return True, "ok"
        if status != 409:
            return False, f"github {status}"
    return False, "conflict"

# ---------- Brain ----------
def read_brain() -> dict:
    out = {}
    for name, fname in BRAIN_FILES.items():
        p = BRAIN_DIR / fname
        out[name] = p.read_text(encoding="utf-8") if p.exists() else "(empty)"
    return out

def brain_context() -> str:
    brain = read_brain()
    parts = [f"## {name}\n{content}" for name, content in brain.items()]
    return "# SST Brain\n\n" + "\n\n".join(parts)

# ---------- Progress updates + chase list (deterministic) ----------
# Chain — what must go where (mirrors the onboarding chain, which works
# because every step has a code path, not just a prompt instruction):
#   onboarding answers   -> 04_members.md  (profile block + confirm button)
#   activation intro     -> 05_tracking.md (activation list)
#   confirmed deadlines  -> 05_tracking.md (unchecked chase items)
#   progress updates     -> 03_active_projects.md (status / next / deadline)
#                           + 05_tracking.md (log line + chase item)
#   chase list           <- 03_active_projects.md + 05_tracking.md (code-built)

def extract_progress(text: str):
    m = re.search(r"```progress\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None

def _norm_name(s: str) -> str:
    # Match project names ignoring quotes / punctuation / spaces, so
    # e.g. 科普文耐力 still finds the heading 科普文"耐力".
    return re.sub(r"[^\w\u4e00-\u9fff]", "", s or "").lower()

def _set_section_lines(body: str, updates: dict) -> str:
    """Set '- key：value' lines inside one project section body."""
    for key, value in updates.items():
        if value is None:
            continue
        pat = re.compile(rf"^- {re.escape(key)}：.*$", re.M)
        line = f"- {key}：{value}"
        if pat.search(body):
            body = pat.sub(line, body, count=1)
        else:
            body = body.rstrip() + "\n" + line + "\n"
    return body

def apply_progress_update(prog: dict) -> dict:
    """Write one progress update to 03 + 05. Idempotent on re-runs:
    re-applying the same update replaces project lines (not duplicates)
    and skips tracking lines already present.
    Returns {"project", "tracking_added", "pushed"}."""
    member = prog.get("member") or "Unknown"
    project = prog.get("project") or "Unnamed project"
    today = datetime.date.today().isoformat()

    p = BRAIN_DIR / "03_active_projects.md"
    cur = p.read_text(encoding="utf-8") if p.exists() else "# Active Projects\n"
    heads = list(re.finditer(r"^-?\s*## (.+)$", cur, re.M))
    target = None
    for i, m in enumerate(heads):
        if _norm_name(project) and (_norm_name(project) in _norm_name(m.group(1))
                                    or _norm_name(m.group(1)) in _norm_name(project)):
            target = (m, heads[i + 1] if i + 1 < len(heads) else None)
            break
    updates = {}
    if prog.get("status"):
        updates["状态"] = prog["status"]
    if prog.get("completed_step"):
        updates["上一步已完成"] = prog["completed_step"]
    if prog.get("next_step"):
        updates["下一步"] = prog["next_step"]
    if prog.get("next_deadline"):
        updates["下次截止"] = prog["next_deadline"]
    if target:
        m, nxt = target
        start, end = m.end(), (nxt.start() if nxt else len(cur))
        cur = cur[:start] + _set_section_lines(cur[start:end], updates) + cur[end:]
    else:
        lines = [f"\n## {project}", ""]
        for k, v in updates.items():
            lines.append(f"- {k}：{v}")
        cur = cur.rstrip() + "\n" + "\n".join(lines) + "\n"
    p.write_text(cur, encoding="utf-8")

    t = BRAIN_DIR / "05_tracking.md"
    tcur = t.read_text(encoding="utf-8") if t.exists() else "# 追踪日志\n"
    entries = []
    if prog.get("completed_step"):
        entries.append(f"- [x] {prog['completed_step']} (project: {project})")
    if prog.get("next_step") and prog.get("next_deadline"):
        entries.append(f"- [ ] {prog['next_deadline']} — {prog['next_step']} (project: {project})")
    elif prog.get("next_step"):
        entries.append(f"- [ ] {prog['next_step']} (project: {project})")
    if prog.get("note"):
        entries.append(f"- note: {prog['note']}")
    new_entries = [e for e in entries if e not in tcur]
    if new_entries:
        block = f"\n## {today} — {member} project update\n" + "\n".join(new_entries) + "\n"
        t.write_text(tcur.rstrip() + "\n" + block, encoding="utf-8")

    pushed = []
    for rel in ("sst_brain/03_active_projects.md", "sst_brain/05_tracking.md"):
        ok, _ = github_push_file(rel)
        if ok:
            pushed.append(rel)
    return {"project": project, "tracking_added": len(new_entries), "pushed": pushed}

def parse_projects() -> list:
    """Every project in 03 with its current status / next step / deadline."""
    p = BRAIN_DIR / "03_active_projects.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    heads = list(re.finditer(r"^-?\s*## (.+)$", text, re.M))
    out = []
    for i, m in enumerate(heads):
        body = text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]

        def field(key):
            mm = re.search(rf"^- {re.escape(key)}：(.*)$", body, re.M)
            return mm.group(1).strip() if mm else ""
        out.append({"name": m.group(1).strip(), "status": field("状态"),
                    "next_step": field("下一步"), "next_deadline": field("下次截止"),
                    "completed": field("上一步已完成"),
                    "team": field("团队"), "schedule": field("排期"),
                    "blocker": field("阻塞") or field("blocker")})
    return out

def parse_members() -> list:
    """Read the member profiles already stored in the SST."""
    p = BRAIN_DIR / "04_members.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8")
    heads = list(re.finditer(r"^### (.+)$", text, re.M))
    members = []
    for i, match in enumerate(heads):
        body = text[match.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        fields = {}
        for line in body.splitlines():
            field = re.match(r"^- ([\w\u4e00-\u9fff]+):\s*(.*)$", line)
            if field:
                fields[field.group(1).lower()] = field.group(2).strip()
        members.append({
            "name": match.group(1).strip(),
            "role": fields.get("role", ""),
            "status": fields.get("status", ""),
            "current_task": fields.get("current_task", ""),
            "interests": fields.get("interests", ""),
            "availability": fields.get("availability", ""),
        })
    return members

def parse_recent_updates(limit: int = 5) -> list:
    """Return dated log entries, without inferring activity from project state."""
    p = BRAIN_DIR / "05_tracking.md"
    if not p.exists():
        return []
    entries = []
    current = ""
    for line in p.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^## (\d{4}-\d{2}-\d{2}) — (.+)$", line)
        if heading:
            current = f"{heading.group(1)} · {heading.group(2)}"
        elif current and line.startswith("- "):
            entries.append({"heading": current, "text": line[2:].strip()})
    return entries[-limit:][::-1]

def make_snapshot_pdf(projects: list, members: list, updates: list,
                      deadlines: list, blockers: list) -> bytes:
    """Create a one-page, source-backed team poster as a downloadable PDF."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    page_width, page_height = landscape(A3)
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(page_width, page_height))
    ink = colors.HexColor("#21232A")
    muted = colors.HexColor("#6C7180")
    blue = colors.HexColor("#6078D6")
    paper = colors.HexColor("#F4F2EE")
    lilac = colors.HexColor("#E8E8F1")
    pdf.setFillColor(paper)
    pdf.rect(0, 0, page_width, page_height, fill=1, stroke=0)

    margin, gap = 48, 20
    pdf.setFillColor(ink)
    pdf.roundRect(margin, page_height - 174, page_width - margin * 2, 126, 18, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("STSong-Light", 31)
    pdf.drawString(margin + 28, page_height - 100, "TEAM SNAPSHOT")
    pdf.setFont("STSong-Light", 13)
    pdf.setFillColor(colors.HexColor("#D7D9E5"))
    pdf.drawString(margin + 30, page_height - 130, "People update. LU coordinates. SST remembers.")
    pdf.setFont("STSong-Light", 11)
    pdf.drawRightString(page_width - margin - 28, page_height - 100,
                        datetime.date.today().isoformat())

    col_width = (page_width - margin * 2 - gap) / 2
    top = page_height - 198
    bottom = 54
    row_gap = 18
    row_height = (top - bottom - row_gap) / 2

    def card(x, y, w, h, title, lines, fill=colors.white, max_lines=9):
        pdf.setFillColor(fill)
        pdf.roundRect(x, y, w, h, 14, fill=1, stroke=0)
        pdf.setFillColor(blue)
        pdf.setFont("STSong-Light", 10)
        pdf.drawString(x + 18, y + h - 25, title.upper())
        pdf.setFillColor(ink)
        pdf.setFont("STSong-Light", 11)
        cursor = y + h - 49
        for text in lines[:max_lines]:
            text = str(text).replace("\n", " ")
            chunks = [text[i:i + 60] for i in range(0, len(text), 60)] or [""]
            for chunk in chunks:
                if cursor < y + 13:
                    return
                pdf.drawString(x + 18, cursor, chunk)
                cursor -= 16

    left = margin
    right = margin + col_width + gap
    row2_y = bottom
    row1_y = bottom + row_height + row_gap
    project_lines = [
        f"{item['name']}  ·  {item['status'] or '状态未填写'}"
        for item in projects
    ]
    member_lines = [
        f"{item['name']}"
        + (f" / {item['role']}" if item.get("role") else "")
        + (f"  ·  {item['current_task']}" if item.get("current_task") else "")
        + (f"  ·  {item['status']}" if item.get("status") else "")
        for item in members
    ]
    update_lines = [f"{item['heading']} — {item['text']}" for item in updates]
    deadline_lines = [
        f"Next: {item['name']} · {item['next_step']}"
        for item in projects if item.get("next_step")
    ] + [
        f"{item['deadline']} · {item['step']}"
        + (f" ({item['project']})" if item.get("project") else "")
        for item in deadlines
    ]
    card(left, row1_y, col_width, row_height, "Project pulse", project_lines, lilac, max_lines=14)
    card(right, row1_y, col_width, row_height, "Team & responsibilities", member_lines)
    card(left, row2_y, col_width, row_height, "Recent notes", update_lines)
    card(right, row2_y, col_width, row_height, "Next · blockers", [
        *[f"Next: {line}" for line in deadline_lines],
        *[f"Blocker: {line}" for line in blockers],
        *(["No next steps or blockers recorded in SST."] if not deadline_lines and not blockers else []),
    ], lilac)
    pdf.setFillColor(muted)
    pdf.setFont("STSong-Light", 8)
    pdf.drawString(margin, 30, "Generated from the LU SST Brain · Responsibilities and decisions stay with the team.")
    pdf.save()
    return stream.getvalue()

def parse_tracking():
    """Parse 05 into (deadline items, activation items, other items,
    last-activity date per member). Only '- [ ]' lines are chaseable;
    '- [x]' lines are done and never chased."""
    p = BRAIN_DIR / "05_tracking.md"
    if not p.exists():
        return [], [], [], {}
    items, activation, other, activity = [], [], [], {}
    member = ""
    for line in p.read_text(encoding="utf-8").splitlines():
        hm = re.match(r"^## (\d{4}-\d{2}-\d{2}) — (.+)$", line)
        if hm:
            rest = hm.group(2)
            member = (rest.split(" confirmed")[0].split(" ready ")[0]
                      .split(" project update")[0].strip())
            try:
                dt = datetime.date.fromisoformat(hm.group(1))
            except ValueError:
                continue
            if member not in activity or dt > activity[member]:
                activity[member] = dt
            continue
        dm = re.match(r"^- \[ \] (\d{4}-\d{2}-\d{2}) — (.+)$", line)
        if dm:
            step = dm.group(2)
            pm = re.search(r"\(project: (.+)\)\s*$", step)
            project = pm.group(1) if pm else ""
            if pm:
                step = step[:pm.start()].strip()
            items.append({"deadline": dm.group(1), "step": step,
                          "project": project, "member": member})
            continue
        if line.startswith("- [ ]") and "main group" in line:
            activation.append({"member": member, "line": line[6:].strip()})
            continue
        if line.startswith("- [ ]"):
            other.append({"member": member, "line": line[6:].strip()})
    return items, activation, other, activity

def _parse_date(s: str):
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None

def chase_reminder(item: dict, today: datetime.date) -> str:
    d = _parse_date(item["deadline"])
    delta = (d - today).days if d else None
    when = ("已逾期 " + str(-delta) + " 天" if delta is not None and delta < 0
            else ("今天截止" if delta == 0 else f"还有 {delta} 天"))
    who = item["member"] or "相关成员"
    proj = f"（项目：{item['project']}）" if item["project"] else ""
    return f"提醒：{who}，{item['step']}{proj}，{item['deadline']} 截止（{when}），请同步一下进度。"

# ---------- Gemini ----------
def get_client():
    from google import genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

SYSTEM_PROMPT = """You are "LU Brain", the AI ops assistant for Lady Up, a women's sports
learning community. You replace the exec team's one-on-one member tracking:
you get to know members, match them with tasks, and track progress.

You have the SST Brain (shared team knowledge base): overview, long-term plans,
protocols & workflows, active projects, member profiles, tracking log.
Answer from it. Never invent facts that are not in the brain.

Style: plain, warm, concise. No emojis. Reply in English by default;
if the user writes in Chinese, reply in Chinese.
Pace yourself: advance one step per turn. Never dump onboarding, next steps,
and brainstorming into a single reply.

Your capabilities:
1. ONBOARDING (proactive): when a new member arrives, YOU start the conversation.
   Do not dump a list of questions. Ask one question at a time, conversationally,
   in this order: (1) name and role, (2) 3-5 interest tags, (3) approximate weekly
   availability plus busy periods, (4) skills or resources they bring,
   (5) what they are working on now, (6) status: active / busy / on leave.
   Keep it light — the whole thing should take under 3 minutes.
   When all six are collected, output a ```profile fenced code block with JSON
   (keys: name, role, interests, availability, resources, current_task, status),
   then ask the user to confirm.
   Existing members can also ask to update their profile. Ask only for the
   changed information, then output the same profile block with all known
   values so the member can confirm the update.
   After a new member's profile is confirmed and saved, do POST-ONBOARDING
   ACTIVATION (one thing at a time, conversationally): (a) briefly introduce the three
   work types from the brain — interview pieces, explainer articles, roundtable
   discussions; (b) propose 3 concrete first steps, then confirm a deadline for
   each (capability 5): 1) follow the team's Instagram and WeChat public account
   and write their 1-2 sentence self-intro (offer to draft it with them; when the
   intro is final, output a ```activation fenced code block with JSON
   {"member": "<name>", "intro": "<1-2 sentences>"} and tell them an exec will
   add them to the main group, where they post the intro themselves);
   2) add at least one exec on WeChat; 3) pick the track that interests them most
   and produce a small project-direction deliverable (e.g. bullet points on
   topics and guests they find interesting).
2. Answer questions about team protocols, workflows, and project schedules.
3. Match members with tasks: recommend from active projects and workflows based
   on their interests, availability, and resources.
4. PROGRESS UPDATES (automatic, no asking): when a member reports progress —
   a step finished, a next step, a status change, or a date change for a
   project — acknowledge it briefly, then ALWAYS output a ```progress fenced
   code block with JSON:
   {"member": "<the chatting member's name from their identity>",
   "project": "<project name as in the brain, or the name they used>",
   "completed_step": "<step just finished, or null>",
   "next_step": "<next step, or null>", "status": "<one-line current
   status, or null>", "next_deadline": "YYYY-MM-DD or null",
   "note": "<short note, or null>"}.
   The app presents this as a structured update and waits for the member to
   confirm before writing it to the project file and tracking log. If the
   project is not in the brain yet, still output the block with the name they
   used; the app will create the project entry after confirmation. Never
   invent a deadline or a step they did not state; use null when unknown.
   A member reporting their OWN progress is a fact to record, not a decision
   for someone else — it needs no exec confirmation. Task assignment and
   schedule changes for OTHER people are still suggestions only.
5. SCHEDULE BREAKDOWN + DEADLINE CONFIRMATION: when a member asks about a deadline
   or their next steps, work backwards from the big deadline using the workflow
   in the brain (e.g. the 5-week interview cycle: contact -> schedule -> draft ->
   publish). Give them their NEXT 3 concrete steps with suggested dates, small
   enough to act on this week. The first step is always a concrete, visible
   deliverable (a short intro, a bullet-points doc) — never "think about it".
   Then ALWAYS confirm deadlines: propose one specific
   calendar date per step and ask the member to confirm or adjust each one,
   conversing naturally until all three dates are fixed. When the member confirms,
   output a ```deadlines fenced code block with JSON:
   {"member": "<name>", "items": [{"step": "<step>", "deadline": "YYYY-MM-DD",
   "project": "<project>"}, ...]}. Then tell them these deadlines are now on the
   exec chase list so an exec will follow up on each one.
6. WEEKLY CHASE LIST (for execs): the app builds it deterministically from
   the brain — it is NOT generated by you in chat. Order: (a) current status
   of EVERY project in the project file; (b) deadlines to chase — overdue
   first, then due within 7 days, then all other unchecked deadlines soonest
   first; (c) silent members to re-engage; (d) members awaiting activation
   (if a member's intro is ready in the tracking log, include it in the
   reminder line so the exec can forward it when adding them to the group);
   (e) items needing the team lead or exec to decide. Each item gets one
   short, copy-paste-ready reminder line. If an exec asks about the chase list
   in chat, answer from the brain and point them to the Chase List page.
7. TOPIC BRAINSTORM: when a member wants a project direction but has none,
   combine their interests with their skills/resources from their profile
   (e.g. gender studies background x football hobby) and propose 3-5 SPECIFIC
   topic angles plus concrete potential interview subjects or guests for each.
   Ask which resonates, refine round by round — never settle for vague themes.
   End with a concrete deliverable: the member sends bullet points on their
   chosen topics/guests within a week, then flow into deadline confirmation.

You never make decisions for people: task assignment and schedule changes are
suggestions only — tell the user to confirm with an exec or the team lead.
"""

def chat_once(client, history: list, user_msg: str, identity_note: str = "") -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    contents = []
    for h in history:
        contents.append({"role": h["role"], "parts": [{"text": h["text"]}]})
    contents.append({"role": "user", "parts": [{"text": user_msg}]})
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config={"system_instruction": SYSTEM_PROMPT + "\n\n" + identity_note + "\n\n" + brain_context()},
    )
    return resp.text

# ---------- Member identity ----------
def get_member_names() -> list:
    p = BRAIN_DIR / "04_members.md"
    if not p.exists():
        return []
    return re.findall(r"^### (.+)$", p.read_text(encoding="utf-8"), re.M)

def get_member_block(name: str) -> str:
    p = BRAIN_DIR / "04_members.md"
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    m = re.search(rf"^### {re.escape(name)}\s*\n(.*?)(?=^### |\Z)",
                  text, re.M | re.S)
    return m.group(1).strip() if m else ""

def identity_note() -> str:
    ident = st.session_state.get("identity", "Not identified")
    if ident in ("Not identified", "I'm new here"):
        return ("The person chatting has not identified themselves yet. "
                "If they ask 'who am I', invite them to choose their name "
                "at the start of the page or begin the onboarding.")
    block = get_member_block(ident)
    return (f"The person you are talking to has identified as: {ident}.\n"
            f"Their saved profile from the member list:\n{block}\n"
            "Use this to personalize answers (identity questions, task "
            "matching, deadlines, progress).")

def save_member_profile(profile: dict) -> str:
    """Create or update a member section while retaining known fields."""
    path = BRAIN_DIR / "04_members.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    name = str(profile.get("name") or "New member").strip()
    heading = re.compile(rf"^### {re.escape(name)}\s*$", re.M)
    match = heading.search(text)
    start = match.start() if match else len(text)
    next_heading = re.search(r"^### .+$", text[match.end():], re.M) if match else None
    end = match.end() + next_heading.start() if next_heading else len(text)
    existing_body = text[match.end():end] if match else ""
    fields = {}
    for line in existing_body.splitlines():
        field = re.match(r"^- ([\w\u4e00-\u9fff]+):\s*(.*)$", line)
        if field:
            fields[field.group(1)] = field.group(2).strip()
    for key, value in profile.items():
        if key != "name" and value not in (None, ""):
            fields[key] = str(value).strip()
    preferred_order = ["role", "interests", "availability", "resources",
                       "current_task", "status"]
    keys = [key for key in preferred_order if key in fields]
    keys.extend(key for key in fields if key not in keys)
    section = "### " + name + "\n" + "".join(
        f"- {key}: {fields[key]}\n" for key in keys
    )
    updated = text[:start].rstrip() + ("\n\n" if text[:start].strip() else "")
    updated += section
    if end < len(text):
        updated += "\n" + text[end:].lstrip("\n")
    path.write_text(updated.rstrip() + "\n", encoding="utf-8")
    return name

from hub_ui import render_hub

render_hub(
    base=BASE,
    get_client=get_client,
    chat_once=chat_once,
    identity_note=identity_note,
    get_member_names=get_member_names,
    get_member_block=get_member_block,
    read_brain=read_brain,
    parse_projects=parse_projects,
    parse_members=parse_members,
    parse_tracking=parse_tracking,
    parse_recent_updates=parse_recent_updates,
    make_snapshot_pdf=make_snapshot_pdf,
    apply_progress_update=apply_progress_update,
    save_member_profile=save_member_profile,
    github_push_file=github_push_file,
)
