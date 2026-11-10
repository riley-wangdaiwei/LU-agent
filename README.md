# LU — Team Conversation

LU is the central coordination assistant for Lady Up. The app follows one
scrolling flow: **Talk → Review → Remember**. Members onboard, share
progress and blockers conversationally, review structured updates, and confirm
what should be written to the SST Brain.

Live demo runs on Streamlit Community Cloud (free tier).

## What it does

- **Kettle-led flow** — a persistent tea kettle image anchors the dark, glass
  interface as people scroll from identity to conversation and SST.
- **Conversational updates** — LU uses the existing Gemini agent and SST
  context for onboarding, project questions, scheduling, and progress reports.
- **Confirm before writing** — profile, progress, deadline, activation, and assigned next-step
  updates are presented for confirmation before being saved.
- **SST Brain** — search and read the team's existing Markdown knowledge base.
- **Team Snapshot** — an editorial report preview with PDF and PNG downloads
  generated from current project, member, update, and tracking data. Members
  whose SST profile explicitly identifies an executive role see an executive
  weekly-brief presentation.

The app currently has no authentication or permission backend. Executive
presentation is based on the saved profile role, not a protected authorization
boundary; do not treat it as access control. Team Snapshot downloads are
available to all app visitors because the existing app has no authentication
or permission backend.

## Run locally

```bash
git clone https://github.com/riley-wangdaiwei/LU-agent.git
cd LU-agent
cp .env.example .env        # add your GEMINI_API_KEY (free at aistudio.google.com)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Optional: set `GEMINI_MODEL` in `.env` to switch models (default `gemini-3.5-flash-lite`).

## Deploy (Streamlit Community Cloud, free)

1. Push this repo to GitHub (already done).
2. Go to share.streamlit.io → New app → pick the repo, main file `app.py`.
3. App settings → Secrets, add:
   ```toml
   GEMINI_API_KEY = "your-key-here"
   ```
4. Deploy, then share the public link with the team.

## Updating the SST Brain

The brain is plain Markdown in `sst_brain/`. The deployed app reads it straight
from the GitHub repo, so **editing a file on GitHub is all it takes** —
Streamlit redeploys automatically and the change is live in a minute or two.
No Streamlit settings to touch.

Easiest method: open the file on github.com → pencil icon → edit → Commit changes.

| File | What it holds | Update when |
|---|---|---|
| `00_overview.md` | Index: what each file is, who updates it | Only if the structure changes |
| `01_longterm.md` | Vision + 1/3/5-year goals, quantitative targets | Whenever plans change (team lead) |
| `02_protocols_workflows.md` | Team rules + content production workflows | Only when a rule/workflow changes |
| `03_active_projects.md` | Active projects, schedules, owners | Weekly (agent drafts, lead/exec confirms) |
| `04_members.md` | Member profiles: interests, availability, resources | Members update via chat, or edit here directly |
| `05_tracking.md` | Tracking log + weekly chase list | Agent maintains |

One caveat: profiles saved **through chat on the deployed app** are written to
the app's own filesystem, which Streamlit Cloud wipes on redeploy. Enable
GitHub sync (below) so those edits are pushed back to the repo automatically.

## GitHub sync (member profiles)

When a member confirms their onboarding profile in chat, the app can push
`sst_brain/04_members.md` back to GitHub by itself:

1. Create a fine-grained personal access token: GitHub → Settings →
   Developer settings → Personal access tokens → Fine-grained →
   select the `LU-agent` repo → Permissions → **Contents: Read and write**.
2. In Streamlit: app settings → Secrets, add:
   ```toml
   GITHUB_TOKEN = "your-token-here"
   ```
3. Done. Each confirmed update is committed straight to **`main`**, so the
   live app reads it after the next redeploy (about a minute). Every
   confirmed change is a GitHub commit, so a bad write can be reverted
   from the repo history.

## Project structure

- `app.py` — Agent, SST parsing/writes, and app entry point
- `hub_ui.py` — single-page scrolling Talk / Review / Remember interface
- `requirements.txt` — Python dependencies
- `.env.example` — copy to `.env`, add your key (never commit `.env`)
- `sst_brain/` — the shared knowledge base (6 Markdown files)
- `assets/` — brand assets, including the floating kettle-and-cloth hero

## Security note

Never hardcode API keys. The key lives only in `.env` locally (git-ignored)
or in Streamlit Secrets when deployed. If a key ever leaks, revoke it in
Google AI Studio and issue a new one.
