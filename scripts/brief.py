"""Morning brief: what each team is on today, and what needs your attention.

Writes briefs/YYYY-MM-DD.md plus briefs/teams/<team>.md. The workflow then copies the
brief into the "Morning Brief" tab of the Google Sheet (scripts/sheets.py brief).

Usage: python scripts/brief.py
"""
import json
import os
from collections import defaultdict
from datetime import date

import tracker
from common import BRIEFS, ask, load_context, slug, today

ATTENTION_SYSTEM = """You are the AI employee described in CONTEXT. Given the open action items,
write the "Needs your attention" section of the manager's morning brief: at most 6 bullets, most
urgent first, each naming the item id, owner and why it matters today (overdue, blocks a hard date,
unowned P1, slipped repeatedly...). Apply the escalation rules. Plain markdown bullets only."""


def _score(r: dict, t: date) -> tuple:
    order = {"P1": 0, "P2": 1, "P3": 2}
    due = tracker.parse_date(r["due"])
    overdue = (t - due).days if due and due < t else -1
    return (order.get(r["priority"], 9), -overdue, r["due"] or "9999")


def _line(r: dict, t: date) -> str:
    due = tracker.parse_date(r["due"])
    tag = ""
    if due and due < t:
        tag = f" **({(t - due).days}d overdue)**"
    elif due == t:
        tag = " **(due today)**"
    blk = f", blocked by {r['blocked_by']}" if r["status"] == "blocked" else ""
    return f"- `{r['id']}` {r['priority']} **{r['owner']}**: {r['task']}{tag}{blk}"


def team_digest(team: str, items: list[dict], t: date) -> str:
    items = sorted(items, key=lambda r: _score(r, t))
    due_soon = [r for r in items if (d := tracker.parse_date(r["due"])) and (d - t).days <= 2]
    rest = [r for r in items if r not in due_soon]
    out = [f"# {team}: {t.isoformat()}", ""]
    if due_soon:
        out += ["## Due today / overdue"] + [_line(r, t) for r in due_soon] + [""]
    if rest:
        out += ["## Everything else open"] + [_line(r, t) for r in rest] + [""]
    if not items:
        out.append("_No open items._")
    return "\n".join(out)


def build() -> tuple[str, dict[str, str]]:
    t = date.today()
    items = tracker.open_items()
    by_team = defaultdict(list)
    for r in items:
        by_team[r["team"] or "Unassigned"].append(r)

    if os.environ.get("ANTHROPIC_API_KEY") and items:
        attention = ask(
            ATTENTION_SYSTEM,
            f"<context>\n{load_context()}\n</context>\n\n<today>{t}</today>\n\n"
            f"<open_items>\n{json.dumps(items, indent=1)}\n</open_items>",
            max_tokens=1200,
        ).strip()
    else:
        attention = "\n".join(_line(r, t) for r in sorted(items, key=lambda r: _score(r, t))[:6]) or "_Nothing open._"

    counts = ", ".join(f"{k}: {len(v)}" for k, v in sorted(by_team.items()))
    brief = [f"# Morning brief: {t.strftime('%A %d %b %Y')}", "",
             f"_{len(items)} open items ({counts})_", "",
             "## Needs your attention", attention, ""]
    digests = {}
    for team, rows in sorted(by_team.items()):
        digests[team] = team_digest(team, rows, t)
        brief.append(digests[team].split("\n", 1)[1].replace("## ", f"### {team}: "))
    return "\n".join(brief), digests


if __name__ == "__main__":
    brief, digests = build()
    (BRIEFS / f"{today()}.md").write_text(brief, encoding="utf-8")
    for team, text in digests.items():
        (BRIEFS / "teams" / f"{slug(team)}.md").write_text(text, encoding="utf-8")
    print(brief)
