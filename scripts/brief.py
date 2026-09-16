"""Morning brief: a plan for the day, not a flat list.

Sections, in order:
  1. Waiting for your confirmation   to_verify items with their completion quote
  2. Do first                        up to 3 items that unblock the most or are closest to a hard date
  3. Batch                           items that share an unblocker ("one call with X closes 04, 05, 07")
  4. Delegate?                       my items that the unblocker or the team lead could own
  5. Recurring threads               threads mentioned 3+ times: make this a task?
  6. Per project                     planned/unplanned counts, items, drafts linked, slips

Writes briefs/YYYY-MM-DD.md plus briefs/teams/<team>.md. The workflow then copies the brief into
the "Morning Brief" tab of the Google Sheet (scripts/sheets.py brief).

Usage: python scripts/brief.py
The only Claude call is the "why" line of Do first; without ANTHROPIC_API_KEY the deterministic
reason is used. Every reason cites item ids.
"""
import re
from collections import defaultdict
from datetime import date

import tracker
from common import (BRIEFS, COMPANY, DRAFTS, THREADS, UNASSIGNED, ask, have_key, load_context, my_name,
                    project_title, read_jsonl, slug, today)

WHY_SYSTEM = """You are the AI employee described in <context>. For each candidate item, write ONE
sentence (under 25 words) saying why it should be done first today. Cite the item ids and the
facts given (what it unblocks, the due date, the next step). No generic advice: if the facts do
not support a reason, write "no strong reason". Reply as markdown bullets, one per item, each
starting with the item id in backticks."""


def _score(r: dict, t: date) -> tuple:
    order = {"P1": 0, "P2": 1, "P3": 2}
    due = tracker.parse_date(r["due"])
    overdue = (t - due).days if due and due < t else -1
    return (order.get(r["priority"], 9), -overdue, r["due"] or "9999")


def _short(item_id: str) -> str:
    return item_id.rsplit("-", 1)[-1] if "-" in item_id else item_id


def _due_tag(r: dict, t: date) -> str:
    due = tracker.parse_date(r["due"])
    if due and due < t:
        return f" **({(t - due).days}d overdue)**"
    if due == t:
        return " **(due today)**"
    return ""


def _slip_tag(r: dict) -> str:
    n, first = tracker.slip_count(r)
    return f" _(slipped {n} times since {first})_" if n >= 2 and first else ""


def _draft_tag(r: dict) -> str:
    p = DRAFTS / f"{r['id']}.md"
    return f" [draft](../drafts/{p.name})" if p.exists() else ""


def _line(r: dict, t: date) -> str:
    blk = f", blocked by {r['blocked_by']}" if r["status"] == "blocked" and r["blocked_by"] else ""
    ver = " _(to verify)_" if r["status"] == "to_verify" else ""
    return (f"- `{r['id']}` {r['priority']} **{r['owner']}**: {r['task']}{_due_tag(r, t)}{blk}{ver}"
            f"{_slip_tag(r)}{_draft_tag(r)}")


def _completion_quote(r: dict) -> str:
    quotes = re.findall(r"\[(.+?)\]", r.get("notes") or "")
    return quotes[-1] if quotes else ""


def team_leads() -> dict[str, str]:
    """{team: lead} from the Teams table in company.md."""
    leads = {}
    if not COMPANY.exists():
        return leads
    in_table = False
    for ln in COMPANY.read_text(encoding="utf-8").splitlines():
        if ln.startswith("| Team") and "Lead" in ln:
            in_table = True
            continue
        if in_table:
            if not ln.startswith("|"):
                break
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if len(cells) >= 2 and not set(cells[0]) <= {"-", " "}:
                leads[cells[0]] = cells[1]
    return leads


def unblocks(r: dict, items: list[dict]) -> list[str]:
    """Ids of other open items that reference this item's id in blocked_by or prerequisites."""
    out = []
    for o in items:
        if o["id"] == r["id"]:
            continue
        refs = f"{o.get('blocked_by', '')} {o.get('prerequisites', '')}"
        if r["id"] in refs or _short(r["id"]) in refs.split():
            out.append(o["id"])
    return out


def do_first(items: list[dict], t: date, n: int = 3) -> list[tuple[dict, str]]:
    """Up to n items with a deterministic reason. Ranked by what they unblock, then hard dates."""
    cands = [r for r in items if r["status"] != "to_verify"]
    scored = []
    for r in cands:
        ub = unblocks(r, items)
        due = tracker.parse_date(r["due"])
        days = (due - t).days if due else 9999
        scored.append((-len(ub), days, _score(r, t), r, ub))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    out = []
    for neg, days, _, r, ub in scored[:n]:
        why = []
        if ub:
            why.append(f"unblocks {', '.join(f'`{i}`' for i in ub)}")
        if days < 0:
            why.append(f"{-days}d overdue")
        elif days == 0:
            why.append("due today")
        elif days < 9999:
            why.append(f"due {r['due']}")
        if r["priority"] == "P1":
            why.append("P1")
        out.append((r, "; ".join(why) or "next in line"))
    return out


def batches(items: list[dict]) -> list[tuple[str, list[dict]]]:
    groups = defaultdict(list)
    for r in items:
        key = r.get("unblocker") or (r.get("blocked_by") if r["status"] == "blocked" else "")
        if key:
            groups[key].append(r)
    return [(k, v) for k, v in sorted(groups.items()) if len(v) >= 2]


