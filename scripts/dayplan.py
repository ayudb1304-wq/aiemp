"""Put today's items on your Google Calendar as time blocks.

A nicer to-do list, nothing more for now: each of your open items for the day becomes a block of
5 to 60 minutes on a dedicated calendar, with a popup reminder, so the day has something to hold
it. Lunch and the walk are protected; items small enough to do on a phone (5 minutes, a
confirmation or a message) may sit inside the walk.

  python scripts/dayplan.py                 # plan today and write the blocks to the calendar
  python scripts/dayplan.py --dry-run       # print the plan only
  python scripts/dayplan.py --date 2026-09-18

What goes on the calendar: items you own that are due today, overdue, or P1, plus every item
waiting for your confirmation (to_verify). Blocked items are skipped. Blocks are placed in order
of urgency from the start of the working day, around the protected windows and around anything
already busy on the calendars listed in context/dayplan.json.

Re-running the same day is safe: a block that already exists on the calendar is left exactly
where you put it (moving it is your call), a block whose item has since closed is deleted, and
only new items get new blocks. Every action is appended to memory/plans.jsonl.

Config lives in context/dayplan.json. Calendar access needs the same credentials as sheets.py
(GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_OAUTH_TOKEN_JSON) plus env GCAL_ID or calendar_id in the
config. Without them the script prints the plan and exits 0.
"""
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from common import CONTEXT_DIR, MEMORY, OPEN_STATUSES, append_jsonl, my_name, today

CONFIG = CONTEXT_DIR / "dayplan.json"
PLANS = MEMORY / "plans.jsonl"
CAL_API = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

DEFAULT_CONFIG = {
    "timezone": "Asia/Kolkata",
    "work_start": "09:00",
    "work_end": "18:30",
    "slot_min_minutes": 5,
    "slot_max_minutes": 60,
    "gap_minutes": 10,
    "protected": [
        {"name": "Lunch", "start": "13:00", "end": "15:00"},
        {"name": "Walk", "start": "17:00", "end": "17:30", "phone_ok": True, "phone_max_minutes": 5,
         "show_on_calendar": True},
    ],
    "calendar_id": "",
    "busy_calendars": [],
}
# An item whose text says it belongs in the walk goes there even if it is longer than a phone task,
# trimmed to whatever of the walk is left. "Brainstorm startup ideas during the walk" qualifies.
WALK_HINT = re.compile(r"\b(during|on|in) (the|my) walk\b|#walk\b", re.I)
WINDOW_PREFIX = "window:"
EFFORT_MINUTES = {"15m": 15, "1h": 60, "half-day": 60, "day+": 60}
TYPE_MINUTES = {"communicate": 15, "coordinate": 15, "decide": 30, "review": 45, "build": 60, "other": 30}
PHONE_TYPES = {"communicate", "coordinate"}
COLOR = {"P1": "11", "P2": "5", "P3": "8"}  # Google Calendar colorIds: tomato, banana, graphite
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}


# ----------------------------------------------------------------------------- config

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    return cfg


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# ----------------------------------------------------------------------------- pure planning

def _parse_date(s) -> date | None:
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def minutes_for(item: dict, cfg: dict) -> int:
    """How long a block to give an item, clamped to the configured slot range."""
    if item.get("status") == "to_verify":
        m = cfg["slot_min_minutes"]
    elif item.get("effort") in EFFORT_MINUTES:
        m = EFFORT_MINUTES[item["effort"]]
    else:
        m = TYPE_MINUTES.get(item.get("type") or "other", 30)
    return max(cfg["slot_min_minutes"], min(cfg["slot_max_minutes"], m))


def phone_ok(item: dict, minutes: int, cfg: dict) -> bool:
    """Small enough and simple enough to do from a phone during the walk."""
    limit = min((p.get("phone_max_minutes", 5) for p in cfg["protected"] if p.get("phone_ok")), default=0)
    if minutes > limit:
        return False
    return item.get("status") == "to_verify" or (item.get("type") in PHONE_TYPES)


def walk_hint(item: dict) -> bool:
    return bool(WALK_HINT.search(f"{item.get('task', '')} {item.get('notes', '')} {item.get('next_step', '')}"))


