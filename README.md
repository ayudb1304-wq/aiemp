# AI Employee

Turns your recorded sync-ups into tracked action items and a morning brief, automatically.

```
transcripts/   drop transcripts here (Teams .docx exports work as-is, or YYYY-MM-DD-<meeting>.md)
context/       CONTEXT.md, the agent's memory: team, name map, priority rules, standing decisions
cards/         generated JSON action cards, one file per transcript
tracker/       actions.xlsx, the source of truth, mirrored two-way to the Google Sheet
briefs/        morning brief + one digest per team
scripts/       extract.py -> tracker.py -> brief.py, plus sheets.py for the Google Sheet
```

## How it runs
1. **Push a transcript** to `transcripts/`. The `process-transcripts` workflow pulls any edits
   you made in the Google Sheet, runs `extract.py` (Claude reads CONTEXT.md, the open items
   and the transcript, emits cards), merges them into `actions.xlsx` (updating existing items
   instead of duplicating), commits, and rewrites the **Actions** tab of the Sheet.
2. **Every weekday 08:30 IST** the `morning-brief` workflow writes `briefs/<date>.md` and
   `briefs/teams/<team>.md`, commits them, and copies the brief into the **Morning Brief** tab.

## Setup (once)
1. In the GitHub repo: **Settings > Actions > General > Workflow permissions**, choose
   "Read and write permissions".
2. **Settings > Secrets and variables > Actions > New repository secret**:
   - `ANTHROPIC_API_KEY`: your key from https://console.anthropic.com/settings/keys
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: the full contents of the service account key file (step 3)
3. Google Sheet access (service account, no OAuth pop-ups in CI):
   1. https://console.cloud.google.com, create a project (any name).
   2. **APIs & Services > Library**: enable **Google Sheets API** and **Google Drive API**.
   3. **IAM & Admin > Service Accounts > Create service account**, then **Keys > Add key > JSON**.
      The downloaded file is the value for `GOOGLE_SERVICE_ACCOUNT_JSON`.
   4. Open the Google Sheet, click **Share**, add the service account email
      (`something@<project>.iam.gserviceaccount.com`) as **Editor**.
   The spreadsheet id is set in `scripts/common.py` (`GSHEET_ID`). To point at another sheet,
   add a repository variable `GSHEET_ID`.
4. Edit `context/CONTEXT.md`. This is what makes it work like *you*.

## Day to day
- Drop transcript files into `transcripts/` and push. That is all.
- Mark things done in the Google Sheet (status column: `done`), or change owner, due, priority,
  notes. The next workflow run pulls those edits into the repo before it does anything else.
  Do not add new rows in the Sheet by hand; they are overwritten. New items come from transcripts.
- Or from the CLI: `python scripts/tracker.py --close <id>`.
- Run either workflow manually from the **Actions** tab with **Run workflow**.

## Local testing
```
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY="..."        # PowerShell
python scripts/extract.py
python scripts/brief.py
```
Set `GOOGLE_SERVICE_ACCOUNT_JSON` too if you want `python scripts/sheets.py push` to work locally.
