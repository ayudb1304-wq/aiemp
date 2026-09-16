"""Two-way mirror between tracker/actions.xlsx and a Google Sheet.

The repo's Excel file stays the source of truth for the scripts. The Google Sheet is
where you look at and edit items. Two commands:

  python scripts/sheets.py pull            # copy status/owner/due/priority/notes edits
                                           # made in the Sheet back into actions.xlsx
  python scripts/sheets.py push            # overwrite the "Actions" tab from actions.xlsx
  python scripts/sheets.py brief FILE.md   # write a brief into the "Morning Brief" tab

Needs env GOOGLE_SERVICE_ACCOUNT_JSON (the service account key file contents). Optional
env GSHEET_ID overrides the default spreadsheet. If the key is not set, every command
prints "skipped" and exits 0, so local runs and workflows without the secret still work.
"""
import json
import os
import sys
from pathlib import Path

from common import COLUMNS, GSHEET_ID, today

EDITABLE = ("status", "owner", "due", "priority", "notes", "blocked_by")
ACTIONS_TAB = "Actions"
BRIEF_TAB = "Morning Brief"


def _client():
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    import gspread

    return gspread.service_account_from_dict(json.loads(raw))


def _sheet(gc):
    return gc.open_by_key(os.environ.get("GSHEET_ID") or GSHEET_ID)


def _tab(sh, title: str, rows: int = 1000, cols: int = 20):
    for ws in sh.worksheets():
        if ws.title == title:
            return ws
    # Reuse the default empty first tab instead of leaving a stray "Sheet1".
    first = sh.worksheets()[0]
    if len(sh.worksheets()) == 1 and not any(first.row_values(1)):
        first.update_title(title)
        return first
    return sh.add_worksheet(title=title, rows=rows, cols=cols)


def push() -> None:
    gc = _client()
    if gc is None:
        print("sheets push: skipped (GOOGLE_SERVICE_ACCOUNT_JSON not set)")
        return
    import tracker

    rows = tracker.load_rows()
    rows = sorted(rows, key=lambda r: (r["status"] not in tracker.OPEN_STATUSES,
                                       {"P1": 0, "P2": 1, "P3": 2}.get(r["priority"], 9),
                                       r["due"] or "9999"))
    values = [COLUMNS] + [[r.get(c, "") for c in COLUMNS] for r in rows]
    ws = _tab(_sheet(gc), ACTIONS_TAB)
    ws.clear()
    ws.update(values, "A1", value_input_option="RAW")
    ws.freeze(rows=1)
    ws.format("1:1", {"textFormat": {"bold": True}})
    print(f"sheets push: {len(rows)} rows -> {ACTIONS_TAB}")


def pull() -> None:
    gc = _client()
    if gc is None:
        print("sheets pull: skipped (GOOGLE_SERVICE_ACCOUNT_JSON not set)")
        return
    import tracker

    ws = _tab(_sheet(gc), ACTIONS_TAB)
    records = ws.get_all_records(default_blank="")
    if not records:
        print("sheets pull: sheet is empty, nothing to pull")
        return
    rows = tracker.load_rows()
    by_id = {r["id"]: r for r in rows}
    changed = 0
    for rec in records:
        r = by_id.get(str(rec.get("id", "")).strip())
        if not r:
            continue
        diff = {k: str(rec.get(k, "")).strip() for k in EDITABLE
                if str(rec.get(k, "")).strip() != str(r.get(k, "")).strip()}
        if diff:
            r.update(diff)
            r["updated"] = today()
            changed += 1
    if changed:
        tracker.save_rows(rows)
    print(f"sheets pull: {changed} rows updated from {ACTIONS_TAB}")


def brief(path: Path) -> None:
    gc = _client()
    if gc is None:
        print("sheets brief: skipped (GOOGLE_SERVICE_ACCOUNT_JSON not set)")
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