def pick_items(rows: list[dict], me: str, t: date) -> list[dict]:
    """My open items for the day, most urgent first. Others' items only when they wait on me."""
    me_l = me.lower()
    out = []
    for r in rows:
        if r.get("status") not in OPEN_STATUSES or r.get("status") == "blocked":
            continue
        mine = (r.get("owner") or "").strip().lower() == me_l
        due = _parse_date(r.get("due"))
        if r.get("status") == "to_verify":
            out.append(r)
        elif mine and (r.get("priority") == "P1" or (due and due <= t)):
            out.append(r)

    def key(r):
        due = _parse_date(r.get("due"))
        overdue_days = (t - due).days if due and due < t else 0
        return (r.get("status") != "to_verify", PRIORITY_ORDER.get(r.get("priority"), 9),
                -overdue_days, due.isoformat() if due else "9999", r.get("id", ""))

    return sorted(out, key=key)


def _windows(cfg: dict, t: date, tz: ZoneInfo, busy: list[tuple[datetime, datetime]]):
    """(desk_windows, phone_windows) as lists of [start, end] datetimes, busy time removed."""
    day_start = datetime.combine(t, _hm(cfg["work_start"]), tz)
    day_end = datetime.combine(t, _hm(cfg["work_end"]), tz)
    blocked = [(datetime.combine(t, _hm(p["start"]), tz), datetime.combine(t, _hm(p["end"]), tz)) for p in cfg["protected"]]
    blocked += [(max(b0, day_start), min(b1, day_end)) for b0, b1 in busy if b1 > day_start and b0 < day_end]
    desk = [[day_start, day_end]]
    for b0, b1 in sorted(blocked):
        nxt = []
        for w0, w1 in desk:
            if b1 <= w0 or b0 >= w1:
                nxt.append([w0, w1])
                continue
            if w0 < b0:
                nxt.append([w0, b0])
            if b1 < w1:
                nxt.append([b1, w1])
        desk = nxt
    phone = [[datetime.combine(t, _hm(p["start"]), tz), datetime.combine(t, _hm(p["end"]), tz)]
             for p in cfg["protected"] if p.get("phone_ok")]
    return desk, phone


def schedule(items: list[dict], cfg: dict, t: date, busy: list[tuple[datetime, datetime]] | None = None) -> dict:
    """Greedy placement. Returns {"blocks": [...], "unplaced": [...]}. Each block:
    {"id", "task", "priority", "start", "end", "minutes", "where": "desk"|"walk"}."""
    tz = ZoneInfo(cfg["timezone"])
    desk, phone = _windows(cfg, t, tz, busy or [])
    gap = timedelta(minutes=cfg["gap_minutes"])
    blocks, unplaced = [], []

    def place(windows, minutes, use_gap):
        need = timedelta(minutes=minutes)
        for w in windows:
            if w[1] - w[0] >= need:
                start = w[0]
                w[0] = start + need + (gap if use_gap else timedelta(0))
                return start, start + need
        return None

    for r in items:
        minutes = minutes_for(r, cfg)
        slot = None
        if walk_hint(r):
            left = max((w[1] - w[0] for w in phone), default=timedelta(0))
            fit = min(minutes, int(left.total_seconds() // 60))
            if fit >= cfg["slot_min_minutes"]:
                minutes = fit
                slot = place(phone, minutes, False)
        elif phone_ok(r, minutes, cfg):
            slot = place(phone, minutes, False)
        where = "walk"
        if slot is None:
            slot = place(desk, minutes, True)
            where = "desk"
        if slot is None:
            unplaced.append(r)
            continue
        blocks.append({"id": r["id"], "task": r["task"], "priority": r.get("priority", ""),
                       "status": r.get("status", ""), "start": slot[0], "end": slot[1],
                       "minutes": minutes, "where": where})
    return {"blocks": blocks, "unplaced": unplaced}


def event_body(block: dict, item: dict, cfg: dict) -> dict:
    tag = "Confirm: " if block["status"] == "to_verify" else ""
    lines = [f"Item {block['id']}", ""]
    for label, key in (("Next step", "next_step"), ("Unblocker", "unblocker"), ("Prerequisites", "prerequisites"),
                       ("Evidence", "evidence"), ("Source", "source")):
        if item.get(key):
            lines.append(f"{label}: {item[key]}")
    lines += ["", "Created by the AI employee. Move it if the time is wrong; it will not be moved back."]
    return {
        "summary": f"[{block['priority']}] {tag}{block['task']}",
        "description": "\n".join(lines),
        "start": {"dateTime": block["start"].isoformat(), "timeZone": cfg["timezone"]},
        "end": {"dateTime": block["end"].isoformat(), "timeZone": cfg["timezone"]},
        "colorId": COLOR.get(block["priority"], "8"),
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 5}]},
        "extendedProperties": {"private": {"aiemp_id": block["id"], "aiemp_date": block["start"].date().isoformat()}},
    }


