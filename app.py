"""
LU Brain — Lady Up 团队的 AI 运营助手 (MVP)

运行: streamlit run app.py
需要环境变量: GEMINI_API_KEY (见 .env.example)
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

BRAIN_FILES = {
    "总览": "00_overview.md",
    "长期计划": "01_longterm.md",
    "公约与流程": "02_protocols_workflows.md",
    "当前项目": "03_active_projects.md",
    "成员档案": "04_members.md",
    "追踪日志": "05_tracking.md",
}

# ---------- Brain 读取 ----------
def read_brain() -> dict:
    out = {}
    for name, fname in BRAIN_FILES.items():
        p = BRAIN_DIR / fname
        out[name] = p.read_text(encoding="utf-8") if p.exists() else "(空)"
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

SYSTEM_PROMPT = """你是 Lady Up（女性体育共学社区）团队的 AI 运营助手 "LU Brain"。
你的工作是代替 exec 做一对一的跟进：了解成员、匹配任务、跟踪进度。

你手上有 SST Brain（团队共享大脑），包含：总览、长期计划、公约与流程、
当前项目、成员档案、追踪日志。请基于它回答，不要编造 brain 里没有的事实。

你的能力：
1. 新成员 onboarding：用聊天逐个收集 6 个字段（名字/角色、兴趣标签、每周可投入、
   资源/技能、当前任务、状态），集齐后输出一个 ```profile 代码块（JSON 格式，
   键名用中文），并请用户确认。一次只问一个问题，语气轻松。
2. 回答关于团队公约、workflow、项目排期的问题。
3. 帮成员找任务：按兴趣/空闲/资源从当前项目和 workflow 里推荐。
4. 成员汇报进度时，记下来并给反馈。
5. 被要求时生成"每周追人清单"：待激活成员 / 任务 overdue / 两周无动静 /
   需要 Riles 或 exec 拍板的事项，每条附上一句可直接转发的提醒文案。

注意：派任务、改排期这类动作只给建议，不直接做决定，提醒用户找 exec / Riles 确认。
"""

def chat_once(client, history: list, user_msg: str) -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    contents = []
    for h in history:
        contents.append({"role": h["role"], "parts": [{"text": h["text"]}]})
    contents.append({"role": "user", "parts": [{"text": user_msg}]})
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config={"system_instruction": SYSTEM_PROMPT + "\n\n" + brain_context()},
    )
    return resp.text

# ---------- 页面 ----------
st.set_page_config(page_title="LU Brain", page_icon="🏀", layout="wide")

st.markdown("""
<style>
:root { --pink: #EB5D73; --green: #02C64F; }
html, body, [class*="css"] { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
h1, h2, h3 { font-family: Georgia, "Songti SC", serif !important; letter-spacing: 0.01em; }
.block-container { max-width: 860px; padding-top: 2rem; }
a { color: #EB5D73 !important; }
.stButton > button { border-radius: 999px; border: 1px solid #161616; }
.stButton > button[kind="primary"] { background: #EB5D73; border-color: #EB5D73; color: white; }
hr { border: none; border-top: 1px solid #e5e0d8; }
.brain-card { background: #FAF8F4; border: 1px solid #e5e0d8; border-radius: 12px; padding: 1.2rem 1.4rem; }
.issue-tag { display:inline-block; background:#EB5D73; color:#fff; border-radius:999px;
             padding: 0.1rem 0.7rem; font-size: 0.8rem; margin-right: 0.4rem; }
.ok-tag { display:inline-block; background:#02C64F; color:#fff; border-radius:999px;
          padding: 0.1rem 0.7rem; font-size: 0.8rem; margin-right: 0.4rem; }
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    logo = ASSETS / "lu_new_1.png"
    if logo.exists():
        st.image(str(logo), use_container_width=True)
    st.markdown("### LU Brain")
    st.caption("Lady Up 团队运营助手 · MVP")
    nav = st.radio("导航", ["💬 聊天", "📖 Brain", "🎯 追人清单"], label_visibility="collapsed")
    st.divider()
    st.caption("Brain 文件就绪" if (BRAIN_DIR / "00_overview.md").exists() else "Brain 文件缺失")

# ---------- 聊天 ----------
if nav == "💬 聊天":
    st.markdown("## 聊天")
    st.caption("新成员先做 onboarding（6 个问题），老成员直接聊任务和进度。")

    client = get_client()
    if client is None:
        st.warning("还没配置 GEMINI_API_KEY。按 .env.example 建一个 .env 文件再重启。")
        st.stop()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for m in st.session_state.messages:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])

    # 检查上一条 AI 回复里有没有待确认的 profile
    def extract_profile(text: str):
        m = re.search(r"```profile\s*(\{.*?\})\s*```", text, re.S)
        if not m:
            return None
        import json
        try:
            return json.loads(m.group(1))
        except Exception:
            return None

    if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
        prof = extract_profile(st.session_state.messages[-1]["text"])
        if prof and not st.session_state.get("profile_saved"):
            st.info("上面这份档案确认无误的话，点一下写入：")
            if st.button("✅ 确认写入成员档案", type="primary"):
                p = BRAIN_DIR / "04_members.md"
                cur = p.read_text(encoding="utf-8") if p.exists() else ""
                lines = ["\n### " + prof.get("名字/角色", prof.get("名字", "新成员"))]
                for k in ["兴趣标签", "每周可投入", "资源/技能", "当前任务", "状态"]:
                    if prof.get(k):
                        lines.append(f"- {k}：{prof[k]}")
                new = cur.rstrip() + "\n" + "\n".join(lines) + "\n"
                # 替换"待 exec 激活后逐个填写"占位行
                new = new.replace("（待 exec 激活后逐个填写）", "（待 exec 激活后逐个填写）")
                p.write_text(new, encoding="utf-8")
                st.session_state.profile_saved = True
                st.success("已写入 04_members.md")

    if prompt := st.chat_input("跟 LU Brain 聊聊…"):
        st.session_state.messages.append({"role": "user", "text": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("想想…"):
                try:
                    reply = chat_once(client, st.session_state.messages[:-1], prompt)
                except Exception as e:
                    reply = f"出小差了：{e}"
            st.markdown(reply)
        st.session_state.messages.append({"role": "assistant", "text": reply})
        st.session_state.profile_saved = False
        st.rerun()

# ---------- Brain ----------
elif nav == "📖 Brain":
    st.markdown("## SST Brain")
    st.caption("团队共享大脑。长期计划由 Riles 更新，成员档案由成员自己找 agent 改。")
    brain = read_brain()
    tabs = st.tabs(list(BRAIN_FILES.keys()))
    for tab, name in zip(tabs, BRAIN_FILES.keys()):
        with tab:
            st.markdown(f'<div class="brain-card">{brain[name]}</div>', unsafe_allow_html=True)

# ---------- 追人清单 ----------
else:
    st.markdown("## 🎯 每周追人清单")
    st.caption("给 exec 用的：agent 生成，exec 只做最小动作（转发文案）。")
    client = get_client()
    if client is None:
        st.warning("还没配置 GEMINI_API_KEY。")
        st.stop()
    if st.button("生成本周追人清单", type="primary"):
        with st.spinner("正在读 brain…"):
            prompt = ("请根据 SST Brain 生成本周追人清单，分四节：1) 待激活成员 "
                        "2) 任务 overdue 3) 两周无动静 4) 需要 Riles/exec 拍板的事项。"
                        "每条附上一句 exec 可直接转发的提醒文案。没有信息就如实说「缺数据」。")
            try:
                out = chat_once(client, [], prompt)
                st.markdown(out)
            except Exception as e:
                st.error(f"生成失败：{e}")
