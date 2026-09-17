"""Two-way mirror between tracker/actions.xlsx and a Google Sheet.

The repo's Excel file stays the source of truth for the scripts. The Google Sheet is
where you look at and edit items. Two commands:

  python scripts/sheets.py pull            # copy edits made in the Sheet back into actions.xlsx
                                           # and log each changed cell to memory/corrections.jsonl
  python scripts/sheets.py push            # overwrite the "Actions" tab from actions.xlsx, and the
                                           # "Decisions" and "Threads" tabs from memory/*.jsonl
  python scripts/sheets.py brief FILE.md   # write a brief into the "Morning Brief" tab

Rows deleted in the Sheet are not removed from the tracker: they are set to status `rejected`
and logged as a correction with field "deleted". Only the Actions tab is pulled back; Decisions
and Threads are read-only mirrors and are rewritten on every push.

Needs env GOOGLE_SERVICE_ACCOUNT_JSON (service account key file contents) or
GOOGLE_OAUTH_TOKEN_JSON (from scripts/google_login.py). Optional env GSHEET_ID overrides the
default spreadsheet. If neither is set, every command prints "skipped" and exits 0, so local
runs and workflows without the secret still work.
"""
import json
import os
import sys
from pathlib import Path

from common import COLUMNS, CORRECTIONS, DECISIONS, GSHEET_ID, THREADS, append_jsonl, today

EDITABLE = ("status", "owner", "due", "priority", "notes", "blocked_by", "project", "task",
            "unblocker", "next_step", "type", "effort")
ACTIONS_TAB = "Actions"
BRIEF_TAB = "Morning Brief"
DECISIONS_TAB = "Decisions"
THREADS_TAB = "Threads"
DECISION_COLS = ("id", "date", "project", "decision", "by", "evidence", "source", "supersedes")
THREAD_COLS = ("id", "date", "last_seen", "mentions", "project", "topic", "note", "evidence",
               "promoted_to", "sources")


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _client():
    """Service account key (GOOGLE_SERVICE_ACCOUNT_JSON) or a user token from
    scripts/google_login.py (GOOGLE_OAUTH_TOKEN_JSON). None if neither is set."""
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    user = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
    if not sa and not user:
        return None
    import gspread

    if sa:
        return gspread.service_account_from_dict(json.loads(sa), scopes=SCOPES)
    from google.oauth2.credentials import Credentials

    return gspread.authorize(Credentials.from_authorized_user_info(json.loads(user), SCOPES))


def _sheet(gc):
    return gc.open_by_key(os.environ.get("GSHEET_ID") or GSHEET_ID)


def _tab(sh, title: str, rows: int = 1000, cols: int = 30):
    for ws in sh.worksheets():
        if ws.title == title:
            return ws
    # Reuse the default empty first tab instead of leaving a stray "Sheet1".
    first = sh.worksheets()[0]
    if len(sh.worksheets()) == 1 and not any(first.row_values(1)):
        first.update_title(title)
        return first
    return sh.add_worksheet(title=title, rows=rows, cols=cols)


def _write_table(sh, title: str, values: list[list]) -> None:
    ws = _tab(sh, title)
    ws.clear()
    ws.update(values, "A1", value_input_option="RAW")
    ws.freeze(rows=1)
    ws.format("1:1", {"textFormat": {"bold": True}})


def memory_rows(path: Path, cols: tuple) -> list[list]:
    """Header plus one row per jsonl record, newest first. Lists are joined with ', '.
    Pure, so selftest can exercise it without credentials."""
    records = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    records.sort(key=lambda r: str(r.get("date", "")), reverse=True)

    def cell(v):
        if isinstance(v, list):
            return ", ".join(str(x) for x in v)
        return "" if v is None else str(v)

    return [list(cols)] + [[cell(r.get(c)) for c in cols] for r in records]


