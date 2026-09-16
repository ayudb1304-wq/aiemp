# AI Employee

Turns whatever lands in your inbox (meeting transcripts, notes, emails, docs) into tracked action
items, a decisions log, open threads, drafts and a morning plan. Automatically, on push.

```
inbox/         drop files here: .md .txt .vtt .srt .docx .pdf .eml  (YYYY-MM-DD-<name>.<ext>)
               note-YYYY-MM-DD-<name>.md = a quick note; its items are "unplanned"
context/       the agent's memory (human-edited; the agent only proposes changes via PR)
  company.md            me, roster, name map, priority rules, escalation, standing decisions
  projects/<slug>.md    one file per project: goal, phase, next milestone, risks, people, glossary
  people/<name>.md      optional per-person notes
memory/        what the agent learned (machine-written, never deleted)
  decisions.jsonl       "we will / we won't" choices, with supersedes links
  threads.jsonl         things mentioned but not committed; 3 mentions -> "make this a task?"
  corrections.jsonl     every cell you changed in the Google Sheet
cards/         one JSON file per processed inbox file (cards, decisions, threads, kind, project)
tracker/       actions.xlsx, the source of truth, mirrored two-way to the Google Sheet
drafts/        drafts/<item-id>.md for communicate / review items (never sent anywhere)
briefs/        briefs/<date>.md (the plan) + briefs/teams/<team>.md
logs/          logs/<run-id>.json: what context was loaded, dropped, token estimates
scripts/       extract.py -> tracker.py -> brief.py; sheets.py; ask.py; consolidate.py; selftest.py
```

## How it runs
1. **Push a file to `inbox/`.** The `process-transcripts` workflow runs the self-test, pulls your
   Sheet edits, then `extract.py`: a cheap triage pass finds the project and people, then Claude
   reads the layered context (company + project file, open items of that project, people,
   matching history, last briefs, all under a token budget) and the document (chunked, never
   truncated) and emits cards, decisions and threads. Cards merge into `actions.xlsx` (updating
   existing items instead of duplicating, at most 3 P1 per document, never setting `done`),
   drafts are written for communicate/review items, everything is committed, and the **Actions**
   tab of the Sheet is rewritten.
2. **Every weekday 08:30 IST** the `morning-brief` workflow writes `briefs/<date>.md`: waiting for
   your confirmation, do first, batch, delegate?, recurring threads, then per project. It commits
   and copies the brief into the **Morning Brief** tab.
3. **Every Sunday 18:00 IST** the `consolidate` workflow rewrites each active project's state file
   from the week's activity and opens a **pull request** (`consolidate/<date>`) with the proposed
   files, observations (days-to-close by type and unblocker, most slipped items) and suggested rule
   changes derived from your corrections. Nothing reaches `main` without you.

## Setup (once)
1. **Make the repository private** (Settings > General > Danger zone > Change visibility). The
   inbox holds real transcripts.
2. **Settings > Actions > General > Workflow permissions**: "Read and write permissions", and tick
   "Allow GitHub Actions to create and approve pull requests" (needed by the consolidation PR).
3. **Settings > Secrets and variables > Actions > New repository secret**:
   - `ANTHROPIC_API_KEY`: your key from https://console.anthropic.com/settings/keys
   - one Google credential from step 4: `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_TOKEN_JSON`
4. Google Sheet access. Pick ONE of the two options.

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
5. Edit `context/company.md` and `context/projects/<slug>.md`. This is what makes it work like
   *you*. Add a project by adding a file; its file name is the slug used in card ids
   (`YYYY-MM-DD-<project-slug>-NN`). Files the agent cannot place go to `unassigned` and are
   flagged in the brief.

## Day to day
- Drop files into `inbox/` and push. That is all. Name them `YYYY-MM-DD-<meeting>.<ext>`; without
  a date prefix the file's modification date is used (and a warning logged). Quick notes go in as
  `note-YYYY-MM-DD-<anything>.md`, one task per line.
- When a transcript says something is finished, the item becomes `to_verify` and shows up at the
  top of the brief. Set `done` in the Sheet (status column) if it is true. Only you set `done`.
- Edit owner, due, priority, status, notes, blocked_by, project, task, type, unblocker,
  next_step or effort in the Sheet. The next run pulls those edits back and logs each change to
  `memory/corrections.jsonl`; the extraction prompt sees your recent corrections, and the weekly
  consolidation PR proposes rule changes from them. Delete a row in the Sheet and the item is set
  to `rejected` (kept in the tracker, never removed). Do not add rows by hand; they are overwritten.
- Or from the CLI: `python scripts/tracker.py --close <id>`.
- Ask the memory: `python scripts/ask.py "when did we last discuss the currency selector"`.
  Keyword search over cards, decisions, threads and briefs; with an API key Claude summarises the
  dated hits, without one you get the raw list.
- Run any workflow manually from the **Actions** tab with **Run workflow**. Review the weekly
  consolidation PR: merge it to accept the new project state, or close it.

## What the agent never does
- Close an item (`done` is yours). It proposes `to_verify`.
- Delete from memory. Files are rewritten; history stays in git.
- Truncate a document. Long ones are chunked at paragraph boundaries and merged.
- Change its own rules. `context/` is edited by you or via the consolidation PR.
- Give advice without a basis. `next_step` is null unless it can cite an item, decision, quote or
  context file (the `basis` column).
- Send a draft anywhere. `drafts/<item-id>.md` is for you to copy.

## Local testing
```
pip install -r requirements.txt
python scripts/selftest.py                    # no API key needed; also runs first in every workflow
$env:ANTHROPIC_API_KEY="..."                  # PowerShell
python scripts/extract.py                     # every inbox file without a cards file yet
python scripts/brief.py
python scripts/consolidate.py --dry-run       # prints proposed project files + PR body, writes nothing
python scripts/ask.py "what did we decide about the currency selector"
```
Set `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_TOKEN_JSON` too if you want
`python scripts/sheets.py push` to work locally. `scripts/migrate_0001_project.py` added the
`project` column to the existing tracker; it is safe to re-run and no longer needed.
