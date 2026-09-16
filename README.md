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
   - one Google credential from step 3: `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_TOKEN_JSON`
3. Google Sheet access. Pick ONE of the two options.

   **Option A, service account (personal Gmail account).** Sign in to
   https://console.cloud.google.com as the Gmail account that owns the sheet. A personal account
   has no organisation, so key creation is allowed. If the project picker shows an organisation
   name instead of "No organisation", you are on a work account: switch accounts or use Option B.
   1. Create a project (any name).
   2. **APIs & Services > Library**: enable **Google Sheets API**.
   3. **IAM & Admin > Service Accounts > Create service account**, then **Keys > Add key > JSON**.
      Save the downloaded file contents as the repo secret `GOOGLE_SERVICE_ACCOUNT_JSON`.
   4. Open the sheet, **Share**, add the service account email
      (`...@<project>.iam.gserviceaccount.com`) as **Editor**.

   **Option B, your own login, no keys (works when the organisation blocks service account
   keys, error `iam.disableServiceAccountKeyCreation`).**
   1. In Cloud Console, create a project and enable **Google Sheets API**.
   2. **Google Auth Platform > Branding**: fill app name and your email. **Audience**: External,
      then **Publish app** (unverified is fine, it is only you). Publishing matters: a token from
      an app left in "Testing" expires after 7 days.
   3. **Clients > Create client**: type **Desktop app**. Download the JSON.
   4. Locally: `pip install -r requirements.txt` then
      `python scripts/google_login.py C:/Downloads/client_secret.json`. A browser opens; sign in as
      the account that owns the sheet (click Advanced > Go to app on the unverified warning).
   5. Paste the printed JSON as the repo secret `GOOGLE_OAUTH_TOKEN_JSON`.

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
Set `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_TOKEN_JSON` too if you want `python scripts/sheets.py push` to work locally.