def push() -> None:
    gc = _client()
    if gc is None:
        print("sheets push: skipped (no Google credentials set)")
        return
    import tracker

    rows = tracker.load_rows()
    rows = sorted(rows, key=lambda r: (r["status"] not in tracker.OPEN_STATUSES,
                                       {"P1": 0, "P2": 1, "P3": 2}.get(r["priority"], 9),
                                       r["due"] or "9999"))
    sh = _sheet(gc)
    _write_table(sh, ACTIONS_TAB, [COLUMNS] + [[r.get(c, "") for c in COLUMNS] for r in rows])
    print(f"sheets push: {len(rows)} rows -> {ACTIONS_TAB}")
    for title, path, cols in ((DECISIONS_TAB, DECISIONS, DECISION_COLS),
                              (THREADS_TAB, THREADS, THREAD_COLS)):
        values = memory_rows(path, cols)
        _write_table(sh, title, values)
        print(f"sheets push: {len(values) - 1} rows -> {title}")


def apply_records(rows: list[dict], records: list[dict], log=append_jsonl) -> int:
    """Merge Sheet records into tracker rows in place. Returns the number of rows changed.
    Deterministic and network-free so selftest can exercise it."""
    by_id = {r["id"]: r for r in rows}
    seen = set()
    changed = 0
    for rec in records:
        rid = str(rec.get("id", "")).strip()
        r = by_id.get(rid)
        if not r:
            continue
        seen.add(rid)
        diff = {k: str(rec.get(k, "")).strip() for k in EDITABLE
                if k in rec and str(rec.get(k, "")).strip() != str(r.get(k, "")).strip()}
        if not diff:
            continue
        for k, v in diff.items():
            log(CORRECTIONS, {"date": today(), "id": rid, "field": k, "from": r.get(k, ""),
                              "to": v, "project": r.get("project", "")})
            if k == "due":
                tracker_note = f"{today()}: due {r.get('due') or 'none'} -> {v or 'none'} (sheet)"
                r["notes"] = (r["notes"] + " | " if r["notes"] else "") + tracker_note
        r.update(diff)
        r["updated"] = today()
        changed += 1
    # Present in xlsx, absent in the Sheet: rejected, kept, logged. Only rows that have been
    # through a push cycle (updated before today) count, and never more than half the tracker
    # at once, so a stale or wiped tab cannot reject everything.
    missing = [r for r in rows if r["id"] not in seen and r["status"] != "rejected"
               and r.get("updated", "") < today()]
    if len(missing) > len(rows) // 2:
        print(f"sheets pull: {len(missing)} rows missing from the Sheet, more than half; not rejecting any")
        missing = []
    for r in missing:
        log(CORRECTIONS, {"date": today(), "id": r["id"], "field": "deleted",
                          "from": r["status"], "to": "rejected", "project": r.get("project", "")})
        r["status"], r["updated"] = "rejected", today()
        changed += 1
    return changed


def pull() -> None:
    gc = _client()
    if gc is None:
        print("sheets pull: skipped (no Google credentials set)")
        return
    import tracker

    ws = _tab(_sheet(gc), ACTIONS_TAB)
    records = ws.get_all_records(default_blank="")
    if not records:
        print("sheets pull: sheet is empty, nothing to pull")
        return
    rows = tracker.load_rows()
    changed = apply_records(rows, records)
    if changed:
        tracker.save_rows(rows)
    print(f"sheets pull: {changed} rows updated from {ACTIONS_TAB}")


def brief(path: Path) -> None:
    gc = _client()
    if gc is None:
        print("sheets brief: skipped (no Google credentials set)")
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    ws = _tab(_sheet(gc), BRIEF_TAB, cols=2)
    ws.clear()
    ws.update([[ln] for ln in lines] or [[""]], "A1", value_input_option="RAW")
    print(f"sheets brief: {len(lines)} lines -> {BRIEF_TAB}")


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["pull"]:
        pull()
    elif cmd == ["push"]:
        push()
    elif cmd == ["brief"] and len(sys.argv) > 2:
        brief(Path(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(1)
