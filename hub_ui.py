"""Single-page, kettle-led conversation flow for the LU team hub."""
import base64
import datetime
import hashlib
import html
import io
import json
import re
from pathlib import Path

import streamlit as st


def _extract(text: str, kind: str):
    match = re.search(rf"```{re.escape(kind)}\s*(\{{.*?\}})\s*```", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _clean_response(text: str) -> str:
    return re.sub(r"```(?:profile|progress|deadlines|activation)\s*\{.*?\}\s*```",
                  "", text, flags=re.S).strip()


def _member_role(profile: str) -> str:
    match = re.search(r"^- role:\s*(.+)$", profile, re.M | re.I)
    return match.group(1).strip() if match else ""


def _is_executive(role: str) -> bool:
    normalized = re.sub(r"\s+", " ", role.strip().casefold())
    return normalized in {
        "exec", "executive", "team executive", "team lead", "team leader",
        "团队负责人", "执行团队", "高管",
    }


def _poster_png(projects: list, members: list, updates: list,
                deadlines: list, blockers: list) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1600, 1100
    image = Image.new("RGB", (width, height), "#090b11")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("DejaVuSans.ttf", 52)
        section_font = ImageFont.truetype("DejaVuSans.ttf", 20)
        body_font = ImageFont.truetype("DejaVuSans.ttf", 17)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        title_font = section_font = body_font = small_font = ImageFont.load_default()

    draw.ellipse((1110, -340, 1780, 340), fill="#0b2418")
    draw.text((78, 64), "LU  /  TEAM SNAPSHOT", font=small_font, fill="#8ad8aa")
    draw.text((78, 104), "One team, in motion.", font=title_font, fill="#f3f4fa")
    draw.text((80, 182), datetime.date.today().isoformat()
              + "   ·   Generated from the SST Brain",
              font=small_font, fill="#969bad")

    columns = [
        ("PROJECT PULSE", [
            f"{item['name']}  ·  {item.get('status') or 'Status not recorded'}"
            for item in projects
        ]),
        ("PEOPLE & RESPONSIBILITIES", [
            f"{item['name']}  ·  {item.get('current_task') or item.get('role') or 'No responsibility recorded'}"
            for item in members
        ]),
        ("RECENT TEAM UPDATES", [
            f"{item['heading']}  ·  {item['text']}" for item in updates
        ]),
        ("NEXT & BLOCKERS", [
            *[f"{item['deadline']}  ·  {item['step']}" for item in deadlines],
            *[f"Blocker  ·  {item}" for item in blockers],
        ]),
    ]
    left, right = 78, 820
    top1, top2, card_w, card_h = 244, 675, 700, 390
    for index, (title, rows) in enumerate(columns):
        x = left if index % 2 == 0 else right
        y = top1 if index < 2 else top2
        draw.text((x + 28, y + 26), title, font=section_font, fill="#8ad8aa")
        for row_index, row in enumerate(rows[:9]):
            line = str(row).replace("\n", " ")
            if len(line) > 76:
                line = line[:73] + "..."
            draw.text((x + 28, y + 75 + row_index * 31), line,
                      font=body_font, fill="#e5e7ee")
        if not rows:
            draw.text((x + 28, y + 78), "No matching information is recorded.",
                      font=body_font, fill="#969bad")
    draw.text((78, 1080), "People update. LU coordinates. SST remembers.",
              font=small_font, fill="#969bad")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _render_next_steps(pending, identity, base, github_push_file, apply_progress_update, reply_hash):
    """Two AI-assigned steps with 3/7/14-day deadline choices + confirm.

    Step 1 becomes the project's next step + deadline in 03; both steps
    become unchecked chase items in 05. Nothing writes before confirm.
    """
    items = [it for it in (pending.get("items") or []) if (it or {}).get("step")][:2]
    if not items:
        return
    member = pending.get("member") or identity
    project = pending.get("project") or ""
    today = datetime.date.today()
    if st.session_state.get("confirmed_nextsteps_key") == reply_hash:
        st.success("Assigned steps confirmed and on the chase list.")
        return
    st.markdown('<div class="flow-label">SUGGESTED NEXT STEPS · FROM YOUR UPDATE</div>',
                unsafe_allow_html=True)
    windows = [("3 days", today + datetime.timedelta(days=3)),
               ("7 days", today + datetime.timedelta(days=7)),
               ("14 days", today + datetime.timedelta(days=14))]
    for i, item in enumerate(items):
        st.markdown(f"**Step {i + 1}** · {html.escape(item.get('step', ''))}")
        if item.get("why"):
            st.caption(item["why"])
        suggested = {"3d": 0, "7d": 1, "14d": 2}.get(item.get("suggested"), 1)
        options = [f"{label} · {d.isoformat()}" for label, d in windows]
        st.radio(f"Deadline for step {i + 1}", options, index=suggested,
                 key=f"ns_deadline_{i}", label_visibility="collapsed")
    if st.button("CONFIRM ASSIGNED STEPS", key="confirm_nextsteps", type="primary"):
        dates = []
        for i, item in enumerate(items):
            label = st.session_state.get(f"ns_deadline_{i}", "")
            m = re.search(r"(\d{4}-\d{2}-\d{2})", label or "")
            dates.append(m.group(1) if m else (today + datetime.timedelta(days=7)).isoformat())
        synced = True
        tracking_added = 0
        if project:
            # Write 03 + the step-1 chase line locally, but push nothing yet:
            # 05 is written and pushed exactly once for the whole confirm
            # (a second push 2s later once overwrote the first one's entry).
            result = apply_progress_update({"member": member, "project": project,
                                            "next_step": items[0].get("step"),
                                            "next_deadline": dates[0]}, push=False)
            if result.get("ambiguous"):
                names = "、".join(result.get("candidates", []))
                st.warning(f"这个名字对上了 {len(result['candidates'])} 个项目：{names}。请在对话里告诉 LU 是哪一个，确认后再点一次。")
                synced = False
            else:
                tracking_added = result.get("tracking_added", 0)
                ok03, _ = github_push_file("sst_brain/03_active_projects.md")
                synced = bool(ok03)
        tpath = base / "sst_brain" / "05_tracking.md"
        tcur = tpath.read_text(encoding="utf-8") if tpath.exists() else "# 追踪日志\n"
        start = 1 if project else 0
        lines = []
        for i in range(start, len(items)):
            line = f"- [ ] {dates[i]} — {items[i].get('step')}" + (f" (project: {project})" if project else "")
            if line not in tcur:
                lines.append(line)
        if lines:
            block = f"\n## {today.isoformat()} — {member} confirmed next steps\n" + "\n".join(lines) + "\n"
            tpath.write_text(tcur.rstrip() + "\n" + block, encoding="utf-8")
        if tracking_added or lines:
            ok, _ = github_push_file("sst_brain/05_tracking.md")
            synced = synced and ok
        st.session_state.confirmed_nextsteps_key = reply_hash
        st.success("Assigned steps confirmed and on the chase list.")
        if not synced:
            st.caption("Saved locally. GitHub sync skipped — the brain file is updated on the next sync.")
        st.rerun()


def _current_status(projects, name):
    """A project's current status, matched by heading or anchor.

    Returns "" unless exactly one project matches — display-only, so an
    ambiguous name shows no old value rather than a wrong one.
    """
    q = re.sub(r"[^\w\u4e00-\u9fff]", "", name or "").lower()
    hits = []
    for p in projects:
        names = [p["name"]] + [a.strip() for a in re.split(r"[、，,]", p.get("anchor", "")) if a.strip()]
        for n in names:
            nn = re.sub(r"[^\w\u4e00-\u9fff]", "", n).lower()
            if q and nn and (nn in q or q in nn):
                hits.append(p)
                break
    return hits[0].get("status", "") if len(hits) == 1 else ""


def render_hub(*, base: Path, get_client, chat_once,
               identity_note, get_member_names, get_member_block, read_brain,
               parse_projects, parse_members, parse_tracking,
               parse_recent_updates, make_snapshot_pdf,
               apply_progress_update, save_member_profile, github_push_file):
    st.set_page_config(
        page_title="LU · One kettle. One conversation.",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    asset_dir = base / "assets"
    kettle_path = asset_dir / "kettle_cloth_cutout.png"
    kettle_tag = ""
    if kettle_path.exists():
        kettle_data = base64.b64encode(kettle_path.read_bytes()).decode("ascii")
        kettle_mime = "image/jpeg" if kettle_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        kettle_tag = (
            f'<img class="floating-kettle" src="data:{kettle_mime};base64,'
            + kettle_data + '" alt="LU kettle with its striped cloth">'
        )

    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Instrument+Serif:ital@0;1&family=Manrope:wght@300;400;500;600;700&display=swap');
:root {{
  color-scheme: dark; --void:#050609; --glass:rgba(18,21,30,.42);
  --glass-strong:rgba(13,16,24,.62); --edge:transparent;
  --text:#F4F4F7; --muted:#A3A6B4; --green:#21B86B; --green-light:#A4E8C2;
}}
html {{ background:var(--void); }}
body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
  background:radial-gradient(ellipse at 50% 25%,#111410 0%,var(--void) 56%) !important;
  color:var(--text); font-family:'Manrope',Arial,sans-serif;overflow-x:hidden;
}}
[data-testid="stHeader"], [data-testid="stToolbar"], footer,
section[data-testid="stSidebar"] {{ display:none !important; }}
.block-container {{ max-width:1100px; padding:3rem 2rem 12vh; position:relative; z-index:2; }}
[data-testid="stMain"] {{ scroll-behavior:smooth;scroll-snap-type:y proximity; }}
[data-testid="stMainBlockContainer"] {{ position:relative; z-index:2; }}
.flow-topline {{ position:fixed;top:24px;left:34px;right:34px;z-index:4;
  display:flex;justify-content:space-between;align-items:center;pointer-events:none; }}
.flow-brand,.flow-count {{ font:11px 'DM Mono',monospace;letter-spacing:.16em;
  color:#A4E8C2;text-transform:uppercase; }}
.flow-brand span {{ color:#78827c; }}
.floating-kettle {{ position:fixed;z-index:0;pointer-events:none;
  width:min(68vw,78vh);height:min(68vw,78vh);max-width:none;object-fit:contain;
  left:50%;top:48%;transform:translate(-50%,-50%);
  mix-blend-mode:normal;opacity:.95;filter:drop-shadow(0 0 50px rgba(25,185,107,.16));
  animation:kettle-drift 9s ease-in-out infinite; }}
@keyframes kettle-drift {{ 0%,100% {{ margin-top:0; }} 50% {{ margin-top:-12px; }} }}
.flow-section {{ min-height:86vh; padding:10vh 0 6vh; scroll-snap-align:start;
  display:flex;flex-direction:column;justify-content:center;position:relative; }}
.flow-section:before {{ content:"";position:absolute;inset:10% -18vw;z-index:-1;
  background:radial-gradient(ellipse at center,rgba(8,10,16,.22),transparent 68%);pointer-events:none; }}
.flow-kicker {{ font:10px 'DM Mono',monospace;color:var(--green-light);
  letter-spacing:.2em;text-transform:uppercase;margin-bottom:16px; }}
.flow-headline {{ font-family:'Manrope',Arial,sans-serif !important;
  font-size:clamp(2.4rem,5vw,5rem) !important;font-weight:300 !important;
  line-height:.98 !important;letter-spacing:-.065em !important;
  color:#F4F4F7 !important;margin:0 0 20px !important;
  text-shadow:0 2px 18px rgba(0,0,0,.9); }}
.flow-headline em {{ font-family:'Instrument Serif',Georgia,serif;font-weight:400;letter-spacing:-.035em; }}
.flow-copy {{ font-size:15px;line-height:1.75;color:#c0c2cc;max-width:510px;
  font-family:'Manrope',Arial,sans-serif;text-shadow:0 2px 18px rgba(0,0,0,.95);
  background:linear-gradient(145deg,rgba(30,34,48,.72),rgba(12,14,21,.78));
  border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:16px 18px;
  box-shadow:0 24px 90px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.08);
  backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px); }}
.flow-label {{ font:10px 'DM Mono',monospace;letter-spacing:.16em;text-transform:uppercase;
  color:#929a95;text-shadow:0 2px 12px rgba(0,0,0,.9); }}
.flow-glass {{ background:linear-gradient(145deg,rgba(30,34,48,.72),rgba(12,14,21,.78));
  border:1px solid var(--edge);border-radius:24px;padding:clamp(22px,4vw,42px);
  box-shadow:0 24px 90px rgba(0,0,0,.35),inset 0 1px 0 rgba(255,255,255,.08);
  backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);position:relative; }}
.flow-glass:after {{ content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;
  background:linear-gradient(120deg,rgba(255,255,255,.045),transparent 35%); }}
.st-key-flow-who,.st-key-flow-talk,.st-key-flow-review {{ min-height:82vh;padding:8vh 0 6vh;
  scroll-snap-align:start;display:flex;flex-direction:column;justify-content:center;
  max-width:430px;width:42vw; }}
.st-key-flow-who {{ padding-top:14vh; }}
.st-key-flow-who,.st-key-flow-review {{ margin-left:1%; }}
.st-key-flow-talk {{ margin-left:auto;margin-right:1%; }}
.st-key-flow-who,.st-key-flow-talk,.st-key-flow-review {{
  background:linear-gradient(90deg,rgba(5,6,9,.42) 0%,rgba(5,6,9,.2) 54%,transparent 100%);
  border:0;border-radius:0;padding:clamp(22px,3vw,34px);box-shadow:none;
  backdrop-filter:none;-webkit-backdrop-filter:none;position:relative;
}}
.st-key-flow-talk {{
  background:linear-gradient(270deg,rgba(5,6,9,.42) 0%,rgba(5,6,9,.2) 54%,transparent 100%);
}}
.st-key-flow-remember {{ max-width:none;width:100%;margin-left:0;margin-right:0; }}
.stTextInput label,.stSelectbox label,.stTextArea label {{ color:#c8ceca !important;font-family:'Manrope',Arial,sans-serif !important; }}
.stTextInput input,.stTextArea textarea,[data-baseweb="select"] > div {{
  background:transparent !important;color:#f6f6fa !important;
  border:0 !important;border-radius:0 !important;box-shadow:none !important;
  font-family:'Manrope',Arial,sans-serif !important; }}
.stTextInput [data-baseweb="base-input"],.stTextArea [data-baseweb="base-input"],
.stSelectbox [data-baseweb="select"],.stSelectbox [data-baseweb="select"] > div,
.stSelectbox [role="group"],.stSelectbox input,.stSelectbox button {{
  background:transparent !important;border:0 !important;box-shadow:none !important;
  outline:0 !important;border-radius:0 !important; }}
.stSelectbox [data-baseweb="select"] *,.stSelectbox [role="group"] * {{
  background:transparent !important;border:0 !important;box-shadow:none !important;
  outline:0 !important;border-radius:0 !important; }}
.stSelectbox [role="group"] {{
  background:transparent !important;border:0 !important;box-shadow:none !important;
  outline:0 !important;border-radius:0 !important; }}
.stTextInput input:focus,.stTextArea textarea:focus,
.stSelectbox [data-baseweb="select"]:focus-within {{
  outline:0 !important;box-shadow:none !important;border:0 !important; }}
.stTextInput input::placeholder,.stTextArea textarea::placeholder {{ color:#858c87 !important; }}
.stTextArea textarea {{ line-height:1.6; }}
.stButton > button,.stFormSubmitButton > button,.stDownloadButton > button {{
  border:1px solid rgba(255,255,255,.16);border-radius:14px;
  background:linear-gradient(145deg,rgba(38,44,62,.88),rgba(16,19,28,.92));
  backdrop-filter:blur(26px);-webkit-backdrop-filter:blur(26px);
  box-shadow:0 18px 50px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.12);
  color:var(--green-light);
  font:500 11px 'DM Mono',monospace;letter-spacing:.1em;
  padding:.85rem 1.4rem;transition:all .22s ease; }}
.stButton > button:hover,.stFormSubmitButton > button:hover,.stDownloadButton > button:hover {{
  background:linear-gradient(145deg,rgba(48,55,76,.92),rgba(22,26,38,.95));
  color:#fff;box-shadow:0 18px 50px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.16);
  transform:translateX(5px); }}
/* Rithub arrow (Feather "arrow-up-right": diagonal shaft + right-angle
   corner) after every button label. Inlined as a mask so it takes the
   button text colour, including the white hover state. */
.stButton > button::after,.stFormSubmitButton > button::after,
.stDownloadButton > button::after,.flow-action-link::after {{
  content:"";display:inline-block;width:1em;height:1em;margin-left:.45em;
  vertical-align:-.12em;background-color:currentColor;
  -webkit-mask-image:url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2024%2024'%20fill='none'%20stroke='black'%20stroke-width='2'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Cline%20x1='7'%20y1='17'%20x2='17'%20y2='7'/%3E%3Cpolyline%20points='7%207%2017%207%2017%2017'/%3E%3C/svg%3E");
  -webkit-mask-size:contain;-webkit-mask-repeat:no-repeat;-webkit-mask-position:center;
  mask-image:url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2024%2024'%20fill='none'%20stroke='black'%20stroke-width='2'%20stroke-linecap='round'%20stroke-linejoin='round'%3E%3Cline%20x1='7'%20y1='17'%20x2='17'%20y2='7'/%3E%3Cpolyline%20points='7%207%2017%207%2017%2017'/%3E%3C/svg%3E");
  mask-size:contain;mask-repeat:no-repeat;mask-position:center; }}
.flow-action-link {{ display:inline-flex;align-items:center;margin-top:24px;
  padding:.85rem 1.4rem;border:1px solid rgba(255,255,255,.16);border-radius:14px;
  background:linear-gradient(145deg,rgba(38,44,62,.88),rgba(16,19,28,.92));
  backdrop-filter:blur(26px);-webkit-backdrop-filter:blur(26px);
  box-shadow:0 18px 50px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.12);
  color:var(--green-light) !important;
  text-decoration:none !important;font:500 11px 'DM Mono',monospace;letter-spacing:.1em;
  transition:all .22s ease; }}
.flow-action-link:hover {{ background:linear-gradient(145deg,rgba(48,55,76,.92),rgba(22,26,38,.95));transform:translateX(5px);color:#fff !important; }}
.stChatMessage {{ background:transparent;border:0;border-radius:0; }}
.stAlert {{ background:rgba(22,25,28,.46);color:var(--text);border:0;border-radius:0; }}
.stExpander details,.stExpander summary {{
  background:transparent !important;border:0 !important;box-shadow:none !important;
  border-radius:0 !important; }}
.stMarkdown,.stCaption {{ color:#d5d6df; }}
.stMarkdown,.stTextInput,.stTextArea,.stSelectbox,.stButton,.stDownloadButton {{
  font-family:'Manrope',Arial,sans-serif; }}
.stTabs [data-baseweb="tab-list"] {{ gap:10px;border:0 !important; }}
.stTabs [data-baseweb="tab"] {{ color:#aeb2b0;border:0 !important; }}
.stTabs [aria-selected="true"] {{ color:#A4E8C2 !important;border:0 !important; }}
.flow-rule {{ border:0;margin:24px 0; }}
.flow-pill {{ display:inline-block;padding:6px 10px;margin:3px;border-radius:99px;
  color:#A4E8C2;background:transparent;border:0;
  font:10px 'DM Mono',monospace;letter-spacing:.05em; }}
.flow-footer {{ padding:6vh 0 3vh;color:#78827c;text-align:center;
  font:10px 'DM Mono',monospace;letter-spacing:.12em; }}
.snapshot-cover {{ position:relative;overflow:hidden;padding:28px 30px;margin:18px 0 24px;
  border:0;border-radius:0;
  background:radial-gradient(ellipse at 92% 10%,rgba(25,185,107,.14),transparent 40%); }}
.snapshot-cover:after {{ content:"";position:absolute;inset:0;pointer-events:none;opacity:.14;
  background:none; }}
.snapshot-title {{ font:300 clamp(2rem,4vw,3.8rem)/1 'Manrope',Arial,sans-serif !important;
  color:#f3f4fa !important;margin:20px 0 14px !important; }}
.snap-card {{ background:linear-gradient(135deg,rgba(30,34,48,.72),rgba(12,14,21,.78));backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:18px 20px;margin-bottom:14px;box-shadow:0 18px 44px rgba(0,0,0,.34); }}
.snap-card .flow-label {{ margin-top:0; }}
.stat-num {{ font-family:'Neue Montreal',system-ui,sans-serif;font-size:44px;font-weight:300;line-height:1;color:#F4F4F7; }}
@supports (animation-timeline:view()) {{
  .st-key-flow-who,.st-key-flow-talk,.st-key-flow-review,
  .st-key-flow-remember {{
    animation:panel-through linear both;animation-timeline:view();
    animation-range:entry 0% exit 100%; }}
  .st-key-flow-talk {{ animation-name:panel-through-right; }}
}}
@keyframes panel-through {{
  0% {{ opacity:.15;transform:translate3d(-12vw,36px,0) scale(.97); }}
  22%,68% {{ opacity:1;transform:translate3d(0,0,0) scale(1); }}
  100% {{ opacity:.18;transform:translate3d(12vw,-28px,0) scale(.97); }}
}}
@keyframes panel-through-right {{
  0% {{ opacity:.15;transform:translate3d(12vw,36px,0) scale(.97); }}
  22%,68% {{ opacity:1;transform:translate3d(0,0,0) scale(1); }}
  100% {{ opacity:.18;transform:translate3d(-12vw,-28px,0) scale(.97); }}
}}
@media(max-width:720px) {{
  .block-container {{ padding:3rem 1rem 8vh; }}
  .floating-kettle {{ width:100vw;height:100vw;top:44%;opacity:.38; }}
  .st-key-flow-who,.st-key-flow-talk,.st-key-flow-review,.st-key-flow-remember {{
    min-height:88vh;padding:7vh 24px;margin-left:0;margin-right:0;width:100%;max-width:none; }}
  .st-key-flow-who,.st-key-flow-review {{
    transform:translateX(-1.5vw); }}
  .st-key-flow-talk {{ transform:translateX(1.5vw); }}
}}
@media(prefers-reduced-motion:reduce) {{
  [data-testid="stMain"] {{ scroll-behavior:auto; }}
  .floating-kettle {{ animation:none; }}
  *,*:before,*:after {{ animation-duration:.01ms !important;scroll-behavior:auto !important; }}
}}
</style>
<div class="flow-topline"><div class="flow-brand">LU <span>/ TEAM CONVERSATION</span></div>
<div class="flow-count">TALK → REVIEW → REMEMBER</div></div>
{kettle_tag}
""", unsafe_allow_html=True)

    names = get_member_names()
    identity_options = ["Not identified"] + names + ["I'm new here"]
    pending_identity = st.session_state.pop("pending_identity", "")
    if pending_identity in identity_options:
        st.session_state.identity = pending_identity
    if st.session_state.get("identity") not in identity_options:
        st.session_state.identity = "Not identified"
    if "messages" not in st.session_state:
        st.session_state.messages = []
    st.session_state.setdefault("profile_saved", False)
    st.session_state.setdefault("progress_applied", set())
    st.session_state.setdefault("deadlines_saved", False)
    st.session_state.setdefault("activation_saved", False)
    st.session_state.setdefault("confirmed_nextsteps_key", "")
    st.session_state.setdefault("brain_doc", "")
    st.session_state.setdefault("sticky_progress", None)
    st.session_state.setdefault("sticky_progress_key", "")
    st.session_state.setdefault("discarded_progress_key", "")

    identity = st.session_state.identity
    is_new = identity == "I'm new here"
    member_profile = get_member_block(identity) if identity not in (
        "Not identified", "I'm new here"
    ) else ""
    role = _member_role(member_profile)
    is_executive = _is_executive(role)
    identity_kind = "new member" if is_new else ("executive" if is_executive else "team member")
    identity_note_value = identity_note()

    def request_lu(prompt: str):
        st.session_state.messages.append({"role": "user", "text": prompt})
        client = get_client()
        if client is None:
            st.session_state.messages.pop()
            st.error("LU chat needs GEMINI_API_KEY. Add it to the app's Streamlit Secrets or local .env.")
            return False
        try:
            reply = chat_once(client, st.session_state.messages[:-1], prompt,
                              identity_note_value)
        except Exception as error:
            st.session_state.messages.pop()
            st.error(f"LU could not respond: {error}")
            return False
        st.session_state.messages.append({"role": "assistant", "text": reply})
        st.session_state.profile_saved = False
        st.session_state.deadlines_saved = False
        st.session_state.activation_saved = False
        return True

    if is_new and not st.session_state.get("onboard_auto_started"):
        st.session_state.onboard_auto_started = True
        if not st.session_state.messages:
            request_lu("Hi, I just joined the team. Please onboard me.")

    latest_reply = (
        st.session_state.messages[-1]["text"]
        if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant"
        else ""
    )
    pending_profile = _extract(latest_reply, "profile")
    pending_progress = _extract(latest_reply, "progress")
    pending_deadlines = _extract(latest_reply, "deadlines")
    pending_activation = _extract(latest_reply, "activation")
    pending_nextsteps = _extract(latest_reply, "nextsteps")
    reply_hash = hashlib.sha256(latest_reply.encode("utf-8")).hexdigest() if latest_reply else ""
    safe_profile = pending_profile or {}
    # A pending update stays put until it is confirmed or discarded: the
    # latest reply alone is too fragile — one more chat turn and it is gone.
    if pending_progress:
        _pkey = hashlib.sha256(json.dumps(pending_progress, sort_keys=True,
                                          ensure_ascii=False).encode("utf-8")).hexdigest()
        if _pkey != st.session_state.get("discarded_progress_key"):
            st.session_state.sticky_progress = pending_progress
            st.session_state.sticky_progress_key = _pkey
    effective_progress = st.session_state.get("sticky_progress")
    safe_progress = effective_progress or {}

    with st.container(key="flow-who", border=False):
        st.markdown(
            '<div class="flow-kicker">01 / WHO ARE YOU?</div>'
            '<h1 class="flow-headline">Start with<br><em>who you are.</em></h1>'
            '<div class="flow-copy">Returning members can move straight to an update. New here? LU will get to know you one small conversation at a time.</div>',
            unsafe_allow_html=True,
        )
        st.selectbox("I'm here as", identity_options, key="identity",
                     help="Identity comes from member profiles in the SST. No login or permission system is configured.")
        st.markdown('<a href="#talk" class="flow-action-link">GET STARTED</a>',
                    unsafe_allow_html=True)

    st.markdown('<div id="talk"></div>', unsafe_allow_html=True)
    with st.container(key="flow-talk", border=False):
        prompt_title = "What's happening?"
        if is_new:
            prompt_title = "Let's meet."
        elif is_executive:
            prompt_title = "How is the team moving?"
        st.markdown(
            f'<div class="flow-kicker">02 / TALK TO LU · {identity_kind.upper()}</div>'
            f'<h2 class="flow-headline">{html.escape(prompt_title)}</h2>'
            '<div class="flow-copy">Your situation, work, progress, blockers, ideas, or next steps — say it naturally. LU will organize what it hears.</div>',
            unsafe_allow_html=True,
        )
        recent_messages = st.session_state.messages[-4:]
        if recent_messages:
            for message in recent_messages:
                if message["role"] == "assistant":
                    visible = _clean_response(message["text"])
                    if visible:
                        st.markdown(f'<div class="flow-label">LU</div>', unsafe_allow_html=True)
                        st.markdown(visible)
                else:
                    st.markdown(
                        f'<div class="flow-label">YOU</div><div>{html.escape(message["text"])}</div>',
                        unsafe_allow_html=True,
                    )
        with st.form("talk_form", clear_on_submit=True):
            user_text = st.text_area(
                "What's on your mind?",
                placeholder="I'm working on… / I finished… / I'm stuck because…",
                height=106,
                label_visibility="collapsed",
            )
            talk_submitted = st.form_submit_button("TALK TO LU", type="primary")
        if talk_submitted:
            if user_text.strip():
                if request_lu(user_text.strip()):
                    st.rerun()
            st.warning("Share a sentence or two to start.")
        st.markdown(
            '<div class="flow-label" style="margin-top:16px">ONE CONVERSATION · NO LONG FORMS</div>',
            unsafe_allow_html=True,
        )

    with st.container(key="flow-review", border=False):
        st.markdown(
            f'<div class="flow-kicker">03 / REVIEW & CONFIRM · FOR THE {identity_kind.upper()}</div>'
            '<h2 class="flow-headline">Here’s what<br><em>LU understood.</em></h2>'
            '<div class="flow-copy">Review what LU understood, pick your next steps, then confirm what goes into the shared brain.</div>',
            unsafe_allow_html=True,
        )
        if is_executive:
            projects = parse_projects()
            members = parse_members()
            updates = parse_recent_updates(limit=6)
            deadlines, _, _, _ = parse_tracking()
            blockers = [
                f"{project['name']} — {project.get('blocker') or project.get('status')}"
                for project in projects
                if project.get("blocker")
                or re.search(r"blocked|阻塞|卡住", project.get("status", ""), re.I)
            ]
            st.markdown('<div class="flow-label">WEEKLY TEAM BRIEF · FROM SST</div>',
                        unsafe_allow_html=True)
            for title, lines in [
                ("Project progress", [f"{p['name']} · {p.get('status') or 'No status recorded'}" for p in projects]),
                ("Team contributions", [f"{m['name']} · {m.get('current_task') or 'No current task recorded'}" for m in members]),
                ("Latest updates", [f"{u['heading']} · {u['text']}" for u in updates]),
                ("Blockers", blockers or ["No blockers are reported in the SST."]),
                ("Priorities & tracked next steps", [
                    *[f"{p['name']} · {p['next_step']}" for p in projects if p.get("next_step")],
                    *[f"{item['deadline']} · {item['step']}" for item in deadlines],
                ] or ["No next steps are currently recorded."]),
            ]:
                st.markdown(f"**{title}**")
                for line in lines[:8]:
                    st.markdown(f"- {line}")
        elif pending_profile and not st.session_state.profile_saved:
            st.markdown('<div class="flow-label">PERSONAL INTRODUCTION</div>', unsafe_allow_html=True)
            profile_rows = [
                ("Name", safe_profile.get("name")),
                ("Role", safe_profile.get("role")),
                ("Skills & expertise", safe_profile.get("resources")),
                ("Interests", safe_profile.get("interests")),
                ("Initial project / current focus", safe_profile.get("current_task")),
                ("Availability", safe_profile.get("availability")),
                ("Status", safe_profile.get("status")),
            ]
            for label, value in profile_rows:
                if value:
                    st.markdown(f"**{label}** · {value}")
            st.caption("Review and confirm the profile in the next section. LU only saves it after confirmation.")
        elif effective_progress:
            st.markdown('<div class="flow-label">PROGRESS UPDATE · AWAITING YOUR CONFIRMATION</div>', unsafe_allow_html=True)
            progress_rows = [
                ("Project", safe_progress.get("project")),
                ("Completed", safe_progress.get("completed_step")),
                ("Current status", safe_progress.get("status")),
                ("Next step", safe_progress.get("next_step")),
                ("Next deadline", safe_progress.get("next_deadline")),
                ("Note", safe_progress.get("note")),
            ]
            for label, value in progress_rows:
                if value in (None, "", "null"):
                    continue
                if label == "Current status":
                    old = _current_status(parse_projects(), safe_progress.get("project"))
                    if old and old != value:
                        st.markdown(f"**Current status** · {old} → {value}")
                        continue
                st.markdown(f"**{label}** · {value}")
            st.caption("Confirm below to write the update to the project file and tracking log.")
        elif pending_deadlines:
            st.markdown('<div class="flow-label">PROPOSED SCHEDULE · AWAITING YOUR CONFIRMATION</div>', unsafe_allow_html=True)
            for item in pending_deadlines.get("items", []):
                st.markdown(f"- **{item.get('deadline', 'Date not set')}** · {item.get('step', '')}"
                            + (f" — {item['project']}" if item.get("project") else ""))
        elif latest_reply:
            visible_reply = _clean_response(latest_reply)
            st.markdown(visible_reply or "LU has prepared an update for review.")
            st.caption("This presentation reflects the latest conversation and SST data; unconfirmed facts are not written to the brain.")
        elif is_new:
            st.caption("As LU gets to know you, your introduction, skills, and first interests will appear here.")
        else:
            st.markdown('<div class="flow-copy">Share an update with LU to see your progress, completed work, blockers, and next steps organized here.</div>',
                        unsafe_allow_html=True)
        if pending_nextsteps:
            _render_next_steps(pending_nextsteps, identity, base, github_push_file,
                               apply_progress_update, reply_hash)
        st.markdown(
            '<div class="flow-label" style="margin-top:28px">CONFIRM · PEOPLE DECIDE</div>'
            '<div class="flow-copy">You and the project owners make decisions. LU only records information after the right person confirms it.</div>',
            unsafe_allow_html=True,
        )
        if pending_profile and not st.session_state.profile_saved:
            if st.button("CONFIRM PROFILE", key="confirm_profile", type="primary"):
                member_name = save_member_profile(safe_profile)
                st.session_state.profile_saved = True
                st.session_state.pending_identity = member_name
                ok, detail = github_push_file("sst_brain/04_members.md")
                if ok:
                    st.success("Profile confirmed and synced to main.")
                else:
                    st.success("Profile confirmed in the app.")
                    st.caption(f"GitHub sync skipped ({detail}). Configure GITHUB_TOKEN for persistent sync.")
                st.rerun()
        elif effective_progress:
            pkey = st.session_state.get("sticky_progress_key", "")
            if st.session_state.get("confirmed_progress_key") == pkey:
                st.success("Update confirmed and recorded in the SST Brain.")
            else:
                if st.button("CONFIRM UPDATE", key="confirm_progress", type="primary"):
                    result = apply_progress_update(safe_progress)
                    if result.get("ambiguous"):
                        names = "、".join(result.get("candidates", []))
                        st.warning(f"这个名字对上了 {len(result['candidates'])} 个项目：{names}。请在对话里告诉 LU 是哪一个，确认后再点一次。")
                    else:
                        st.session_state.confirmed_progress_key = pkey
                        if result["pushed"]:
                            st.success("Update confirmed and synced to the SST Brain.")
                        else:
                            st.success("Update confirmed in the app.")
                            st.caption("GitHub sync was not completed; configure GITHUB_TOKEN for persistent sync.")
                        st.rerun()
                if st.button("DISCARD UPDATE", key="discard_progress"):
                    st.session_state.discarded_progress_key = pkey
                    st.session_state.sticky_progress = None
                    st.rerun()
        elif pending_deadlines and not st.session_state.deadlines_saved:
            if st.button("CONFIRM NEXT STEPS", key="confirm_deadlines", type="primary"):
                path = base / "sst_brain" / "05_tracking.md"
                current = path.read_text(encoding="utf-8") if path.exists() else ""
                today = datetime.date.today().isoformat()
                member = pending_deadlines.get("member") or identity
                lines = [f"\n## {today} — {member} confirmed deadlines"]
                for item in pending_deadlines.get("items", []):
                    project = f" (project: {item['project']})" if item.get("project") else ""
                    lines.append(f"- [ ] {item.get('deadline')} — {item.get('step')}{project}")
                path.write_text(current.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
                st.session_state.deadlines_saved = True
                ok, detail = github_push_file("sst_brain/05_tracking.md")
                st.success("Next steps confirmed and recorded." + (" Synced to GitHub." if ok else ""))
                if not ok:
                    st.caption(f"GitHub sync skipped ({detail}).")
                st.rerun()
        elif pending_activation and not st.session_state.activation_saved:
            st.markdown(f"**Introduction** · {pending_activation.get('intro', '')}")
            if st.button("CONFIRM INTRODUCTION", key="confirm_activation", type="primary"):
                path = base / "sst_brain" / "05_tracking.md"
                current = path.read_text(encoding="utf-8") if path.exists() else ""
                today = datetime.date.today().isoformat()
                member = pending_activation.get("member") or identity
                block = [
                    f"\n## {today} — {member} ready for group activation",
                    f"- [ ] Add {member} to the main group. Intro: {pending_activation.get('intro', '')}",
                ]
                path.write_text(current.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")
                st.session_state.activation_saved = True
                ok, detail = github_push_file("sst_brain/05_tracking.md")
                st.success("Introduction confirmed and added to activation follow-ups.")
                if not ok:
                    st.caption(f"GitHub sync skipped ({detail}).")
                st.rerun()
        elif is_executive:
            st.caption("Executive-only approval is not available until the application has an authenticated role/permission system.")
            st.markdown('<a href="#remember" class="flow-action-link">GENERATE WEEKLY REPORT</a>',
                        unsafe_allow_html=True)
        elif is_new and st.session_state.profile_saved:
            st.success("Profile confirmed. LU added the member introduction to the SST.")
        elif pending_profile and st.session_state.profile_saved:
            st.success("Profile confirmed and saved to the SST Brain.")
        elif pending_deadlines and st.session_state.deadlines_saved:
            st.success("Next steps confirmed and saved to the SST Brain.")
        elif pending_activation and st.session_state.activation_saved:
            st.success("Introduction confirmed and added to activation follow-ups.")
        elif identity == "Not identified":
            st.caption("Identify yourself above to continue.")
        else:
            st.caption("When LU extracts an update or next step from your conversation, confirm it here before it is added to the shared brain.")
        with st.expander("MANUAL UPDATE · 手动记录", expanded=False):
            st.caption("聊天里没生成确认卡片时，在这里直接写，同样会同步到 Brain。")
            _manual_msg = st.session_state.get("manual_update_msg")
            if _manual_msg:
                st.success(_manual_msg)
            _mp_list = parse_projects()
            _mp_titles = [p["name"] for p in _mp_list]
            with st.form("manual_update_form", clear_on_submit=False):
                _mp = st.selectbox("项目", options=_mp_titles, key="manual_project")
                _status_options = ["已完成", "撰写中", "最后一稿出篇中", "brainstorm中", "未开始", "持续更新中", "进行中"]
                _cur = _current_status(_mp_list, _mp)
                if _cur and _cur not in _status_options:
                    _status_options = [_cur] + _status_options
                _idx = _status_options.index(_cur) if _cur else 0
                _ms = st.selectbox("新状态", options=_status_options, index=_idx, key="manual_status")
                _mc = st.text_input("上一步已完成", placeholder="例如：写完了耐力科普文", key="manual_completed")
                _mn = st.text_input("下一步", placeholder="例如：排版并发布", key="manual_next")
                _md = st.date_input("下次截止（可选）", value=None, key="manual_deadline")
                _submit = st.form_submit_button("CONFIRM MANUAL UPDATE")
            if _submit:
                _prog = {"member": identity, "project": _mp, "status": _ms,
                         "completed_step": _mc.strip() or None,
                         "next_step": _mn.strip() or None,
                         "next_deadline": _md.isoformat() if _md else None,
                         "note": None}
                _res = apply_progress_update(_prog)
                if _res.get("ambiguous"):
                    _names = "、".join(_res.get("candidates", []))
                    st.session_state.manual_update_msg = f"名字对上了多个项目：{_names}，请换一个更具体的项目名。"
                else:
                    _sync = "已同步到 main" if _res.get("pushed") else "已保存在应用内，GitHub 同步未完成"
                    st.session_state.manual_update_msg = f"已写入 Brain：{_mp} → {_ms}（{_sync}）。"
                    _sticky = st.session_state.get("sticky_progress") or {}
                    if _sticky.get("project") == _mp:
                        st.session_state.sticky_progress = None
                        st.session_state.sticky_progress_key = ""
                st.rerun()
        st.markdown('<div class="flow-label" style="margin-top:22px">PEOPLE DECIDE · LU ORGANIZES</div>',
                    unsafe_allow_html=True)

    with st.container(key="flow-remember", border=False):
        st.markdown(
            '<div id="remember"></div><div class="flow-kicker">04 / REMEMBER · SST BRAIN</div>'
            '<h2 class="flow-headline">One shared<br><em>memory.</em></h2>'
            '<div class="flow-copy">Confirmed conversations, project context, protocols, and responsibilities stay together in the team’s Single Source of Truth.</div>',
            unsafe_allow_html=True,
        )
        brain = read_brain()
        docs = list(brain)
        query = st.text_input("Search the SST Brain", placeholder="Search projects, people, protocols…")
        if query:
            matches = []
            for name in docs:
                for line in brain[name].splitlines():
                    if query.casefold() in line.casefold() and line.strip():
                        matches.append((name, line.strip()))
            if not matches:
                st.caption("No matching lines in the SST Brain.")
            for name, line in matches[:8]:
                st.markdown(f"**{name}** · {html.escape(line)}")
            if len(matches) > 8:
                st.caption(f"+ {len(matches) - 8} more matching lines.")
        else:
            st.markdown('<div class="flow-label">OPEN A BRAIN DOCUMENT</div>',
                        unsafe_allow_html=True)
            doc_cards = st.columns(3)
            for i, name in enumerate(docs):
                first = next((ln.strip().lstrip("> #") for ln in brain[name].splitlines()
                              if ln.strip() and not ln.strip().startswith("```")), "")
                with doc_cards[i % 3]:
                    if st.button(name.upper(), key=f"brain_doc_{i}"):
                        st.session_state.brain_doc = name
                    if first:
                        st.caption(first[:60] + ("…" if len(first) > 60 else ""))
            selected_doc = st.session_state.get("brain_doc") or docs[0]
            if selected_doc in brain:
                st.markdown(brain[selected_doc])

        st.markdown('<div class="flow-rule"></div><div class="flow-label">'
                    + ("EXECUTIVE WEEKLY BRIEF" if is_executive else "TEAM SNAPSHOT")
                    + ' · ACTUAL SST DATA</div>', unsafe_allow_html=True)
        projects = parse_projects()
        members = parse_members()
        updates = parse_recent_updates(limit=6)
        deadlines, _, _, _ = parse_tracking()
        blockers = [
            f"{project['name']} — {project.get('blocker') or project.get('status')}"
            for project in projects
            if project.get("blocker")
            or re.search(r"blocked|阻塞|卡住", project.get("status", ""), re.I)
        ]
        st.markdown(
            '<div class="snapshot-cover"><div class="flow-label">LU / TEAM SNAPSHOT</div>'
            '<div class="snapshot-title">One team.<br>One clear picture.</div>'
            f'<div class="flow-label">{datetime.date.today().isoformat()} · '
            f'{len(projects):02d} PROJECTS · {len(members):02d} MEMBERS</div></div>',
            unsafe_allow_html=True,
        )
        stat_cols = st.columns(3)
        for col, value, label in [
            (stat_cols[0], f"{len(projects):02d}", "PROJECTS"),
            (stat_cols[1], f"{len(members):02d}", "MEMBERS"),
            (stat_cols[2], f"{len(deadlines):02d}", "TRACKED NEXT STEPS"),
        ]:
            with col:
                st.markdown(f'<div class="stat-num">{value}</div>'
                            f'<div class="flow-label">{label}</div>', unsafe_allow_html=True)
        st.markdown('<div class="flow-rule"></div>', unsafe_allow_html=True)

        def _snap_card(title, lines, empty="Nothing recorded."):
            body = "".join(f"<div>{html.escape(line)}</div>" for line in lines) or f"<div>{empty}</div>"
            st.markdown(f'<div class="snap-card"><div class="flow-label">{title}</div>{body}</div>',
                        unsafe_allow_html=True)

        snap_cols = st.columns(2)
        with snap_cols[0]:
            _snap_card("PROJECT PULSE",
                       [f"{p['name']} · {p.get('status') or 'No status recorded'}" for p in projects[:4]]
                       + ([f"+ {len(projects) - 4} more projects"] if len(projects) > 4 else []))
            _snap_card("RECENT UPDATES",
                       [f"{u['heading']} · {u['text']}" for u in updates[:4]])
        with snap_cols[1]:
            _snap_card("PEOPLE & RESPONSIBILITIES",
                       [f"{m['name']} · {m.get('current_task') or 'Not recorded'}" for m in members[:4]]
                       + ([f"+ {len(members) - 4} more members"] if len(members) > 4 else []))
            next_lines = [f"{p['name']} · {p['next_step']}" for p in projects if p.get("next_step")][:3]
            next_lines += [f"{it['deadline']} · {it['step']}" for it in deadlines[:3]]
            next_lines += [f"Blocker · {b}" for b in blockers[:2]]
            _snap_card("NEXT STEPS & BLOCKERS", next_lines,
                       empty="No blockers or next steps are currently recorded.")
        pdf_data = make_snapshot_pdf(projects, members, updates, deadlines, blockers)
        try:
            png_data = _poster_png(projects, members, updates, deadlines, blockers)
        except ImportError as error:
            st.error(f"PNG export is unavailable: {error}")
            png_data = None
        report_prefix = "weekly-team-brief" if is_executive else "team-snapshot"
        report_name = f"lu-{report_prefix}-{datetime.date.today().isoformat()}"
        downloads = st.columns(2)
        with downloads[0]:
            st.download_button("DOWNLOAD PDF", pdf_data, f"{report_name}.pdf",
                               "application/pdf", type="primary")
        if png_data:
            with downloads[1]:
                st.download_button("DOWNLOAD PNG", png_data, f"{report_name}.png",
                                   "image/png")
        if not role:
            st.caption("No authentication or permission backend is configured; this shared report is available to app visitors.")
        st.markdown('<div class="flow-footer">ONE KETTLE · ONE CONVERSATION · ONE SHARED BRAIN</div>',
                    unsafe_allow_html=True)
