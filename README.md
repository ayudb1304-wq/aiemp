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
               Dashboard sheet (headline numbers, workload and due-horizon charts) + Actions table
drafts/        drafts/<item-id>.md for communicate / review items (never sent anywhere)
briefs/        briefs/<date>.md (the plan, what the memory reads) + <date>.html (the same plan as a
               styled page) + <date>.json (the plan as data) + briefs/teams/<team>.md
logs/          logs/<run-id>.json: what context was loaded, dropped, token estimates
scripts/       extract.py -> verify.py -> tracker.py -> brief.py; sheets.py; ask.py; prep.py; search.py;
               consolidate.py; selftest.py
```

## How it runs
1. **Push a file to `inbox/`.** The `process-transcripts` workflow runs the self-test, pulls your
   Sheet edits, then `extract.py`: a cheap triage pass finds the project and people, then Claude
   reads the layered context (company + project file, open items of that project, people,
   matching history, last briefs, all under a token budget) and the document (chunked, never
   truncated) and emits cards, decisions and threads. Cards merge into `actions.xlsx` (updating
   existing items instead of duplicating, at most 3 P1 per document, never setting `done`),
   drafts are written for communicate/review items, everything is committed, and the **Actions**
   tab of the Sheet is rewritten (frozen header and task column, colour by priority, status and
   overdue date, dropdowns on the enumerated columns), along with a **Dashboard** tab of live
   formulas over it and read-only **Decisions** and **Threads** tabs mirrored from
   `memory/decisions.jsonl` and `memory/threads.jsonl`.
2. **Every weekday morning (07:53 IST, retried 09:53)** the `morning-brief` workflow writes `briefs/<date>.md`: waiting for
   your confirmation, do first, batch, delegate?, recurring threads, then per project. The same
   plan is written as `briefs/<date>.html` (headline numbers, cards, per-project tables; open it
   in a browser or forward it) and `briefs/<date>.json`. It commits and writes the plan into the
   **Morning Brief** tab as a formatted table (section bands, priority and overdue colours).
   It then puts your own items for the day on your Google Calendar as blocks of 5 to 60 minutes
   (`scripts/dayplan.py`): items you own that are due, overdue or P1, plus anything waiting for
   your confirmation. On a day with nothing of your own, each teammate item that is due, overdue,
   P1 or blocked becomes a 5-minute check-in block instead (up to `checkin_max`), so the day
   still has a shape. Lunch and the walk in `context/dayplan.json` are never touched, except that
   5-minute phone-sized items (a confirmation, a message) may sit inside the walk. Blocks you move
   stay where you put them; blocks for items that closed are removed. Nothing tracks whether a block
   happened yet, this is a reminder, not a scorecard.
   **Habits** (`scripts/habits.py`, config in `context/habits.json`): the walk is tracked against
   a weekly quota rather than nagged daily. The walk event's title carries the score ("Walk: 2 of 5
   this week, 3 days left"), turns red with three reminders only when the slack is gone
   ("MANDATORY today"), and green once the quota is met. You record done, skip or snooze in the
   **Habits** tab of the Sheet (one row per day of the current week); the next run pulls it into
   `memory/habits.jsonl` and refreshes the event. Skip shows its consequence in the description.
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
5. Google Calendar (optional, for the day blocks).
   1. In the Cloud project from step 4, **APIs & Services > Library**: enable **Google Calendar API**
      (a 403 from the calendar step means this was skipped).
   2. In Google Calendar, **Settings > Add calendar > Create new calendar**, name it "AI Employee".
      A separate calendar keeps generated blocks apart from real meetings and lets you hide them.
   3. Open that calendar's settings, **Share with specific people**, add the service account email
      with **Make changes to events**. Scroll to **Integrate calendar** and copy the **Calendar ID**.
   4. Add it as the repository variable `GCAL_ID` (Settings > Secrets and variables > Actions >
      Variables), or set `calendar_id` in `context/dayplan.json`.
   5. So blocks avoid your real meetings, also share your main calendar with the service account
      as **See only free/busy** and add its address (your Gmail) to `busy_calendars` in
      `context/dayplan.json`.
   Working hours, lunch, the walk and slot sizes are all in `context/dayplan.json`. If you use
   Option B above, run `google_login.py` again: the calendar scope was added to it.
6. Edit `context/company.md` and `context/projects/<slug>.md`. This is what makes it work like
   *you*. Add a project by adding a file; its file name is the slug used in card ids
   (`YYYY-MM-DD-<project-slug>-NN`). Files the agent cannot place go to `unassigned` and are
   flagged in the brief.

## The tracker workbook
`tracker/actions.xlsx` opens on a **Dashboard**: open / P1 / overdue / due today / to verify /
blocked / done tiles, open items by owner (stacked by priority, with a chart), the due horizon
(overdue, today, next 7 days, later, no date, with a chart), and per-project and per-status
tables. Every number is a formula over the Actions sheet, so a status or priority you change in
Excel moves the tiles and charts at once (the owner and project lists refresh on the next run).
GitHub's file preview shows formula cells blank; open the file in Excel. The **Actions** sheet is an Excel table
(filter and sort from the header, banded rows) with the columns you act on first: task, owner,
priority, status, due, project, team, then the advisor columns (type, next step, unblocker,
effort, blocked by, prerequisites, notes, evidence, basis, flags) and provenance last (meeting,
origin, updated, closed, created, source, id). Colour follows the data: P1 red, to_verify amber, blocked
orange, in_progress blue, done green, overdue dates in red. Status, priority, type, effort and
origin are dropdowns. Dates are real dates, so Excel can filter them by month.

## Day to day
- Drop files into `inbox/` and push. That is all. Name them `YYYY-MM-DD-<meeting>.<ext>`; without
  a date prefix the file's modification date is used (and a warning logged). Quick notes go in as
  `note-YYYY-MM-DD-<anything>.md`, one task per line.
- When a transcript says something is finished, the item becomes `to_verify` and shows up at the
  top of the brief. Set `done` in the Sheet (status column) if it is true. Only you set `done`.
- Edit owner, due, priority, status, notes, blocked_by, project, task, type, unblocker,
  next_step or effort in the Sheet. The next run pulls those edits back and logs each change to
  `memory/corrections.jsonl`; the extraction prompt sees your recent corrections, the next
  brief lists them under "Since", and the weekly consolidation PR proposes rule changes from them. Delete a row in the Sheet and the item is set
  to `rejected` (kept in the tracker, never removed). Do not add rows by hand; they are overwritten.
- Or from the CLI: `python scripts/tracker.py --close <id>`.
- Ask the memory: `python scripts/ask.py "when did we last discuss the currency selector"`.
  Keyword search over cards, decisions, threads and briefs; with an API key Claude summarises the
  dated hits, without one you get the raw list.
- Run any workflow manually from the **Actions** tab with **Run workflow**. Review the weekly
  consolidation PR: merge it to accept the new project state, or close it.

## How it keeps itself honest
- **Fact checks on every card** (`scripts/verify.py`, run inside extraction, no model call). The
  evidence quote must be in the document (transcription-tolerant), the owner must be on the
  roster (a name-map alias such as "Lakshmi" is replaced by the real name), the basis of a next
  step must resolve to a known id, a context file or text in the document or context, the due
  date must parse and sit between the meeting date and a year out, a blocked item must name its
  blocker, and a matched id must exist. A card that fails is kept with the reasons in its `flags`
  column (amber in Excel and the Sheet) and listed under **Check these** in the brief. Over time
  the column shows how often, and where, the model gets things wrong. Re-check a cards file with
  `python scripts/verify.py cards/<file>.json`.
- **Decision chains.** A decision that supersedes an earlier one marks it replaced. The history
  the model sees carries `current` and `replaced_by` on every decision and the prompt says a
  replaced decision is never a basis; the Sheet's Decisions tab shows the same two columns; the
  brief and the prep pack list only the decisions in force per project.
- **Retrieval that understands your names and terms** (`scripts/search.py`). Matching is BM25
  over canonical tokens: the name map in `company.md` folds transcription aliases into the real
  person, short glossary expansions fold into their term ("change request" -> CR), plurals and
  verb endings are stemmed, and a query word found nowhere is matched fuzzily. This feeds context
  layer 4, `ask.py` and `prep.py`; each run log records `history_hits`.
- **Since the last brief.** The brief opens with what moved: new, closed, reported complete,
  slipped (from the due-change markers in notes), rejected, your Sheet edits, decisions and
  threads. It is a comparison of dated records, never a summary the model wrote.
- **Meeting prep pack.** `python scripts/prep.py Laxmikant` or `--project intelligent-tracker`
  (or both) writes `briefs/prep/<date>-<who>.md` and `.html`: what they own, what is waiting on
  them, what they reported complete, their last commitments as quoted, decisions they made
  (current first), threads they keep raising, slips and drafts ready to paste. Every line has an
  id, a date or a quote; nothing is generated and no API key is needed.

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
python scripts/prep.py Laxmikant --project intelligent-tracker   # meeting prep pack, no key needed
python scripts/verify.py cards/2026-09-16-activity-tracker-sync.json   # re-run the fact checks
```
Set `GOOGLE_SERVICE_ACCOUNT_JSON` or `GOOGLE_OAUTH_TOKEN_JSON` too if you want
`python scripts/sheets.py push` to work locally. `scripts/migrate_0001_project.py` added the
`project` column to the existing tracker; it is safe to re-run and no longer needed.
