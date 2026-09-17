"""Two-way mirror between tracker/actions.xlsx and a Google Sheet.

The repo's Excel file stays the source of truth for the scripts. The Google Sheet is
where you look at and edit items. Two commands:

  python scripts/sheets.py pull            # copy edits made in the Sheet back into actions.xlsx
                                           # and log each changed cell to memory/corrections.jsonl
  python scripts/sheets.py push            # overwrite the "Actions" tab from actions.xlsx (formatted:
                                           # frozen header, colour by status/priority/overdue, dropdowns),
                                           # a "Dashboard" tab of live formulas over it, and the
                                           # "Decisions" and "Threads" tabs from memory/*.jsonl
  python scripts/sheets.py brief FILE.md   # write the plan (FILE.json next to FILE.md) into the
                                           # "Morning Brief" tab as a formatted table

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

from common import (COLUMNS, CORRECTIONS, DECISIONS, EFFORTS, GSHEET_ID, OPEN_STATUSES, ORIGINS, THREADS, TYPES,
                    append_jsonl, today)

EDITABLE = ("status", "owner", "due", "priority", "notes", "blocked_by", "project", "task",
            "unblocker", "next_step", "type", "effort")
ACTIONS_TAB = "Actions"
DASHBOARD_TAB = "Dashboard"
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


def _write_table(sh, title: str, values: list[list]):
    ws = _tab(sh, title)
    ws.clear()
    ws.update(values, "A1", value_input_option="RAW")
    ws.freeze(rows=1)
    ws.format("1:1", {"textFormat": {"bold": True}})
    return ws


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


# ----------------------------------------------------------------------------- formatting

def _rgb(hex6: str) -> dict:
    return {"red": int(hex6[0:2], 16) / 255, "green": int(hex6[2:4], 16) / 255, "blue": int(hex6[4:6], 16) / 255}


NAVY, INK, MUTED, LINE = "1F3864", "1F2933", "6B7280", "D9DEE7"
STATUS_STYLE = {"done": ("E2F3E2", "1E7B1E"), "to_verify": ("FFF2CC", "8A6100"), "blocked": ("FDE4D7", "9A3F12"),
                "in_progress": ("E1ECFB", "1C5CAB")}
PRIORITY_STYLE = {"P1": ("FBE3E3", "9B1C1C"), "P2": ("FFF4D6", "8A6100"), "P3": ("EEF1F5", MUTED)}
COL_WIDTH = {"task": 420, "owner": 110, "priority": 70, "status": 95, "due": 90, "project": 130, "team": 120,
             "type": 95, "next_step": 280, "unblocker": 100, "effort": 70, "blocked_by": 150,
             "prerequisites": 190, "notes": 280, "evidence": 320, "basis": 190, "meeting": 180,
             "origin": 80, "updated": 90, "closed": 90, "created": 90, "source": 230, "id": 230}


def _grid(sheet_id: int, r0: int, r1: int | None = None, c0: int = 0, c1: int | None = None) -> dict:
    """0-based, end-exclusive GridRange. None means open-ended."""
    g = {"sheetId": sheet_id, "startRowIndex": r0, "startColumnIndex": c0}
    if r1 is not None:
        g["endRowIndex"] = r1
    if c1 is not None:
        g["endColumnIndex"] = c1
    return g


def _cell_format(bg: str | None = None, fg: str | None = None, bold: bool | None = None, italic: bool | None = None,
                 size: int | None = None, wrap: bool = False, valign: str | None = None, halign: str | None = None) -> dict:
    fmt: dict = {"textFormat": {"fontFamily": "Arial"}}
    if bg:
        fmt["backgroundColor"] = _rgb(bg)
    if fg:
        fmt["textFormat"]["foregroundColor"] = _rgb(fg)
    if bold is not None:
        fmt["textFormat"]["bold"] = bold
    if italic is not None:
        fmt["textFormat"]["italic"] = italic
    if size:
        fmt["textFormat"]["fontSize"] = size
    if wrap:
        fmt["wrapStrategy"] = "WRAP"
    if valign:
        fmt["verticalAlignment"] = valign
    if halign:
        fmt["horizontalAlignment"] = halign
    return fmt


def _repeat(rng: dict, fmt: dict) -> dict:
    return {"repeatCell": {"range": rng, "cell": {"userEnteredFormat": fmt}, "fields": "userEnteredFormat"}}


def _rule(rng: dict, formula: str, bg: str | None = None, fg: str | None = None, bold: bool = False) -> dict:
    fmt: dict = {}
    if bg:
        fmt["backgroundColor"] = _rgb(bg)
    if fg or bold:
        fmt["textFormat"] = {}
        if fg:
            fmt["textFormat"]["foregroundColor"] = _rgb(fg)
        if bold:
            fmt["textFormat"]["bold"] = True
    return {"addConditionalFormatRule": {"index": 0, "rule": {
        "ranges": [rng], "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]},
                                         "format": fmt}}}}


def _reset_requests(sh, ws) -> list[dict]:
    """Requests that strip a tab back to plain cells: merges, conditional rules, embedded charts."""
    reqs = [{"unmergeCells": {"range": {"sheetId": ws.id}}}]
    meta = next((m for m in sh.fetch_sheet_metadata().get("sheets", []) if m["properties"]["sheetId"] == ws.id), {})
    for _ in meta.get("conditionalFormats", []):
        reqs.append({"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}})
    for chart in meta.get("charts", []):
        reqs.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})
    return reqs


def _col_letter(name: str) -> str:
    import string
    i = COLUMNS.index(name)
    return string.ascii_uppercase[i] if i < 26 else "A" + string.ascii_uppercase[i - 26]


def actions_format_requests(sheet_id: int, n_rows: int) -> list[dict]:
    """Header band, frozen header + task column, widths, wrapped text, colour rules that follow
    edits, and dropdowns for the enumerated columns. Pure, so selftest can inspect it."""
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {
                "frozenRowCount": 1, "frozenColumnCount": 1}}, "fields": "gridProperties(frozenRowCount,frozenColumnCount)"}},
            _repeat(_grid(sheet_id, 1), _cell_format(fg=INK, size=10, wrap=True, valign="TOP")),
            _repeat(_grid(sheet_id, 0, 1), _cell_format(bg=NAVY, fg="FFFFFF", bold=True, size=10, wrap=True, valign="MIDDLE")),
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
                                           "properties": {"pixelSize": 34}, "fields": "pixelSize"}}]
    for i, col in enumerate(COLUMNS):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": COL_WIDTH.get(col, 100)}, "fields": "pixelSize"}})
        if col in ("priority", "status", "due", "effort", "origin", "updated", "closed", "created"):
            reqs.append(_repeat(_grid(sheet_id, 1, None, i, i + 1), _cell_format(fg=INK, size=10, wrap=True, valign="TOP", halign="CENTER")))
    st, pr, du = (f"${_col_letter(c)}2" for c in ("status", "priority", "due"))
    closed = f'OR({st}="done",{st}="rejected")'
    ci = {c: COLUMNS.index(c) for c in ("status", "priority", "due")}
    # Each rule is inserted at index 0 and Sheets applies the first rule that is true, so append in
    # reverse precedence: the row-wide grey for closed items first, the status colours last.
    reqs.append(_rule(_grid(sheet_id, 1, None, 0, len(COLUMNS)), f"={closed}", fg=MUTED))
    reqs.append(_rule(_grid(sheet_id, 1, None, ci["due"], ci["due"] + 1),
                      f'=IFERROR(AND(DATEVALUE({du})=TODAY(),NOT({closed})),FALSE)', fg="8A6100", bold=True))
    reqs.append(_rule(_grid(sheet_id, 1, None, ci["due"], ci["due"] + 1),
                      f'=IFERROR(AND(DATEVALUE({du})<TODAY(),NOT({closed})),FALSE)', fg="C0262A", bold=True))
    for value, (bg, fg) in PRIORITY_STYLE.items():
        reqs.append(_rule(_grid(sheet_id, 1, None, ci["priority"], ci["priority"] + 1),
                          f'=AND({pr}="{value}",NOT({closed}))', bg=bg, fg=fg, bold=True))
    for value, (bg, fg) in STATUS_STYLE.items():
        reqs.append(_rule(_grid(sheet_id, 1, None, ci["status"], ci["status"] + 1), f'={st}="{value}"', bg=bg, fg=fg, bold=True))
    for col, values in (("status", sorted(OPEN_STATUSES) + ["done", "rejected"]), ("priority", ["P1", "P2", "P3"]),
                        ("type", TYPES), ("effort", EFFORTS), ("origin", ORIGINS)):
        i = COLUMNS.index(col)
        reqs.append({"setDataValidation": {"range": _grid(sheet_id, 1, None, i, i + 1), "rule": {
            "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in values]},
            "showCustomUi": True, "strict": False}}})
    return reqs


def dashboard_values(rows: list[dict]) -> tuple[list[list[str]], dict]:
    """Cells for the Dashboard tab: live COUNTIFS over the Actions tab, so they follow your edits
    without waiting for the next run. Returns (values, layout) where layout names the row indexes
    the formatter and the chart need. Pure, so selftest can exercise it."""
    A = ACTIONS_TAB
    S, P, D, O, J = (f"{A}!${_col_letter(c)}$2:${_col_letter(c)}" for c in ("status", "priority", "due", "owner", "project"))
    is_open = f'{S},"<>done",{S},"<>rejected",{S},"<>"'
    overdue = f'ARRAYFORMULA(SUM(({D}<>"")*(IFERROR(DATEVALUE({D}),9^9)<TODAY())*({S}<>"done")*({S}<>"rejected")*({S}<>"")))'
    due_today = f'ARRAYFORMULA(SUM(({D}<>"")*(IFERROR(DATEVALUE({D}),0)=TODAY())*({S}<>"done")*({S}<>"rejected")*({S}<>"")))'
    next7 = (f'ARRAYFORMULA(SUM(({D}<>"")*(IFERROR(DATEVALUE({D}),0)>TODAY())*(IFERROR(DATEVALUE({D}),0)<=TODAY()+7)'
             f'*({S}<>"done")*({S}<>"rejected")*({S}<>"")))')
    later = f'ARRAYFORMULA(SUM(({D}<>"")*(IFERROR(DATEVALUE({D}),0)>TODAY()+7)*({S}<>"done")*({S}<>"rejected")*({S}<>"")))'
    no_date = f'COUNTIFS({is_open},{D},"")'
    values = [["Action tracker"],
              ["Live view of the Actions tab: edit a status or priority there and these numbers follow. Rebuilt on every run."],
              [],
              ["Open items", "P1 open", "Overdue", "Due today", "To verify", "Blocked", "Done"],
              [f"=COUNTIFS({is_open})", f'=COUNTIFS({is_open},{P},"P1")', f"={overdue}", f"={due_today}",
               f'=COUNTIF({S},"to_verify")', f'=COUNTIF({S},"blocked")', f'=COUNTIF({S},"done")'],
              [],
              ["Open items by owner"],
              ["Owner", "P1", "P2", "P3", "Open", "Overdue"]]
    owners = sorted({r["owner"] or "Unassigned" for r in rows if r["status"] in OPEN_STATUSES},
                    key=lambda o: -sum(1 for r in rows if (r["owner"] or "Unassigned") == o and r["status"] in OPEN_STATUSES))
    owner_first = len(values) + 1
    for o in owners:
        n = len(values) + 1
        who = f'{O},$A{n}'
        values.append([o, f'=COUNTIFS({is_open},{who},{P},"P1")', f'=COUNTIFS({is_open},{who},{P},"P2")',
                       f'=COUNTIFS({is_open},{who},{P},"P3")', f"=COUNTIFS({is_open},{who})",
                       f'=ARRAYFORMULA(SUM(({O}=$A{n})*({D}<>"")*(IFERROR(DATEVALUE({D}),9^9)<TODAY())'
                       f'*({S}<>"done")*({S}<>"rejected")*({S}<>"")))'])
    owner_last = len(values)
    if owners:
        n = len(values) + 1
        values.append(["Total"] + [f"=SUM({c}{owner_first}:{c}{owner_last})" for c in "BCDEF"])
    values += [[], ["When is it due"], ["Horizon", "Items"],
               ["Overdue", f"={overdue}"], ["Due today", f"={due_today}"], ["Next 7 days", f"={next7}"],
               ["Later", f"={later}"], ["No date", f"={no_date}"], [], ["By project"], ["Project", "Open", "P1", "Overdue", "Done"]]
    horizon_first = len(values) - 8
    project_first = len(values) + 1
    for pj in sorted({r["project"] or "unassigned" for r in rows}):
        n = len(values) + 1
        values.append([pj, f"=COUNTIFS({is_open},{J},$A{n})", f'=COUNTIFS({is_open},{J},$A{n},{P},"P1")',
                       f'=ARRAYFORMULA(SUM(({J}=$A{n})*({D}<>"")*(IFERROR(DATEVALUE({D}),9^9)<TODAY())'
                       f'*({S}<>"done")*({S}<>"rejected")*({S}<>"")))', f'=COUNTIFS({J},$A{n},{S},"done")'])
    values += [[], ["Colour key on Actions: red priority = P1; amber status = reported complete, waiting for your "
                    "confirmation (to_verify); orange = blocked; green = done; a red due date is overdue. Only you set done."]]
    layout = {"owner_header": 7, "owner_first": owner_first - 1, "owner_last": owner_last, "total": owner_last,
              "horizon_header": horizon_first - 1, "project_header": project_first - 2, "n_rows": len(values)}
    return values, layout


def dashboard_format_requests(sheet_id: int, layout: dict) -> list[dict]:
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"hideGridlines": True}},
                                       "fields": "gridProperties.hideGridlines"}},
            _repeat(_grid(sheet_id, 0), _cell_format(fg=INK, size=10)),
            _repeat(_grid(sheet_id, 0, 1), _cell_format(fg=NAVY, bold=True, size=18)),
            _repeat(_grid(sheet_id, 1, 2), _cell_format(fg=MUTED, size=9)),
            _repeat(_grid(sheet_id, 3, 4, 0, 7), _cell_format(bg="F3F6FB", fg=MUTED, bold=True, size=8, halign="CENTER")),
            _repeat(_grid(sheet_id, 4, 5, 0, 7), _cell_format(bg="F3F6FB", fg=NAVY, bold=True, size=20, halign="CENTER")),
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 4, "endIndex": 5},
                                           "properties": {"pixelSize": 44}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                                           "properties": {"pixelSize": 170}, "fields": "pixelSize"}},
            {"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 7},
                                           "properties": {"pixelSize": 90}, "fields": "pixelSize"}},
            _rule(_grid(sheet_id, 4, 5, 1, 3), "=B5>0", fg="C0262A")]
    for key, width in (("owner_header", 6), ("horizon_header", 2), ("project_header", 5)):
        r = layout[key]
        reqs.append(_repeat(_grid(sheet_id, r - 1, r, 0, 1), _cell_format(fg=NAVY, bold=True, size=12)))
        reqs.append(_repeat(_grid(sheet_id, r, r + 1, 0, width), _cell_format(bg=NAVY, fg="FFFFFF", bold=True, size=9)))
    if layout["owner_last"] > layout["owner_first"]:
        reqs.append(_repeat(_grid(sheet_id, layout["total"], layout["total"] + 1, 0, 6), _cell_format(bold=True, fg=INK)))
        reqs.append({"addChart": {"chart": {"spec": {
            "title": "Open items by owner and priority", "titleTextFormat": {"fontFamily": "Arial", "bold": True},
            "basicChart": {"chartType": "BAR", "stackedType": "STACKED", "legendPosition": "BOTTOM_LEGEND", "headerCount": 1,
                           "axis": [{"position": "BOTTOM_AXIS", "title": "open items"}, {"position": "LEFT_AXIS", "title": ""}],
                           "domains": [{"domain": {"sourceRange": {"sources": [_grid(sheet_id, layout["owner_header"], layout["owner_last"], 0, 1)]}}}],
                           "series": [{"series": {"sourceRange": {"sources": [_grid(sheet_id, layout["owner_header"], layout["owner_last"], c, c + 1)]}},
                                       "targetAxis": "BOTTOM_AXIS", "color": _rgb(colour)}
                                      for c, colour in ((1, "1C5CAB"), (2, "5598E7"), (3, "B7D3F6"))]}},
            "position": {"overlayPosition": {"anchorCell": {"sheetId": sheet_id, "rowIndex": layout["owner_header"] - 1, "columnIndex": 8},
                                             "widthPixels": 620, "heightPixels": 360}}}}})
    reqs.append(_repeat(_grid(sheet_id, layout["n_rows"] - 1, layout["n_rows"], 0, 1), _cell_format(fg=MUTED, size=9, wrap=False)))
    return reqs


def brief_format_requests(sheet_id: int, kinds: list[str]) -> list[dict]:
    """Formats the Morning Brief tab from the row kinds brief.sheet_rows() produced."""
    n_cols = 8
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 3, "hideGridlines": True}},
                                       "fields": "gridProperties(frozenRowCount,hideGridlines)"}},
            _repeat(_grid(sheet_id, 0), _cell_format(fg=INK, size=10, wrap=True, valign="TOP"))]
    for i, w in enumerate((150, 120, 420, 70, 90, 170, 320, 230)):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                   "properties": {"pixelSize": w}, "fields": "pixelSize"}})
    for i, kind in enumerate(kinds):
        if kind == "title":
            reqs.append({"mergeCells": {"range": _grid(sheet_id, i, i + 1, 0, n_cols), "mergeType": "MERGE_ALL"}})
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 0, n_cols), _cell_format(bg=NAVY, fg="FFFFFF", bold=True, size=16, valign="MIDDLE")))
            reqs.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1},
                                                       "properties": {"pixelSize": 44}, "fields": "pixelSize"}})
        elif kind == "subtitle":
            reqs.append({"mergeCells": {"range": _grid(sheet_id, i, i + 1, 0, n_cols), "mergeType": "MERGE_ALL"}})
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 0, n_cols), _cell_format(bg="2A4F8F", fg="FFFFFF", size=10, valign="MIDDLE")))
        elif kind == "header":
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 0, n_cols), _cell_format(bg="EEF1F5", fg=MUTED, bold=True, size=9)))
        elif kind == "section":
            reqs.append({"mergeCells": {"range": _grid(sheet_id, i, i + 1, 0, n_cols), "mergeType": "MERGE_ALL"}})
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 0, n_cols), _cell_format(bg="E1ECFB", fg=NAVY, bold=True, size=11, valign="MIDDLE")))
            reqs.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": i, "endIndex": i + 1},
                                                       "properties": {"pixelSize": 30}, "fields": "pixelSize"}})
        elif kind in ("sub", "empty"):
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 0, n_cols), _cell_format(fg=MUTED, italic=True, size=10, wrap=True, valign="TOP")))
        elif kind == "item":
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 1, 2), _cell_format(fg=INK, bold=True, size=10, wrap=True, valign="TOP")))
            reqs.append(_repeat(_grid(sheet_id, i, i + 1, 7, 8), _cell_format(fg=MUTED, size=8, wrap=True, valign="TOP")))
    for value, (bg, fg) in PRIORITY_STYLE.items():
        reqs.append(_rule(_grid(sheet_id, 3, None, 3, 4), f'=$D4="{value}"', bg=bg, fg=fg, bold=True))
    reqs.append(_rule(_grid(sheet_id, 3, None, 5, 6), '=REGEXMATCH($F4,"overdue")', fg="C0262A", bold=True))
    reqs.append(_rule(_grid(sheet_id, 3, None, 5, 6), '=REGEXMATCH($F4,"due today|to verify")', fg="8A6100", bold=True))
    reqs.append(_rule(_grid(sheet_id, 3, None, 5, 6), '=REGEXMATCH($F4,"blocked")', fg="9A3F12", bold=True))
    return reqs


def _apply(sh, ws, requests: list[dict], what: str) -> None:
    """Formatting never blocks the data push: a failure is printed and the plain values stand."""
    try:
        sh.batch_update({"requests": _reset_requests(sh, ws) + requests})
    except Exception as e:
        print(f"sheets {what}: formatting skipped ({e})")


def push() -> None:
    gc = _client()
    if gc is None:
        print("sheets push: skipped (no Google credentials set)")
        return
    import tracker

    rows = tracker._sorted(tracker.load_rows())
    sh = _sheet(gc)
    ws = _write_table(sh, ACTIONS_TAB, [COLUMNS] + [[r.get(c, "") for c in COLUMNS] for r in rows])
    print(f"sheets push: {len(rows)} rows -> {ACTIONS_TAB}")
    _apply(sh, ws, actions_format_requests(ws.id, len(rows)), "push")
    values, layout = dashboard_values(rows)
    ws = _tab(sh, DASHBOARD_TAB, cols=20)
    ws.clear()
    ws.update(values, "A1", value_input_option="USER_ENTERED")
    _apply(sh, ws, dashboard_format_requests(ws.id, layout), "dashboard")
    print(f"sheets push: {layout['n_rows']} rows -> {DASHBOARD_TAB}")
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
    sh = _sheet(gc)
    plan_path = path.with_suffix(".json")
    if plan_path.exists():
        import brief as brief_mod

        rows = brief_mod.sheet_rows(json.loads(plan_path.read_text(encoding="utf-8")))
        kinds = [k for k, _ in rows]
        values = [cells + [""] * (8 - len(cells)) for _, cells in rows]
        ws = _tab(sh, BRIEF_TAB, cols=8)
        if ws.col_count < 8:   # the tab used to be a single column of markdown lines
            ws.resize(cols=8)
        ws.clear()
        ws.update(values, "A1", value_input_option="RAW")
        _apply(sh, ws, brief_format_requests(ws.id, kinds), "brief")
        print(f"sheets brief: {len(values)} rows -> {BRIEF_TAB}")
        return
    lines = path.read_text(encoding="utf-8").splitlines()   # old briefs without a plan file
    ws = _tab(sh, BRIEF_TAB, cols=2)
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