def window_event(p: dict, t: date, cfg: dict) -> dict:
    """A protected window shown on the calendar as its own event, e.g. the daily walk."""
    tz = ZoneInfo(cfg["timezone"])
    start, end = datetime.combine(t, _hm(p["start"]), tz), datetime.combine(t, _hm(p["end"]), tz)
    return {
        "summary": p["name"],
        "description": "Protected time from context/dayplan.json. The AI employee plans around it.",
        "start": {"dateTime": start.isoformat(), "timeZone": cfg["timezone"]},
        "end": {"dateTime": end.isoformat(), "timeZone": cfg["timezone"]},
        "colorId": "2",  # sage
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 5}]},
        "extendedProperties": {"private": {"aiemp_id": f"{WINDOW_PREFIX}{p['name']}", "aiemp_date": t.isoformat()}},
    }


def stale_ids(have: dict, planned_ids: set) -> list[str]:
    """Our events for the day that no longer correspond to a planned item. Window events stay."""
    return [i for i in have if i not in planned_ids and not i.startswith(WINDOW_PREFIX)]


def render(plan: dict, cfg: dict, t: date) -> str:
    out = [f"## Today on your calendar ({t.strftime('%A %d %b')}, {cfg['timezone']})", ""]
    if not plan["blocks"]:
        out.append("_Nothing of yours is due today._")
    for b in sorted(plan["blocks"], key=lambda b: b["start"]):
        where = " (walk, phone)" if b["where"] == "walk" else ""
        tag = "Confirm: " if b["status"] == "to_verify" else ""
        out.append(f"- {b['start']:%H:%M}-{b['end']:%H:%M} `{b['id']}` {b['priority']} {tag}{b['task']}{where}")
    if plan["unplaced"]:
        out += ["", "Did not fit today:"] + [f"- `{r['id']}` {r['priority']} {r['task']}" for r in plan["unplaced"]]
    return "\n".join(out)


# ----------------------------------------------------------------------------- Google Calendar

def _session():
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    user = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON", "").strip()
    if not sa and not user:
        return None
    from google.auth.transport.requests import AuthorizedSession

    if sa:
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(json.loads(sa), scopes=SCOPES)
    else:
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(json.loads(user), SCOPES)
    return AuthorizedSession(creds)


def _ok(resp):
    """raise_for_status with the reason Google gives, plus the fix for the two usual causes."""
    if resp.ok:
        return resp
    try:
        err = resp.json().get("error", {})
        reason = "; ".join(e.get("reason", "") for e in err.get("errors", [])) or err.get("status", "")
        message = err.get("message", "")
    except ValueError:
        reason, message = "", resp.text[:300]
    hint = ""
    if resp.status_code == 403 and ("accessNotConfigured" in reason or "has not been used" in message
                                    or "is disabled" in message):
        hint = " Enable the Google Calendar API in the Cloud project (APIs & Services > Library) and retry."
    elif resp.status_code in (403, 404):
        hint = (" Check GCAL_ID and share that calendar with the service account email with "
                "'Make changes to events'.")
    raise RuntimeError(f"Google Calendar {resp.status_code} {reason}: {message}.{hint}")


def _day_bounds(t: date, tz: ZoneInfo) -> tuple[str, str]:
    return (datetime.combine(t, time(0, 0), tz).isoformat(),
            datetime.combine(t + timedelta(days=1), time(0, 0), tz).isoformat())


