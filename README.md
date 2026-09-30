# LU Brain

AI ops assistant for Lady Up, a women's sports learning community.
Members chat with it to onboard, find tasks, break down deadlines, and report
progress. Execs get a weekly chase list and only handle activation + exceptions.

Live demo runs on Streamlit Community Cloud (free tier).

## What it does

- **Chat** — talk to the team brain. Starter questions are shown up front
  ("Who am I on the team?", "What projects can I join?", "What are the
  deadlines for my project?", "What are my next 3 steps?").
- **Onboarding** — new members tap "I am new here" and the agent walks them
  through 6 quick questions, one at a time (under 3 minutes). The profile is
  saved to the member list after confirmation.
- **Schedule breakdown** — give it a project deadline and it works backwards
  through the team workflow (e.g. the 5-week interview cycle) to produce the
  next 3 concrete steps with suggested dates.
- **Brain** — browse the shared knowledge base (SST Brain).
- **Chase List** — one click generates the weekly list: members awaiting
  activation, overdue tasks, two weeks of silence, items needing a decision.
  Each item comes with a short line the exec can forward as-is.

## Run locally

```bash
git clone https://github.com/riley-wangdaiwei/LU-agent.git
cd LU-agent
cp .env.example .env        # add your GEMINI_API_KEY (free at aistudio.google.com)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Optional: set `GEMINI_MODEL` in `.env` to switch models (default `gemini-3.8-flash`).

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
the app's own filesystem, which Streamlit Cloud wipes on redeploy. Anything
that must stick should be edited on GitHub — the repo is the source of truth.

## Project structure

- `app.py` — the Streamlit app (Chat / Brain / Chase List)
- `requirements.txt` — Python dependencies
- `.env.example` — copy to `.env`, add your key (never commit `.env`)
- `sst_brain/` — the shared knowledge base (6 Markdown files)
- `assets/` — Lady Up logo

## Security note

Never hardcode API keys. The key lives only in `.env` locally (git-ignored)
or in Streamlit Secrets when deployed. If a key ever leaks, revoke it in
Google AI Studio and issue a new one.
