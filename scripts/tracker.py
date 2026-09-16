"""Excel tracker: the single source of truth for action items.

Usage:
  python scripts/tracker.py cards/2026-09-16-intelligent-tracker-sync.json [...]
  python scripts/tracker.py --close <id>          # mark done from the CLI (a human action)

Rules the code enforces:
- the agent never sets `done`; a card that says done becomes `to_verify`
- a due date change is recorded in notes as "due <old> -> <new>" so slips can be counted
- re-running the same cards file creates no duplicates (same source + same task text)
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import COLUMNS, OPEN_STATUSES, TRACKER, UNASSIGNED, parse_date, slug, today

FILL_P1 = PatternFill("solid", fgColor="F8D7DA")
FILL_OVERDUE = PatternFill("solid", fgColor="FFE5B4")
FILL_DONE = PatternFill("solid", fgColor="E2EFDA")
FILL_VERIFY = PatternFill("solid", fgColor="FFF2CC")
FILL_HEADER = PatternFill("solid", fgColor="1F3864")
WIDTHS = {"id": 30, "created": 11, "meeting": 18, "team": 12, "project": 16, "owner": 12,
          "task": 55, "due": 11, "priority": 8, "status": 11, "blocked_by": 18,
          "evidence": 45, "source": 28, "updated": 11, "notes": 35, "origin": 10, "type": 12,
          "next_step": 40, "prerequisites": 30, "unblocker": 12, "effort": 9, "basis": 30,
          "closed": 11}
ADVISOR_FIELDS = ("type", "next_step", "prerequisites", "unblocker", "effort", "basis")
DUE_MARK = re.compile(r"due (\d{4}-\d{2}-\d{2}|none) -> (\d{4}-\d{2}-\d{2}|none)")


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x)
    return str(v)


def load_rows() -> list[dict]:
    if not TRACKER.exists():
        return []
    ws = load_workbook(TRACKER).active
    headers = [c.value for c in ws[1]]
    rows = []
    for values in ws.iter_rows(min_row=2, values_only=True):
        if not any(values):
            continue
        row = dict(zip(headers, values))
        for k in ("created", "due", "updated", "closed"):
            d = parse_date(row.get(k))
            row[k] = d.isoformat() if d else ""
        rows.append({c: _s(row.get(c)) for c in COLUMNS})
    return rows


def save_rows(rows: list[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Actions"
    ws.append(COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = FILL_HEADER
    order = {"P1": 0, "P2": 1, "P3": 2}
    rows = sorted(rows, key=lambda r: (r["status"] not in OPEN_STATUSES,
                                       order.get(r["priority"], 9), r["due"] or "9999"))
    t = date.today()
    for r in rows:
        if r.get("status") == "done" and not r.get("closed"):
            r["closed"] = today()
        ws.append([_s(r.get(c, "")) for c in COLUMNS])
        row_cells = ws[ws.max_row]
        due = parse_date(r["due"])
        if r["status"] not in OPEN_STATUSES:
            fill = FILL_DONE
        elif r["status"] == "to_verify":
            fill = FILL_VERIFY
        elif due and due < t:
            fill = FILL_OVERDUE
        elif r["priority"] == "P1":
            fill = FILL_P1
        else:
            fill = None
        for c in row_cells:
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if fill:
                c.fill = fill
    for i, col in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(col, 14)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    TRACKER.parent.mkdir(parents=True, exist_ok=True)
    wb.save(TRACKER)


def open_items(rows: list[dict] | None = None, project: str | None = None) -> list[dict]:
    rows = load_rows() if rows is None else rows
    out = [r for r in rows if r["status"] in OPEN_STATUSES]
    if project:
        out = [r for r in out if slug(r.get("project")) == slug(project)]
    return out


def _next_id(rows: list[dict], meeting_date: str, project: str) -> str:
    prefix = f"{meeting_date}-{slug(project or UNASSIGNED)}-"
    n = sum(1 for r in rows if r["id"].startswith(prefix)) + 1
    return f"{prefix}{n:02d}"


def _add_note(r: dict, meeting_date: str, note: str) -> None:
    r["notes"] = (r["notes"] + " | " if r["notes"] else "") + f"{meeting_date}: {note}"


def slip_count(r: dict) -> tuple[int, str]:
    """(number of due changes, first due) parsed from the notes markers."""
    marks = DUE_MARK.findall(r.get("notes") or "")
    first = next((m[0] for m in marks if m[0] != "none"), r.get("due") or "")
    return len(marks), first


def upsert(cards: list[dict], meeting: str, meeting_date: str, source: str,
           project: str | None = None, origin: str = "planned") -> tuple[list[str], list[str]]:
    """Merge extracted cards into the tracker. Returns (created_ids, updated_ids)."""
    rows = load_rows()
    by_id = {r["id"]: r for r in rows}
    created, updated = [], []
    for c in cards:
        match = c.get("matches_existing_id")
        status = c.get("status") or "open"
        if status == "done":  # the agent never closes an item
            status = "to_verify"
        if match and match in by_id:
            r = by_id[match]
            note = c.get("update_note") or f"Mentioned again in {meeting}"
            if status == "to_verify" and c.get("evidence"):
                note = f"{note} [{c['evidence']}]"
            if f"{meeting_date}: {note}" in r["notes"]:
                continue  # same file processed again: this update is already applied
            if c.get("due") and c["due"] != r["due"]:
                _add_note(r, meeting_date, f"due {r['due'] or 'none'} -> {c['due']}")
                r["due"] = c["due"]
            for k in ("priority", "owner", "blocked_by"):
                if c.get(k):
                    r[k] = _s(c[k])
            if c.get("status"):
                r["status"] = status
            if c.get("basis") and _s(c["basis"]) != r.get("basis"):
                for k in ADVISOR_FIELDS:  # refresh the advice when its basis changed
                    if c.get(k) is not None:
                        r[k] = _s(c[k])
            elif c.get("type") and not r.get("type"):
                r["type"] = _s(c["type"])
            _add_note(r, meeting_date, note)
            r["updated"] = today()
            updated.append(r["id"])
            continue
        dup = next((r for r in rows if r["source"] == source and r["task"] == c.get("task", "")), None)
        if dup:  # same file processed again: idempotent
            continue
        proj = c.get("project") or project or UNASSIGNED
        row = {col: "" for col in COLUMNS}
        row.update({
            "id": _next_id(rows, meeting_date, proj),
            "created": meeting_date,
            "meeting": meeting,
            "team": c.get("team", ""),
            "project": slug(proj),
            "owner": c.get("owner", ""),
            "task": c.get("task", ""),
            "due": c.get("due") or "",
            "priority": c.get("priority", "P2"),
            "status": status,
            "blocked_by": _s(c.get("blocked_by")),
            "evidence": c.get("evidence", ""),
            "source": source,
            "updated": today(),
            "origin": c.get("origin") or origin,
        })
        for k in ADVISOR_FIELDS:
            row[k] = _s(c.get(k))
        if status == "to_verify" and c.get("evidence"):
            _add_note(row, meeting_date, f"Reported complete [{c['evidence']}]")
        rows.append(row)
        by_id[row["id"]] = row
        created.append(row["id"])
    save_rows(rows)
    return created, updated


def close(item_id: str) -> None:
    rows = load_rows()
    for r in rows:
        if r["id"] == item_id:
            r["status"], r["updated"], r["closed"] = "done", today(), today()
    save_rows(rows)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--close"]:
        close(args[1])
        print(f"closed {args[1]}")
        sys.exit()
    for p in args:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        c, u = upsert(data["cards"], data["meeting"], data["date"], data["source"],
                      project=data.get("project"), origin=data.get("origin", "planned"))
        print(f"{p}: {len(c)} created, {len(u)} updated")
