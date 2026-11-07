"""
LU Brain — Lady Up team AI ops assistant (MVP)

Run: streamlit run app.py
Requires env var: GEMINI_API_KEY (see .env.example)
"""
import datetime
import base64
import html
import hashlib
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
   (keys: name, interests, availability, resources, current_task, status),
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
   {"member": "<the chatting member's name from the sidebar identity>",
   "project": "<project name as in the brain, or the name they used>",
   "completed_step": "<step just finished, or null>",
   "next_step": "<next step, or null>", "status": "<one-line current
   status, or null>", "next_deadline": "YYYY-MM-DD or null",
   "note": "<short note, or null>"}.
   Do NOT ask whether to save it, and do not ask twice: the app saves it
   automatically to the project file and the tracking log and shows a saved
   note. If the project is not in the brain yet, still output the block with
   the name they used; the app will create the project entry. Never invent
   a deadline or a step they did not state; use null when unknown.
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
                "If they ask 'who am I', invite them to pick their name in "
                "the sidebar or do the onboarding.")
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

def navigate_to(page: str, identity: str = ""):
    st.session_state.nav = page
    if identity:
        st.session_state.identity = identity

# ---------- Page ----------
st.set_page_config(page_title="LU · Team Coordination", page_icon=None, layout="wide")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&family=DM+Serif+Display&display=swap');
:root {{
    --paper: #F3F4F8; --white: rgba(255,255,255,.82); --ink: #242630; --muted: #777B8B;
    --line: #E2E4ED; --lilac: #E9EAF3; --blue: #7185F5; --blue-dark: #5068D8;
    --cyan: #92C4FF; --violet: #9B8CFF;
    --green: #2F795D; --amber: #B77A2D; --rose: #B95F69;
}}
html, body, [class*="css"] {{
    font-family: 'DM Sans', 'Helvetica Neue', Arial, sans-serif;
    background: radial-gradient(ellipse at 18% 0%, rgba(119,134,235,.10), transparent 42%), var(--paper);
    color: var(--ink);
}}
[data-testid="stAppViewContainer"], [data-testid="stMain"] {{
    background: radial-gradient(ellipse at 18% 0%, rgba(119,134,235,.10), transparent 42%), var(--paper) !important;
}}
h1, h2, h3 {{ color: var(--ink); letter-spacing: -0.035em; }}
.block-container {{ max-width: 1420px; padding: 2.2rem 3.2rem 4rem; }}
.serif {{ font-family: 'DM Serif Display', Georgia, serif; font-weight: 400; }}
.eyebrow, .mono {{ font-family: 'DM Mono', monospace; letter-spacing: .08em; text-transform: uppercase; }}
.eyebrow {{ color: var(--blue); font-size: .72rem; }}
a {{ color: var(--blue-dark) !important; }}
hr {{ border: none; border-top: 1px solid var(--line); margin: 1.5rem 0; }}
.stButton > button {{
    border-radius: 999px; border: 1px solid var(--line);
    background: rgba(255,255,255,.86); color: var(--ink); font-size: .86rem; padding: .5rem 1rem;
    transition: all .22s ease; box-shadow: 0 4px 14px rgba(48,55,91,.04);
}}
.stButton > button:hover {{ border-color: var(--blue); color: var(--blue-dark); transform: translateY(-2px); box-shadow: 0 9px 24px rgba(84,104,214,.14); }}
.stButton > button[kind="primary"] {{ background: linear-gradient(115deg,#6479E8,#7F75E8); border-color: transparent; color: #fff; box-shadow: 0 7px 20px rgba(92,109,224,.22); }}
.stButton > button[kind="primary"]:hover {{ background: linear-gradient(115deg,#536BD9,#6E63D8); color: #fff; box-shadow: 0 10px 28px rgba(92,109,224,.3); }}
.stChatMessage {{ background: var(--white); border: 1px solid var(--line); border-radius: 18px; }}
section[data-testid="stSidebar"] {{ background: rgba(235,237,245,.92); border-right: 1px solid rgba(212,216,231,.9); backdrop-filter: blur(18px); }}
section[data-testid="stSidebar"] > div {{ padding-top: 1.5rem; }}
section[data-testid="stSidebar"] [data-testid="stRadio"] label {{ font-size: .93rem; }}
section[data-testid="stMain"] {{ background: var(--paper); }}
.stTabs [data-baseweb="tab"][aria-selected="true"],
.stTabs button[role="tab"][aria-selected="true"] {{
    color: var(--blue-dark) !important; border-bottom-color: var(--blue) !important;
}}
.hero {{
    background: radial-gradient(ellipse at 88% 18%,rgba(113,133,245,.34),transparent 40%),
                radial-gradient(ellipse at 10% 100%,rgba(109,120,204,.18),transparent 42%), #232530;
    border: 1px solid rgba(255,255,255,.11); border-radius: 26px; color: #fff; overflow: hidden;
    min-height: 390px; padding: 3.25rem 3rem; position: relative;
    box-shadow: 0 28px 75px rgba(42,47,83,.19), inset 0 1px 0 rgba(255,255,255,.12);
    isolation: isolate;
}}
.hero:before {{
    content: ""; position: absolute; inset: 0; z-index: -1; opacity: .16;
    background-image: linear-gradient(rgba(196,204,255,.24) 1px, transparent 1px),
                      linear-gradient(90deg, rgba(196,204,255,.24) 1px, transparent 1px);
    background-size: 34px 34px; mask-image: linear-gradient(135deg,black,transparent 72%);
}}
.hero-visual {{ min-height: 570px; }}
.hero h1 {{ color: #fff; font: 400 clamp(2.8rem, 4vw, 4.2rem) 'DM Serif Display', Georgia, serif !important; line-height: .98; margin: .8rem 0 1.1rem; }}
.hero p {{ color: #D0D0D9; font-size: 1.05rem; line-height: 1.7; max-width: 540px; }}
.hero-kettle {{ width: 100%; max-height: 345px; object-fit: contain; filter: drop-shadow(0 18px 34px rgba(0,0,0,.4)) drop-shadow(0 0 34px rgba(146,177,255,.22)); animation: float 6s ease-in-out infinite; }}
@keyframes float {{ 0%,100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-9px); }} }}
.surface {{ background: var(--white); backdrop-filter: blur(14px); border: 1px solid rgba(219,222,235,.9); border-radius: 18px; padding: 1.25rem 1.4rem; box-shadow: 0 10px 28px rgba(37,43,78,.045); transition: transform .22s ease, box-shadow .22s ease; }}
.surface:hover {{ transform: translateY(-2px); box-shadow: 0 16px 34px rgba(37,43,78,.09); }}
.surface-lilac {{ background: linear-gradient(135deg,rgba(233,234,244,.94),rgba(243,244,249,.8)); border: 1px solid rgba(216,220,236,.95); border-radius: 18px; padding: 1.25rem 1.4rem; }}
.metric {{ font: 500 2rem 'DM Mono', monospace; letter-spacing: -.08em; color: var(--ink); background:linear-gradient(100deg,#343849,#6479D6);-webkit-background-clip:text;-webkit-text-fill-color:transparent; }}
.project-title {{ font: 400 1.35rem 'DM Serif Display', Georgia, serif; color: var(--ink); }}
.status {{ display:inline-block; padding:.25rem .6rem; border-radius:99px; font:500 .68rem 'DM Mono',monospace; text-transform:uppercase; letter-spacing:.04em; background:#ECEBF0; color:#646573; }}
.status-active {{ background:#E2EFE7; color:#2F795D; }}
.status-blocked {{ background:#F8E8E7; color:#A64852; }}
.status-progress {{ background:#E8EAF8; color:#5268C3; }}
.small-muted {{ color:var(--muted); font-size:.86rem; line-height:1.55; }}
.section-title {{ font:400 2rem 'DM Serif Display', Georgia, serif; letter-spacing:-.02em; }}
.step-card {{ border-top:1px solid var(--line); padding:1rem 0 .4rem; }}
.poster {{ background:radial-gradient(ellipse at 95% 0%,rgba(113,133,245,.38),transparent 42%),#252630; color:#fff; border:1px solid rgba(255,255,255,.12); border-radius:24px; padding:2rem; box-shadow:0 20px 60px rgba(39,44,77,.18); }}
.poster h2 {{ color:#fff; font:400 2.8rem 'DM Serif Display', Georgia, serif; }}
@media (max-width: 800px) {{
    .block-container {{ padding: 1.3rem 1rem 3rem; }}
    .hero {{ padding: 2rem 1.5rem; min-height: unset; }}
    .hero-visual {{ min-height: 340px; }}
    .hero h1 {{ font-size: 3.3rem; }}
}}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    logo = ASSETS / "lu_new_1.png"
    if logo.exists():
        st.image(str(logo), width=58)
    st.markdown('<h3 style="margin:.25rem 0 0">LU <span style="font-weight:400">/ team hub</span></h3>', unsafe_allow_html=True)
    st.caption("People update · LU coordinates · SST remembers")
    nav = st.radio(
        "Navigate",
        ["Team Overview", "LU Chat", "Projects", "SST Brain"],
        key="nav",
        label_visibility="collapsed",
    )
    st.divider()
    names = get_member_names()
    st.selectbox(
        "Your team identity",
        ["Not identified"] + names + ["I'm new here"],
        key="identity",
        help="Choose your name so LU can personalize your updates. New here? Start onboarding in LU Chat.",
    )
    if st.session_state.get("identity") not in (None, "Not identified", "I'm new here"):
        st.caption(f"Working as · {st.session_state.identity}")
    st.divider()
    ok = (BRAIN_DIR / "00_overview.md").exists()
    st.caption("● SST connected" if ok else "○ SST files missing")

# ---------- Chat ----------
if nav == "LU Chat":
    st.markdown('<div class="eyebrow">One conversation, shared context</div><h1 class="section-title">Talk it through with LU.</h1>', unsafe_allow_html=True)
    st.caption("Share progress, ask for context, or work through onboarding. LU organizes updates into the shared brain; people keep the decisions.")

    client = get_client()
    if client is None:
        st.warning("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key, then restart.")
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Auto-start onboarding if the visitor picked "I'm new here"
    if (not st.session_state.messages
            and st.session_state.get("identity") == "I'm new here"
            and not st.session_state.get("onboard_auto_started")):
        st.session_state.onboard_auto_started = True
        st.session_state.messages.append(
            {"role": "user", "text": "Hi, I just joined the team. Please onboard me."})
        with st.spinner("Thinking..."):
            try:
                reply = chat_once(client, [], st.session_state.messages[-1]["text"],
                                  identity_note())
            except Exception as e:
                reply = f"Something went wrong: {e}"
        st.session_state.messages.append({"role": "assistant", "text": reply})

    def extract_profile(text: str):
        m = re.search(r"```profile\s*(\{.*?\})\s*```", text, re.S)
        if not m:
            return None
        import json
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    def extract_deadlines(text: str):
        m = re.search(r"```deadlines\s*(\{.*?\})\s*```", text, re.S)
        if not m:
            return None
        import json
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    def extract_activation(text: str):
        m = re.search(r"```activation\s*(\{.*?\})\s*```", text, re.S)
        if not m:
            return None
        import json
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    def send_and_reply(user_text: str):
        st.session_state.messages.append({"role": "user", "text": user_text})
        with st.chat_message("user"):
            st.markdown(user_text)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    reply = chat_once(client, st.session_state.messages[:-1], user_text,
                                      identity_note())
                except Exception as e:
                    reply = f"Something went wrong: {e}"
            st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "text": reply})
        st.session_state.profile_saved = False
        st.session_state.deadlines_saved = False
        st.session_state.activation_saved = False

    # Starter questions (only before the conversation starts)
    if not st.session_state.messages:
        st.markdown("Start with one of these, or just say hi.")
        cols = st.columns(2)
        for i, q in enumerate(STARTERS):
            with cols[i % 2]:
                if st.button(q, key=f"starter_{i}"):
                    send_and_reply(q)
                    st.rerun()
        st.divider()
        if st.button("I am new here — start onboarding", type="primary", key="onboard_btn"):
            send_and_reply("Hi, I just joined the team. Please onboard me.")
            st.rerun()

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])

    # Pending profile confirmation
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        prof = extract_profile(st.session_state.messages[-1]["text"])
        if prof and not st.session_state.get("profile_saved"):
            st.info("If this looks right, save it to the member profiles:")
            if st.button("Confirm and save profile", type="primary"):
                save_member_profile(prof)
                st.session_state.profile_saved = True
                ok, msg = github_push_file("sst_brain/04_members.md")
                if ok:
                    st.success("Saved and synced to GitHub (brain-updates branch).")
                else:
                    st.success("Saved in the app.")
                    st.caption(f"GitHub sync skipped ({msg}). Add GITHUB_TOKEN "
                               "to Secrets to sync automatically.")

    # Pending deadlines confirmation
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        dl = extract_deadlines(st.session_state.messages[-1]["text"])
        if dl and not st.session_state.get("deadlines_saved"):
            st.info("If these dates look right, put them on the exec chase list:")
            if st.button("Confirm and save deadlines", type="primary"):
                p = BRAIN_DIR / "05_tracking.md"
                cur = p.read_text(encoding="utf-8") if p.exists() else ""
                today = datetime.date.today().isoformat()
                member = dl.get("member") or st.session_state.get("identity", "")
                lines = [f"\n## {today} — {member} confirmed deadlines"]
                for it in dl.get("items", []):
                    proj = f" (project: {it['project']})" if it.get("project") else ""
                    lines.append(f"- [ ] {it.get('deadline')} — {it.get('step')}{proj}")
                p.write_text(cur.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
                st.session_state.deadlines_saved = True
                ok, msg = github_push_file("sst_brain/05_tracking.md")
                if ok:
                    st.success("Deadlines saved and synced to GitHub (brain-updates branch).")
                else:
                    st.success("Deadlines saved in the app.")
                    st.caption(f"GitHub sync skipped ({msg}). Add GITHUB_TOKEN "
                               "to Secrets to sync automatically.")

    # Pending activation (intro ready -> exec adds member to the group)
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        act = extract_activation(st.session_state.messages[-1]["text"])
        if act and not st.session_state.get("activation_saved"):
            st.info("Intro ready. Save it so an exec can add this member to the group:")
            st.code(act.get("intro", ""), language=None)
            if st.button("Save to activation list", type="primary"):
                p = BRAIN_DIR / "05_tracking.md"
                cur = p.read_text(encoding="utf-8") if p.exists() else ""
                today = datetime.date.today().isoformat()
                member = act.get("member") or st.session_state.get("identity", "")
                lines = [f"\n## {today} — {member} ready for group activation",
                         f"- [ ] Add {member} to the main group. Intro: {act.get('intro', '')}"]
                p.write_text(cur.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
                st.session_state.activation_saved = True
                ok, msg = github_push_file("sst_brain/05_tracking.md")
                if ok:
                    st.success("Saved and synced to GitHub (brain-updates branch).")
                else:
                    st.success("Saved in the app.")
                    st.caption(f"GitHub sync skipped ({msg}). Add GITHUB_TOKEN "
                               "to Secrets to sync automatically.")

    # Auto-saved progress updates — this is the chain that was missing.
    # The model outputs a ```progress block; the app writes it to the
    # project file + tracking log exactly once per reply. No "do you want
    # to save this?" loop: a member reporting their own progress is a fact
    # to record, and the saved note below is confirmation, not a question.
    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        prog = extract_progress(st.session_state.messages[-1]["text"])
        if prog:
            if "progress_applied" not in st.session_state:
                st.session_state.progress_applied = set()
            key = hashlib.sha256(
                st.session_state.messages[-1]["text"].encode("utf-8")).hexdigest()
            if key not in st.session_state.progress_applied:
                st.session_state.progress_applied.add(key)
                result = apply_progress_update(prog)
                st.success(f"已更新：{result['project']} 的状态和下一步已写入项目文件，"
                           f"追人清单也同步了（新增 {result['tracking_added']} 条记录）。")
                if result["pushed"]:
                    st.caption("已同步到 GitHub（brain-updates branch）：" +
                               "、".join(result["pushed"]) + "。")
                else:
                    st.caption("已保存在应用内；未同步到 GitHub（无 GITHUB_TOKEN 时跳过同步）。")

    if prompt := st.chat_input("Message LU Brain..."):
        send_and_reply(prompt)
        st.rerun()

# ---------- Team overview ----------
elif nav == "Team Overview":
    projects = parse_projects()
    members = parse_members()
    updates = parse_recent_updates()
    deadline_items, activation_items, other_items, activity = parse_tracking()
    blockers = [
        project for project in projects
        if project.get("blocker")
        or re.search(r"blocked|阻塞|卡住", project.get("status", ""), re.I)
    ]

    hero_asset = ASSETS / "kettle_hero.png"
    hero_image = ""
    if hero_asset.exists():
        encoded = base64.b64encode(hero_asset.read_bytes()).decode("ascii")
        hero_image = f'<img class="hero-kettle" src="data:image/png;base64,{encoded}" alt="LU kettle mascot">'

    left, right = st.columns([1.2, .8], gap="large")
    with left:
        st.markdown(
            '<div class="hero"><div class="eyebrow" style="color:#AEB9F2">Lady Up · team coordination</div>'
            '<h1 class="serif">Work moves<br>better together.</h1>'
            '<p>One place to see what the team is making, share what changed, and keep the context that helps everyone move.</p>'
            '<div class="mono" style="font-size:.72rem;color:#AEB0BD;margin-top:1.6rem">PEOPLE → LU → SST BRAIN</div></div>',
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f'<div class="hero hero-visual" style="padding:1.3rem;display:flex;align-items:center;justify-content:center">{hero_image}'
            '<div class="mono" style="position:absolute;right:24px;bottom:20px;font-size:.62rem;color:#AEB0BD">LU · KEEPS THE THREAD</div></div>',
            unsafe_allow_html=True,
        )

    st.write("")
    metric_cols = st.columns(4)
    metrics = [
        (len(projects), "Projects in SST"),
        (len(members), "Member profiles"),
        (len(deadline_items), "Open follow-ups"),
        (len(updates), "Recent log entries"),
    ]
    for column, (value, label) in zip(metric_cols, metrics):
        with column:
            st.markdown(
                f'<div class="surface"><div class="metric">{value:02d}</div>'
                f'<div class="small-muted">{html.escape(label)}</div></div>',
                unsafe_allow_html=True,
            )

    overview_tab, followups_tab = st.tabs(["Team pulse", "Coordination follow-ups"])
    with overview_tab:
        st.write("")
        project_col, team_col = st.columns([1.2, .8], gap="large")
        with project_col:
            st.markdown('<div class="section-title">In motion</div>', unsafe_allow_html=True)
            if not projects:
                st.info("No projects are listed in the SST yet.")
            for project in projects[:4]:
                status = project.get("status") or "Status not recorded"
                tone = ("status-blocked" if re.search(r"blocked|阻塞|卡住", status, re.I)
                        else "status-active" if re.search(r"active|持续|进行|排版中", status, re.I)
                        else "status-progress")
                st.markdown(
                    f'<div class="surface" style="margin:.75rem 0">'
                    f'<div class="eyebrow">PROJECT · SST</div>'
                    f'<div class="project-title">{html.escape(project["name"])}</div>'
                    f'<div style="margin:.45rem 0 .7rem"><span class="status {tone}">{html.escape(status)}</span></div>'
                    f'<div class="small-muted">{html.escape(project.get("next_step") or project.get("schedule") or "Next step not recorded in SST.")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            if len(projects) > 4:
                st.caption(f"Showing 4 of {len(projects)} projects.")
            st.button("See all projects →", on_click=navigate_to, args=("Projects",))

        with team_col:
            st.markdown('<div class="section-title">People & now</div>', unsafe_allow_html=True)
            if not members:
                st.info("Member profiles will appear here as people onboard.")
            for member in members:
                task = member.get("current_task") or "No current task recorded"
                status = member.get("status") or "Status not recorded"
                st.markdown(
                    f'<div class="surface-lilac" style="margin:.75rem 0">'
                    f'<div style="display:flex;justify-content:space-between;gap:1rem">'
                    f'<strong>{html.escape(member["name"])}</strong>'
                    f'<span class="mono" style="font-size:.66rem">{html.escape(status)}</span></div>'
                    f'<div class="small-muted" style="margin-top:.55rem">{html.escape(task)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            st.markdown('<div class="surface" style="margin-top:1rem"><div class="eyebrow">THE WAY WE WORK</div>'
                        '<div class="small-muted" style="margin-top:.5rem">Share what changed in LU Chat. LU records it; project owners and teammates make the calls.</div></div>',
                        unsafe_allow_html=True)

        st.write("")
        updates_col, next_col = st.columns(2, gap="large")
        with updates_col:
            st.markdown('<div class="section-title">Latest tracking notes</div>', unsafe_allow_html=True)
            if updates:
                for update in updates[:3]:
                    st.markdown(
                        f'<div class="step-card"><div class="mono" style="font-size:.66rem;color:#777987">{html.escape(update["heading"])}</div>'
                        f'<div style="margin-top:.35rem">{html.escape(update["text"])}</div></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No recent updates have been recorded.")
        with next_col:
            st.markdown('<div class="section-title">Next & blockers</div>', unsafe_allow_html=True)
            next_steps = [p for p in projects if p.get("next_step") or p.get("next_deadline")]
            if next_steps:
                for project in next_steps[:3]:
                    detail = project.get("next_step") or project.get("next_deadline")
                    st.markdown(f"- **{html.escape(project['name'])}** — {html.escape(detail)}")
            else:
                st.caption("No next steps are currently recorded.")
            if deadline_items:
                st.markdown("**Tracked dates**")
                for item in deadline_items[:3]:
                    date = _parse_date(item["deadline"])
                    marker = "Past due" if date and date < datetime.date.today() else "Due"
                    project_label = f" · {item['project']}" if item.get("project") else ""
                    st.markdown(
                        f"- **{marker} · {item['deadline']}** — "
                        f"{html.escape(item['step'] + project_label)}"
                    )
            if blockers:
                for project in blockers:
                    st.warning(f"{project['name']}: {project.get('blocker') or project.get('status')}")
            else:
                st.caption("No blockers are reported in the SST.")

        st.write("")
        story_img, story_copy = st.columns([.28, .72], gap="large")
        with story_img:
            supporting_kettle = ASSETS / "kettle_cloth.jpg"
            if supporting_kettle.exists():
                st.image(str(supporting_kettle), width=190)
        with story_copy:
            st.markdown('<div class="surface-lilac"><span class="eyebrow">HOW WE STAY ALIGNED</span>'
                        '<div style="display:flex;gap:1rem;flex-wrap:wrap;margin-top:.7rem">'
                        '<span><b>01 · People update</b><br><span class="small-muted">Progress, context, capacity</span></span>'
                        '<span class="mono" style="color:#6479D6">→</span>'
                        '<span><b>02 · LU coordinates</b><br><span class="small-muted">Organizes, clarifies, reminds</span></span>'
                        '<span class="mono" style="color:#6479D6">→</span>'
                        '<span><b>03 · SST remembers</b><br><span class="small-muted">One shared team picture</span></span>'
                        '</div></div>', unsafe_allow_html=True)

        cta, cta2 = st.columns([1, 3])
        with cta:
            st.button("Share an update with LU", type="primary",
                      on_click=navigate_to, args=("LU Chat",))
        with cta2:
            st.button("New member? Start onboarding", on_click=navigate_to,
                      args=("LU Chat", "I'm new here"))

    with followups_tab:
        st.markdown('<div class="section-title">Coordination follow-ups</div>', unsafe_allow_html=True)
        st.caption("Built deterministically from the project file and tracking log. Follow-ups are prompts for the team, not automatic decisions.")
        today = datetime.date.today()
        dated = [(item, _parse_date(item["deadline"])) for item in deadline_items]
        dated = [(item, date) for item, date in dated if date]
        overdue = sorted([(item, date) for item, date in dated if date < today], key=lambda item: item[1])
        soon = sorted([(item, date) for item, date in dated if today <= date <= today + datetime.timedelta(days=7)], key=lambda item: item[1])
        later = sorted([(item, date) for item, date in dated if date > today + datetime.timedelta(days=7)], key=lambda item: item[1])
        st.markdown("#### Needs a check-in · overdue or due within 7 days")
        if not overdue and not soon:
            st.info("No overdue or near-term dates in the tracking log.")
        for item, _ in overdue + soon:
            st.markdown(f"- {chase_reminder(item, today)}")
        st.markdown("#### Later dates")
        if not later:
            st.caption("No later dated items.")
        for item, _ in later:
            st.markdown(f"- {chase_reminder(item, today)}")

        st.markdown("#### Member check-ins")
        silent = [name for name in names if name in activity and (today - activity[name]).days >= 14]
        no_record = [name for name in names if name not in activity]
        if not silent and not no_record:
            st.caption("No members currently meet the 14-day check-in rule.")
        for name in silent:
            st.markdown(f"- {name} · last tracking entry {activity[name].isoformat()} ({(today - activity[name]).days} days ago).")
        for name in no_record:
            st.markdown(f"- {name} · no tracking entry recorded.")
        st.markdown("#### Waiting for activation")
        if not activation_items:
            st.caption("No activation items in the tracking log.")
        for item in activation_items:
            st.markdown(f"- {item['line']}")
        st.markdown("#### Other follow-ups")
        if not other_items:
            st.caption("No other follow-ups in the tracking log.")
        for item in other_items:
            st.markdown(f"- {item['member'] + ': ' if item['member'] else ''}{item['line']}")

# ---------- Projects ----------
elif nav == "Projects":
    projects = parse_projects()
    project_updates = parse_recent_updates(limit=20)
    st.markdown('<div class="eyebrow">Shared work, clearly held</div><h1 class="section-title">Projects</h1>', unsafe_allow_html=True)
    st.caption("A simple view of current work, sourced from SST. Update progress by telling LU in chat; project owners keep decision-making.")
    if not projects:
        st.info("No projects are listed in the SST yet.")
    for start in range(0, len(projects), 2):
        columns = st.columns(2, gap="large")
        for column, project in zip(columns, projects[start:start + 2]):
            with column:
                status = project.get("status") or "Status not recorded"
                tone = ("status-blocked" if re.search(r"blocked|阻塞|卡住", status, re.I)
                        else "status-active" if re.search(r"active|持续|进行|排版中", status, re.I)
                        else "status-progress")
                team = project.get("team") or "Contributors not specified in SST"
                schedule = project.get("schedule") or project.get("next_deadline") or "No schedule recorded"
                step = project.get("next_step") or "Next step not recorded"
                blocker = project.get("blocker") or ("Reported in status" if re.search(r"blocked|阻塞|卡住", status, re.I) else "No blocker recorded")
                project_key = _norm_name(project["name"])
                latest = next((
                    update for update in project_updates
                    if project_key and (
                        project_key in _norm_name(update["text"])
                        or _norm_name(update["text"]) in project_key
                    )
                ), None)
                latest_text = (f"{latest['heading']} · {latest['text']}"
                               if latest else "No project-specific update recorded")
                st.markdown(
                    f'<div class="surface" style="min-height:255px;margin:.45rem 0">'
                    f'<div class="eyebrow">TEAM PROJECT</div>'
                    f'<div class="project-title" style="font-size:1.6rem;margin:.5rem 0">{html.escape(project["name"])}</div>'
                    f'<span class="status {tone}">{html.escape(status)}</span>'
                    f'<div class="small-muted" style="margin-top:1rem"><b>Description</b> · Not recorded in SST</div>'
                    f'<div class="small-muted" style="margin-top:.5rem"><b>Owner</b> · Not specified in SST</div>'
                    f'<div class="small-muted" style="margin-top:1rem"><b>Contributors</b> · {html.escape(team)}</div>'
                    f'<div class="small-muted" style="margin-top:.5rem"><b>Schedule</b> · {html.escape(schedule)}</div>'
                    f'<div class="small-muted" style="margin-top:.5rem"><b>Next</b> · {html.escape(step)}</div>'
                    f'<div class="small-muted" style="margin-top:.5rem"><b>Blocker</b> · {html.escape(blocker)}</div>'
                    f'<div class="small-muted" style="margin-top:.5rem"><b>Latest update</b> · {html.escape(latest_text)}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    if projects:
        st.info("The SST currently stores project descriptions mainly as status, team, and schedule notes. No progress percentages or unrecorded owners are inferred.")
    st.button("Update a project in LU Chat", type="primary",
              on_click=navigate_to, args=("LU Chat",))

# ---------- SST Brain ----------
else:
    st.markdown('<div class="eyebrow">Single Source of Truth</div><h1 class="section-title">SST Brain</h1>', unsafe_allow_html=True)
    st.caption("The team's shared memory: updates, project context, protocols, profiles, and tracking.")
    working_tab, snapshot_tab = st.tabs(["Working Brain", "Team Snapshot"])
    brain = read_brain()
    with working_tab:
        query = st.text_input("Search the shared brain", placeholder="Search projects, people, decisions, protocols…")
        selected = [name for name in BRAIN_FILES if not query or query.casefold() in brain[name].casefold()]
        if query and not selected:
            st.info("No matching SST documents.")
        for name in selected:
            contents = brain[name]
            with st.expander(name, expanded=bool(query)):
                st.markdown(contents)
        st.caption("Source documents remain the editable SST files; this view doesn't create a second copy.")

    with snapshot_tab:
        projects = parse_projects()
        members = parse_members()
        updates = parse_recent_updates(limit=6)
        deadline_items, _, _, _ = parse_tracking()
        blockers = [
            f"{project['name']} — {project.get('blocker') or project.get('status')}"
            for project in projects
            if project.get("blocker") or re.search(r"blocked|阻塞|卡住", project.get("status", ""), re.I)
        ]
        st.markdown('<div class="poster"><div class="eyebrow" style="color:#AEB9F2">LADY UP · TEAM REPORT</div>'
                    '<h2>One team, in motion.</h2><div class="mono" style="font-size:.72rem;color:#BFC0CA">'
                    f'{datetime.date.today().isoformat()} · GENERATED FROM SST BRAIN</div></div>',
                    unsafe_allow_html=True)
        pcol, mcol = st.columns(2, gap="large")
        with pcol:
            st.markdown("### Current priorities")
            if projects:
                for project in projects:
                    detail = project.get("next_step") or project.get("status") or "No update recorded"
                    st.markdown(f"- **{project['name']}** · {detail}")
            else:
                st.caption("No project priorities recorded.")
        with mcol:
            st.markdown("### Responsibilities")
            if members:
                for member in members:
                    role_or_task = member.get("current_task") or member.get("role") or "No responsibility recorded"
                    st.markdown(f"- **{member['name']}** · {role_or_task}")
            else:
                st.caption("No member profiles recorded.")
        accomplishments, blockers_col = st.columns(2, gap="large")
        with accomplishments:
            st.markdown("### Recent team updates")
            st.caption("Entries are shown as recorded; completion is not inferred.")
            if updates:
                for update in updates:
                    st.markdown(f"- {update['heading']} · {update['text']}")
            else:
                st.caption("No recent entries in the tracking log.")
        with blockers_col:
            st.markdown("### Blockers & next steps")
            if blockers:
                for blocker in blockers:
                    st.markdown(f"- {blocker}")
            else:
                st.caption("No blockers are reported in the SST.")
            if deadline_items:
                st.markdown("**Tracked next steps**")
                for item in deadline_items[:5]:
                    project_label = f" · {item['project']}" if item.get("project") else ""
                    st.markdown(f"- {item['deadline']} · {item['step']}{project_label}")
            else:
                st.caption("No dated next steps are in the tracking log.")
        st.divider()
        st.caption("Share this report by downloading the poster. Public or permission-controlled links aren't configured in the current app.")
        pdf_data = make_snapshot_pdf(projects, members, updates, deadline_items, blockers)
        st.download_button(
            "Download Team Snapshot · PDF",
            data=pdf_data,
            file_name=f"lu-team-snapshot-{datetime.date.today().isoformat()}.pdf",
            mime="application/pdf",
            type="primary",
        )
