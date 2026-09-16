# AI Employee

Turns your recorded sync-ups into tracked action items and a morning brief, automatically.

```
transcripts/   drop transcripts here (name them YYYY-MM-DD-<meeting>.md)
context/       CONTEXT.md — the agent's memory: teams, projects, your priority rules
cards/         generated JSON action cards, one file per transcript
tracker/       actions.xlsx — source of truth, mirrored to Drive/SharePoint
briefs/        morning brief + one digest per team
scripts/       extract.py -> tracker.py -> brief.py
```

## How it runs
1. **Push a transcript** → `process-transcripts` workflow runs `extract.py`: Claude reads
   CONTEXT.md + the open items + the transcript, emits cards, `tracker.py` merges them into
   `actions.xlsx` (updating existing items instead of duplicating), commits, and uploads the
   Excel file to your Drive/SharePoint folder.
2. **Every weekday 08:30 IST** → `morning-brief` workflow runs `brief.py`: writes
   `briefs/<date>.md` and `briefs/teams/<team>.md`, and posts them to Slack if webhooks are set.

## Setup (once)
1. Create a private GitHub repo with these files. In **Settings → Actions → General**, allow
   workflows read and write permissions.
2. Add secret `ANTHROPIC_API_KEY`.
3. Edit `context/CONTEXT.md` — this is what makes it work like *you*.
4. Drive/SharePoint upload (optional): install [rclone](https://rclone.org) locally, run
   `rclone config` and create a remote named `drive` (Google Drive) or `sp` (OneDrive/SharePoint).
   Paste the contents of `~/.config/rclone/rclone.conf` into secret `RCLONE_CONFIG`, and set
   repository variable `RCLONE_DEST` to e.g. `drive:AI-Employee` or `sp:Shared/AI-Employee`.
5. Slack (optional): incoming-webhook secrets `SLACK_WEBHOOK_URL` (whole brief) and
   `SLACK_WEBHOOK_<TEAM>` per team (e.g. `SLACK_WEBHOOK_PLATFORM`). Add a line to
   `morning-brief.yml` for each new team.

## Day to day
- Drop transcript files into `transcripts/` and push. That's it.
- Mark things done in the Excel file (status column) or `python scripts/tracker.py --close <id>`.
  If the file lives in Drive/SharePoint, copy it back into `tracker/` before pushing so the repo
  stays the source of truth — or make the repo the only place you edit it.
- Run either workflow manually from the Actions tab with **Run workflow**.

## Local testing
```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
cd scripts && python extract.py && python brief.py
```
A sample transcript is included in `transcripts/` so the first run has something to chew on.
