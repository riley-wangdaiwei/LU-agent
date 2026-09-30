"""
LU Brain — Lady Up team AI ops assistant (MVP)

Run: streamlit run app.py
Requires env var: GEMINI_API_KEY (see .env.example)
"""
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
2. Answer questions about team protocols, workflows, and project schedules.
3. Match members with tasks: recommend from active projects and workflows based
   on their interests, availability, and resources.
4. When a member reports progress, acknowledge it and give brief feedback.
5. SCHEDULE BREAKDOWN: when a member asks about a deadline or their next steps,
   work backwards from the big deadline using the workflow in the brain
   (e.g. the 5-week interview cycle: contact -> schedule -> draft -> publish).
   Give them their NEXT 3 concrete steps with suggested dates, small enough
   to act on this week.
6. WEEKLY CHASE LIST (for execs): members awaiting activation / overdue tasks /
   no activity for two weeks / items needing the team lead or exec to decide.
   Each item gets one short, copy-paste-ready reminder line.

You never make decisions for people: task assignment and schedule changes are
suggestions only — tell the user to confirm with an exec or the team lead.
"""

def chat_once(client, history: list, user_msg: str, identity_note: str = "") -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
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
h1, h2 {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif !important;
          color: {GREEN}; letter-spacing: 0.01em; }}
h3 {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif !important; color: #000; }}
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
    st.markdown("## LU Brain")
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
    st.markdown("## Chat")
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
                st.success("Saved to 04_members.md")

    if prompt := st.chat_input("Message LU Brain..."):
        send_and_reply(prompt)
        st.rerun()

# ---------- Brain ----------
elif nav == "Brain":
    st.markdown("## SST Brain")
    st.caption("The team's shared knowledge base.")
    brain = read_brain()
    tabs = st.tabs(list(BRAIN_FILES.keys()))
    for tab, name in zip(tabs, BRAIN_FILES.keys()):
        with tab:
            st.markdown(f'<div class="brain-card">{brain[name]}</div>', unsafe_allow_html=True)

# ---------- Chase list ----------
else:
    st.markdown("## Chase List")
    st.caption("For execs: generated by the agent, you only forward the lines.")
    client = get_client()
    if client is None:
        st.warning("GEMINI_API_KEY is not set.")
        st.stop()
    if st.button("Generate this week's chase list", type="primary"):
        with st.spinner("Reading the brain..."):
            prompt = ("Generate this week's chase list from the SST Brain, in four sections: "
                      "1) members awaiting activation "
                      "2) overdue tasks "
                      "3) no activity for two weeks "
                      "4) items needing the team lead or exec to decide. "
                      "Each item gets one short reminder line the exec can forward as-is. "
                      "If information is missing, say so plainly instead of inventing it.")
            try:
                st.markdown(chat_once(client, [], prompt))
            except Exception as e:
                st.error(f"Generation failed: {e}")