def delegate(items: list[dict], me: str) -> list[tuple[dict, str]]:
    leads = team_leads()
    out = []
    for r in items:
        if slug(r["owner"]) != slug(me):
            continue
        ub = r.get("unblocker")
        lead = leads.get(r.get("team", ""), "")
        if ub and slug(ub) != slug(me):
            out.append((r, ub))
        elif lead and slug(lead) != slug(me) and r.get("type") in ("build", "coordinate", "other", ""):
            out.append((r, f"{lead} (team lead)"))
    return out


def recurring_threads(min_mentions: int = 3) -> list[dict]:
    return [t for t in read_jsonl(THREADS)
            if int(t.get("mentions", 1)) >= min_mentions and not t.get("promoted_to")]


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


def build(ask_fn=None, t: date | None = None) -> tuple[str, dict[str, str]]:
    """Returns (brief markdown, {team: digest markdown}). ask_fn(system, user) -> str writes the
    Do-first reasons; None means deterministic reasons (used when there is no API key)."""
    t = t or date.today()
    me = my_name()
    items = tracker.open_items()
    if ask_fn is None and have_key():
        ask_fn = ask

    by_project = defaultdict(list)
    for r in items:
        by_project[r["project"] or UNASSIGNED].append(r)
    counts = ", ".join(f"{project_title(p)}: {len(v)}" for p, v in sorted(by_project.items()))
    out = [f"# Morning brief: {t.strftime('%A %d %b %Y')}", "", f"_{len(items)} open items ({counts})_", ""]

    verify = [r for r in items if r["status"] == "to_verify"]
    out.append("## Waiting for your confirmation")
    if verify:
        for r in verify:
            q = _completion_quote(r)
            out.append(f"- `{r['id']}` **{r['owner']}**: {r['task']}" + (f' ("{q}")' if q else "")
                       + " -> set `done` in the Sheet if true")
    else:
        out.append("_Nothing reported complete._")
    out.append("")

    first = do_first(items, t)
    out.append("## Do first")
    if first:
        reasons = {r["id"]: why for r, why in first}
        if ask_fn is not None:
            try:
                facts = "\n".join(f"- {r['id']} ({r['owner']}): {r['task']} | facts: {why}"
                                  f" | next_step: {r.get('next_step') or 'none'}" for r, why in first)
                reply = ask_fn(WHY_SYSTEM, f"<context>\n{load_context()}\n</context>\n\n<today>{t}</today>\n\n"
                                           f"<candidates>\n{facts}\n</candidates>")
                for ln in reply.splitlines():
                    m = re.match(r"\s*[-*]\s*`([^`]+)`\s*[:,.-]?\s*(.+)", ln)
                    if m and m.group(1) in reasons and "no strong reason" not in m.group(2).lower():
                        reasons[m.group(1)] = m.group(2).strip()
            except Exception as e:  # deterministic reasons still stand
                out.append(f"_Claude reasons unavailable: {e}_")
        for r, _ in first:
            out.append(f"- `{r['id']}` **{r['owner']}**: {r['task']}{_due_tag(r, t)}{_draft_tag(r)}")
            out.append(f"  - why: {reasons[r['id']]}")
            if r.get("next_step"):
                out.append(f"  - next step: {r['next_step']}" + (f" _(basis: {r['basis']})_" if r.get("basis") else ""))
    else:
        out.append("_Nothing open._")
    out.append("")

    out.append("## Batch")
    bs = batches(items)
    if bs:
        for who, rows in bs:
            ids = ", ".join(f"`{_short(r['id'])}`" for r in rows)
            out.append(f"- one call with **{who}** closes {ids}")
            out += [f"  - `{r['id']}` {r['task']}" for r in rows]
    else:
        out.append("_No shared unblockers._")
    out.append("")

    out.append("## Delegate?")
    dl = delegate(items, me)
    if dl:
        out += [f"- `{r['id']}` {r['task']} -> could go to **{who}**" for r, who in dl]
    else:
        out.append(f"_Nothing of {me}'s looks delegable._")
    out.append("")

    out.append("## Recurring threads")
    rt = recurring_threads()
    if rt:
        out += [f"- `{th['id']}` **{th['topic']}** ({th['project']}, mentioned {th['mentions']}x, last {th['last_seen']}):"
                f" {th['note']} -> make this a task?" for th in rt]
    else:
        out.append("_None yet._")
    out.append("")

    digests = {}
    by_team = defaultdict(list)
    for r in items:
        by_team[r["team"] or "Unassigned"].append(r)
    for project, rows in sorted(by_project.items()):
        planned = sum(1 for r in rows if r.get("origin") != "unplanned")
        unplanned = len(rows) - planned
        title = project_title(project) if project != UNASSIGNED else "Unassigned (flag: no project)"
        out.append(f"## Project: {title}")
        out.append(f"_{planned} planned, {unplanned} unplanned_")
        rows = sorted(rows, key=lambda r: _score(r, t))
        due_soon = [r for r in rows if (d := tracker.parse_date(r["due"])) and (d - t).days <= 2]
        rest = [r for r in rows if r not in due_soon]
        if due_soon:
            out += ["### Due today / overdue"] + [_line(r, t) for r in due_soon]
        if rest:
            out += ["### Everything else open"] + [_line(r, t) for r in rest]
        out.append("")
    for team, rows in sorted(by_team.items()):
        digests[team] = team_digest(team, rows, t)
    return "\n".join(out), digests


if __name__ == "__main__":
    brief, digests = build()
    BRIEFS.mkdir(parents=True, exist_ok=True)
    (BRIEFS / "teams").mkdir(parents=True, exist_ok=True)
    (BRIEFS / f"{today()}.md").write_text(brief, encoding="utf-8")
    for team, text in digests.items():
        (BRIEFS / "teams" / f"{slug(team)}.md").write_text(text, encoding="utf-8")
    print(brief)
