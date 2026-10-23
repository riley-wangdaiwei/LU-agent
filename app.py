"""
LU Brain — Lady Up team AI ops assistant (MVP)

Run: streamlit run app.py
Requires env var: GEMINI_API_KEY (see .env.example)
"""
import datetime
import hashlib
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

PINK = "#EB5D73"
GREEN = "#02C64F"

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
    heads = list(re.finditer(r"^## (.+)$", cur, re.M))
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
    heads = list(re.finditer(r"^## (.+)$", text, re.M))
    out = []
    for i, m in enumerate(heads):
        body = text[m.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]

        def field(key):
            mm = re.search(rf"^- {re.escape(key)}：(.*)$", body, re.M)
            return mm.group(1).strip() if mm else ""
        out.append({"name": m.group(1).strip(), "status": field("状态"),
                    "next_step": field("下一步"), "next_deadline": field("下次截止"),
                    "completed": field("上一步已完成")})
    return out

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
   After the profile is confirmed and saved, do POST-ONBOARDING ACTIVATION
   (one thing at a time, conversationally): (a) briefly introduce the three
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

# ---------- Page ----------
st.set_page_config(page_title="LU Brain", page_icon=None, layout="wide")

st.markdown(f"""
<style>
html, body, [class*="css"] {{
    font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
    background: #ffffff; color: #000000;
}}
h1, h2, h3 {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif !important;
          color: #000; letter-spacing: 0.01em; }}
.page-title {{ color: {GREEN} !important; }}
.block-container {{ max-width: 820px; padding-top: 2rem; }}
a {{ color: {PINK} !important; }}
hr {{ border: none; border-top: 1px solid #000; opacity: 0.15; }}
.stButton > button {{
    border-radius: 999px; border: 1px solid #000;
    background: #fff; color: #000; font-size: 0.85rem;
}}
.stButton > button:hover {{ border-color: {PINK}; color: {PINK}; }}
.stButton > button[kind="primary"] {{ background: {PINK}; border-color: {PINK}; color: #fff; }}
.stButton > button[kind="primary"]:hover {{ background: #d14a60; color: #fff; }}
.brain-card {{
    background: #fff; border: 1px solid #000; border-radius: 4px;
    padding: 1.2rem 1.4rem;
}}
.accent-pink {{ color: {PINK}; font-weight: 600; }}
.accent-green {{ color: {GREEN}; font-weight: 600; }}
.stChatMessage {{ background: #fff; }}
section[data-testid="stSidebar"] {{ background: #fff; border-right: 1px solid #000; }}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    logo = ASSETS / "lu_new_1.png"
    if logo.exists():
        st.image(str(logo), width=72)
    st.markdown('<h2 class="page-title">LU Brain</h2>', unsafe_allow_html=True)
    st.caption("Lady Up team ops assistant")
    nav = st.radio("Navigate", ["Chat", "Brain", "Chase List"], label_visibility="collapsed")
    st.divider()
    names = get_member_names()
    st.selectbox(
        "Chatting as",
        ["Not identified"] + names + ["I'm new here"],
        key="identity",
        help="Pick your name so the agent knows who you are.",
    )
    if st.session_state.get("identity") not in (None, "Not identified", "I'm new here"):
        st.caption(f"Chatting as {st.session_state.identity}")
    st.divider()
    ok = (BRAIN_DIR / "00_overview.md").exists()
    st.caption("Brain loaded" if ok else "Brain files missing")

# ---------- Chat ----------
if nav == "Chat":
    st.markdown('<h2 class="page-title">Chat</h2>', unsafe_allow_html=True)
    st.caption("Talk to LU Brain: onboarding, tasks, deadlines, progress updates. "
               "English or 中文都可以。")

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
                p = BRAIN_DIR / "04_members.md"
                cur = p.read_text(encoding="utf-8") if p.exists() else ""
                lines = ["\n### " + prof.get("name", "New member")]
                for k in ["interests", "availability", "resources", "current_task", "status"]:
                    if prof.get(k):
                        lines.append(f"- {k}: {prof[k]}")
                p.write_text(cur.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
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

# ---------- Brain ----------
elif nav == "Brain":
    st.markdown('<h2 class="page-title">SST Brain</h2>', unsafe_allow_html=True)
    st.caption("The team's shared knowledge base.")
    brain = read_brain()
    tabs = st.tabs(list(BRAIN_FILES.keys()))
    for tab, name in zip(tabs, BRAIN_FILES.keys()):
        with tab:
            st.markdown(f'<div class="brain-card">{brain[name]}</div>', unsafe_allow_html=True)

# ---------- Chase list ----------
# Deterministic: built from 03 + 05 by code, not guessed by the LLM.
# That was the bug — the old page asked the model to read the brain and
# hope it noticed everything, so items went missing. Every section below
# comes from a parse, with explicit rules ("快到了" = overdue or ≤ 7 days).
else:
    st.markdown('<h2 class="page-title">Chase List</h2>', unsafe_allow_html=True)
    st.caption("For execs: built from the project file + tracking log, you only forward the lines. "
               "No API key needed for this page.")
    today = datetime.date.today()
    projects = parse_projects()
    items, activation, other, activity = parse_tracking()

    st.markdown("### 所有项目当前状态")
    if not projects:
        st.info("项目文件里还没有项目。成员汇报进度后会自动出现在这里。")
    for pr in projects:
        st.markdown(f"- **{pr['name']}** — 状态：{pr['status'] or '未知'}；"
                    f"上一步已完成：{pr['completed'] or '—'}；"
                    f"下一步：{pr['next_step'] or '未填写'}；"
                    f"下次截止：{pr['next_deadline'] or '暂无'}")

    dated = [(it, _parse_date(it["deadline"])) for it in items]
    dated = [(it, d) for it, d in dated if d]
    overdue = sorted([(it, d) for it, d in dated if d < today], key=lambda x: x[1])
    soon = sorted([(it, d) for it, d in dated
                   if today <= d <= today + datetime.timedelta(days=7)], key=lambda x: x[1])
    rest = sorted([(it, d) for it, d in dated
                   if d > today + datetime.timedelta(days=7)], key=lambda x: x[1])

    st.markdown("### 待催：日期快到了（逾期 + 7 天内）")
    if not overdue and not soon:
        st.info("没有逾期或 7 天内到期的事项。")
    for it, d in overdue + soon:
        st.markdown(f"- {chase_reminder(it, today)}")

    st.markdown("### 其他待催日期（按时间排序）")
    if not rest:
        st.info("没有其他未完成的日期事项。")
    for it, d in rest:
        st.markdown(f"- {chase_reminder(it, today)}")

    st.markdown("### 两周无动静")
    names = get_member_names()
    if not names:
        st.info("成员档案里还没有成员。")
    else:
        silent = [n for n in names
                  if n in activity and (today - activity[n]).days >= 14]
        no_record = [n for n in names if n not in activity]
        if not silent and not no_record:
            st.info("没有静默成员。")
        for n in silent:
            st.markdown(f"- {n}（上次活跃 {activity[n].isoformat()}，"
                        f"{(today - activity[n]).days} 天前）：问问现在能做什么。")
        for n in no_record:
            st.markdown(f"- {n}（追踪日志里还没有任何记录）。")

    st.markdown("### 待激活")
    if not activation:
        st.info("没有等着进群的成员。")
    for act in activation:
        st.markdown(f"- {act['line']}")

    st.markdown("### 其他待跟进（非日期事项）")
    if not other:
        st.info("没有其他待跟进事项。")
    for o in other:
        who = f"{o['member']}：" if o["member"] else ""
        st.markdown(f"- {who}{o['line']}")