def busy_times(s, calendars: list[str], t: date, tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    if not calendars:
        return []
    lo, hi = _day_bounds(t, tz)
    resp = s.post(f"{CAL_API}/freeBusy", json={"timeMin": lo, "timeMax": hi, "timeZone": cfg_tz_name(tz),
                                               "items": [{"id": c} for c in calendars]})
    _ok(resp)
    out = []
    for cal in resp.json().get("calendars", {}).values():
        for b in cal.get("busy", []):
            out.append((datetime.fromisoformat(b["start"]).astimezone(tz), datetime.fromisoformat(b["end"]).astimezone(tz)))
    return out


def cfg_tz_name(tz: ZoneInfo) -> str:
    return tz.key


def existing_events(s, cal_id: str, t: date, tz: ZoneInfo) -> dict[str, dict]:
    """Our events for the day, keyed by item id."""
    lo, hi = _day_bounds(t, tz)
    resp = s.get(f"{CAL_API}/calendars/{cal_id}/events",
                 params={"timeMin": lo, "timeMax": hi, "singleEvents": "true", "maxResults": 250,
                         "privateExtendedProperty": f"aiemp_date={t.isoformat()}"})
    _ok(resp)
    return {e["extendedProperties"]["private"]["aiemp_id"]: e
            for e in resp.json().get("items", []) if e.get("status") != "cancelled"
            and e.get("extendedProperties", {}).get("private", {}).get("aiemp_id")}


def sync(s, cal_id: str, plan: dict, items_by_id: dict, cfg: dict, t: date) -> dict:
    tz = ZoneInfo(cfg["timezone"])
    have = existing_events(s, cal_id, t, tz)
    counts = {"created": 0, "kept": 0, "deleted": 0}
    for b in plan["blocks"]:
        if b["id"] in have:
            counts["kept"] += 1
            append_jsonl(PLANS, {"date": t.isoformat(), "id": b["id"], "action": "kept",
                                 "event_id": have[b["id"]]["id"], "run": today()})
            continue
        resp = s.post(f"{CAL_API}/calendars/{cal_id}/events", json=event_body(b, items_by_id[b["id"]], cfg))
        _ok(resp)
        counts["created"] += 1
        append_jsonl(PLANS, {"date": t.isoformat(), "id": b["id"], "action": "created", "where": b["where"],
                             "start": b["start"].isoformat(), "end": b["end"].isoformat(), "minutes": b["minutes"],
                             "event_id": resp.json()["id"], "run": today()})
    planned = {b["id"] for b in plan["blocks"]}
    for item_id in stale_ids(have, planned):
        e = have[item_id]
        _ok(s.delete(f"{CAL_API}/calendars/{cal_id}/events/{e['id']}"))
        counts["deleted"] += 1
        append_jsonl(PLANS, {"date": t.isoformat(), "id": item_id, "action": "deleted", "event_id": e["id"],
                             "run": today()})
    for p in cfg["protected"]:
        wid = f"{WINDOW_PREFIX}{p['name']}"
        if p.get("show_on_calendar") and wid not in have:
            resp = _ok(s.post(f"{CAL_API}/calendars/{cal_id}/events", json=window_event(p, t, cfg)))
            counts["created"] += 1
            append_jsonl(PLANS, {"date": t.isoformat(), "id": wid, "action": "created", "where": "window",
                                 "start": p["start"], "end": p["end"], "event_id": resp.json()["id"], "run": today()})
    return counts


# ----------------------------------------------------------------------------- entry point

def main(argv: list[str]) -> int:
    import tracker

    cfg = load_config()
    tz = ZoneInfo(cfg["timezone"])
    t = datetime.now(tz).date()
    dry = "--dry-run" in argv
    if "--date" in argv:
        t = date.fromisoformat(argv[argv.index("--date") + 1])
    rows = tracker.load_rows()
    items = pick_items(rows, my_name(), t)
    cal_id = os.environ.get("GCAL_ID", "").strip() or cfg.get("calendar_id", "")
    s = None if dry else _session()
    busy = []
    if s is not None and cal_id:
        try:
            busy = busy_times(s, [cal_id] + list(cfg.get("busy_calendars", [])), t, tz)
        except Exception as e:  # a failed free/busy lookup should not stop the plan
            print(f"dayplan: free/busy lookup failed, planning without it: {e}")
    plan = schedule(items, cfg, t, busy)
    print(render(plan, cfg, t))
    if s is None or not cal_id:
        why = "dry run" if dry else "no Google credentials or GCAL_ID"
        print(f"\ndayplan: calendar not written ({why})")
        return 0
    counts = sync(s, cal_id, plan, {r["id"]: r for r in rows}, cfg, t)
    print(f"\ndayplan: {counts['created']} created, {counts['kept']} kept, {counts['deleted']} deleted on the calendar")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
