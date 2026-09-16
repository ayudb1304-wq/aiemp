"""Excel tracker: the single source of truth for action items.

Usage:
  python scripts/tracker.py cards/2026-09-16-platform-sync.json [...]
  python scripts/tracker.py --close <id>          # mark done from the CLI
"""
import json
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from common import COLUMNS, OPEN_STATUSES, TRACKER, parse_date, slug, today

FILL_P1 = PatternFill("solid", fgColor="F8D7DA")
FILL_OVERDUE = PatternFill("solid", fgColor="FFE5B4")
FILL_DONE = PatternFill("solid", fgColor="E2EFDA")
FILL_HEADER = PatternFill("solid", fgColor="1F3864")
WIDTHS = {"id": 24, "created": 11, "meeting": 18, "team": 12, "owner": 12,
          "task": 55, "due": 11, "priority": 8, "status": 11, "blocked_by": 18,
          "evidence": 45, "source": 28, "updated": 11, "notes": 35}


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
        for k in ("created", "due", "updated"):
            d = parse_date(row.get(k))
            row[k] = d.isoformat() if d else ""
        rows.append({c: (row.get(c) or "") for c in COLUMNS})
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
        ws.append([r.get(c, "") for c in COLUMNS])
        row_cells = ws[ws.max_row]
        due = parse_date(r["due"])
        if r["status"] not in OPEN_STATUSES:
            fill = FILL_DONE
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


def open_items(rows: list[dict] | None = None) -> list[dict]:
    rows = load_rows() if rows is None else rows
    return [r for r in rows if r["status"] in OPEN_STATUSES]


def _next_id(rows: list[dict], meeting_date: str, team: str) -> str:
    prefix = f"{meeting_date}-{slug(team)}-"
    n = sum(1 for r in rows if r["id"].startswith(prefix)) + 1
    return f"{prefix}{n:02d}"


def upsert(cards: list[dict], meeting: str, meeting_date: str, source: str) -> tuple[int, int]:
    """Merge extracted cards into the tracker. Returns (created, updated)."""
    rows = load_rows()
    by_id = {r["id"]: r for r in rows}
    created = updated = 0
    for c in cards:
        match = c.get("matches_existing_id")
        if match and match in by_id:
            r = by_id[match]
            for k in ("due", "priority", "status", "owner", "blocked_by"):
                if c.get(k):
                    r[k] = c[k]
            note = c.get("update_note") or f"Mentioned again in {meeting}"
            r["notes"] = (r["notes"] + " | " if r["notes"] else "") + f"{meeting_date}: {note}"
            r["updated"] = today()
            updated += 1
        else:
            row = {col: "" for col in COLUMNS}
            row.update({
                "id": _next_id(rows, meeting_date, c.get("team", "")),
                "created": meeting_date,
                "meeting": meeting,
                "team": c.get("team", ""),
                "owner": c.get("owner", ""),
                "task": c.get("task", ""),
                "due": c.get("due") or "",
                "priority": c.get("priority", "P2"),
                "status": c.get("status") or "open",
                "blocked_by": c.get("blocked_by") or "",
                "evidence": c.get("evidence", ""),
                "source": source,
                "updated": today(),
            })
            rows.append(row)
            by_id[row["id"]] = row
            created += 1
    save_rows(rows)
    return created, updated


def close(item_id: str) -> None:
    rows = load_rows()
    for r in rows:
        if r["id"] == item_id:
            r["status"], r["updated"] = "done", today()
    save_rows(rows)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--close"]:
        close(args[1])
        print(f"closed {args[1]}")
        sys.exit()
    for p in args:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        c, u = upsert(data["cards"], data["meeting"], data["date"], data["source"])
        print(f"{p}: {c} created, {u} updated")
