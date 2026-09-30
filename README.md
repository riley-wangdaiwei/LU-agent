# LU Brain — Lady Up 团队 AI 运营助手

Streamlit MVP。团队成员直接跟 agent 聊天：onboarding、领任务、汇报进度；
exec 每周一键生成追人清单，只做最小动作。

## 模型（免费）

用 **Gemini 2.0 Flash 免费档**：15 请求/分钟、1500 请求/天，不用太好的模型，
够团队日常用。换模型改环境变量 `GEMINI_MODEL` 即可。

## 本地运行

```bash
cp .env.example .env      # 填入 GEMINI_API_KEY（去 aistudio.google.com 免费申请）
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

**安全**：旧 repo 曾把 API key 硬编码提交，已删除。key 只放 `.env`（已在
`.gitignore`），不要提交。如果旧 key 泄露过，去 AI Studio 删掉重建一个。

## 部署（Streamlit Community Cloud，免费）

1. 把本 repo 推到 GitHub
2. share.streamlit.io → New app → 选这个 repo，`app.py`
3. Settings → Secrets 里加 `GEMINI_API_KEY`
4. 得到公开链接发给团队成员

## 目录

- `app.py` — 主应用（聊天 / Brain / 追人清单）
- `sst_brain/` — 团队共享大脑（5 件套，见 `00_overview.md`）
- `assets/` — LU logo（branding 文件夹）
