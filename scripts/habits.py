"""Quota-aware habits: the walk and anything else you want done N times a week.

A fixed daily reminder for a flexible habit is wrong most days, so you learn to ignore it. This
keeps the weekly score instead and lets the calendar event say where you stand:

    Walk: 2 of 5 this week, 3 days left            (slack, low pressure)
    Walk: MANDATORY today (1 of 5, 2 days left)     (no slack, red, three reminders)
    Walk: done for the week (5 of 5)

Recording happens in the Habits tab of the Sheet: one row per day for the current week, status
done / skip / snooze. The workflow pulls that tab before every run and appends changes to
memory/habits.jsonl, which is what the score reads. Skip shows its consequence in the state line.

Config: context/habits.json, a list of {"name", "quota_per_week", "start", "end"}. The name must
match a protected window in context/dayplan.json with "show_on_calendar" so the block exists.
"""
import json
from datetime import date, timedelta
from pathlib import Path

from common import CONTEXT_DIR, MEMORY, read_jsonl, append_jsonl, today

CONFIG = CONTEXT_DIR / "habits.json"
LOG = MEMORY / "habits.jsonl"
STATUSES = ("done", "skip", "snooze")
DEFAULT = [{"name": "Walk", "quota_per_week": 5, "start": "17:00", "end": "17:30"}]
SHEET_COLS = ("date", "day", "habit", "status", "state")


def load_habits() -> list[dict]:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return list(DEFAULT)


def by_name(name: str) -> dict | None:
    return next((h for h in load_habits() if h["name"].lower() == name.lower()), None)


def week_start(t: date) -> date:
    return t - timedelta(days=t.weekday())  # Monday


def latest(records: list[dict]) -> dict[tuple[str, str], dict]:
    """Last record per (date, habit); later lines win."""
    out = {}
    for r in records:
        out[(r["date"], r["habit"])] = r
    return out


def state(habit: dict, t: date, records: list[dict]) -> dict:
    """Where the week stands as of t. days_left counts today unless today is already resolved."""
    last = latest(records)
    ws = week_start(t)
    week = [ws + timedelta(days=i) for i in range(7)]
    done = sum(1 for d in week if d <= t and last.get((d.isoformat(), habit["name"]), {}).get("status") == "done")
    today_status = last.get((t.isoformat(), habit["name"]), {}).get("status", "")
    quota = int(habit.get("quota_per_week", 7))
    remaining = max(0, quota - done)
    future = [d for d in week if d > t]
    days_left = len(future) + (0 if today_status in ("done", "skip") else 1)
    slack = days_left - remaining
    mandatory = remaining > 0 and slack <= 0 and today_status not in ("done", "skip")
    if remaining == 0:
        line = f"{habit['name']}: done for the week ({done} of {quota})"
    elif today_status == "skip":
        line = f"{habit['name']}: skipped today ({done} of {quota}, {len(future)} days left for {remaining})"
    elif mandatory:
        line = f"{habit['name']}: MANDATORY today ({done} of {quota}, {days_left} days left)"
    else:
        line = f"{habit['name']}: {done} of {quota} this week, {days_left} days left"
    # what a skip today would mean
    if remaining > 0 and today_status not in ("done", "skip"):
        after = len(future) - remaining
        if after < 0:
            consequence = "skipping today makes the quota impossible this week"
        elif after == 0:
            consequence = "skipping today makes every remaining day mandatory"
        else:
            consequence = f"skipping today leaves {after} spare day{'s' if after != 1 else ''}"
    else:
        consequence = ""
    return {"done": done, "quota": quota, "remaining": remaining, "days_left": days_left, "slack": slack,
            "mandatory": mandatory, "today_status": today_status, "line": line, "consequence": consequence}


def decorate(body: dict, habit: dict, st: dict) -> dict:
    """Apply the habit's state to a calendar event body (summary, colour, reminders, description)."""
    body = dict(body)
    body["summary"] = st["line"]
    body["colorId"] = "11" if st["mandatory"] else ("10" if st["remaining"] == 0 else "2")  # tomato / basil / sage
    minutes = [30, 10, 0] if st["mandatory"] else [5]
    body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": m} for m in minutes]}
    lines = [st["line"]]
    if st["consequence"]:
        lines.append(st["consequence"].capitalize() + ".")
    lines += ["", "Record it in the Habits tab of the Sheet: done, skip or snooze. The score updates on the next run."]
    body["description"] = "\n".join(lines)
    return body


def sheet_rows(habits: list[dict], t: date, records: list[dict]) -> list[list]:
    """Header plus one row per habit per day of the current week. Past days without a record show
    'missed'. The status cell is what you edit."""
    last = latest(records)
    ws = week_start(t)
    out = [list(SHEET_COLS)]
    for h in habits:
        for i in range(7):
            d = ws + timedelta(days=i)
            rec = last.get((d.isoformat(), h["name"]), {})
            status = rec.get("status", "")
            if not status and d < t:
                status = "missed"
            st_line = state(h, d, records)["line"] if d <= t else ""
            out.append([d.isoformat(), d.strftime("%a"), h["name"], status, st_line])
    return out


def apply_sheet(sheet_records: list[dict], records: list[dict], t: date, log=append_jsonl) -> int:
    """Statuses typed in the Habits tab become log entries. Only done/skip/snooze on days up to
    today count; 'missed' and blanks are display only. Returns the number of entries written."""
    last = latest(records)
    n = 0
    for rec in sheet_records:
        d, habit, status = str(rec.get("date", "")).strip(), str(rec.get("habit", "")).strip(), str(rec.get("status", "")).strip().lower()
        if not d or not habit or status not in STATUSES or d > t.isoformat():
            continue
        if last.get((d, habit), {}).get("status") == status:
            continue
        entry = {"date": d, "habit": habit, "status": status, "run": today()}
        log(LOG, entry)
        last[(d, habit)] = entry
        n += 1
    return n


def week_summary(t: date) -> list[str]:
    records = read_jsonl(LOG)
    return [state(h, t, records)["line"] for h in load_habits()]
