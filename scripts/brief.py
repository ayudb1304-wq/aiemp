"""Morning brief: a plan for the day, not a flat list.

Sections, in order:
  1. Waiting for your confirmation   to_verify items with their completion quote
  2. Do first                        up to 3 items that unblock the most or are closest to a hard date
  3. Batch                           items that share an unblocker ("one call with X closes 04, 05, 07")
  4. Delegate?                       my items that the unblocker or the team lead could own
  5. Recurring threads               threads mentioned 3+ times: make this a task?
  6. Per project                     planned/unplanned counts, items, drafts linked, slips

Writes briefs/YYYY-MM-DD.md (the record the memory and search read), briefs/YYYY-MM-DD.html (the
same plan as a styled page: headline numbers, cards, per-project tables), briefs/YYYY-MM-DD.json (the
plan as data) and briefs/teams/<team>.md. The workflow then writes the plan into the "Morning Brief"
tab of the Google Sheet as a formatted table (scripts/sheets.py brief).

Usage: python scripts/brief.py
The only Claude call is the "why" line of Do first; without ANTHROPIC_API_KEY the deterministic
reason is used. Every reason cites item ids.
"""
import html
import json
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


def plan(ask_fn=None, t: date | None = None) -> dict:
    """The day's plan as data. ask_fn(system, user) -> str writes the Do-first reasons; None means
    deterministic reasons (used when there is no API key). Every renderer below reads this dict."""
    t = t or date.today()
    me = my_name()
    items = tracker.open_items()
    if ask_fn is None and have_key():
        ask_fn = ask

    by_project = defaultdict(list)
    for r in items:
        by_project[r["project"] or UNASSIGNED].append(r)
    overdue = [r for r in items if (d := tracker.parse_date(r["due"])) and d < t]
    counts = {"open": len(items), "p1": sum(r["priority"] == "P1" for r in items), "overdue": len(overdue),
              "due_today": sum(tracker.parse_date(r["due"]) == t for r in items),
              "to_verify": sum(r["status"] == "to_verify" for r in items),
              "blocked": sum(r["status"] == "blocked" for r in items)}

    first = do_first(items, t)
    reasons = {r["id"]: why for r, why in first}
    claude_error = ""
    if first and ask_fn is not None:
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
            claude_error = str(e)

    projects = []
    for project, rows in sorted(by_project.items()):
        rows = sorted(rows, key=lambda r: _score(r, t))
        due_soon = [r for r in rows if (d := tracker.parse_date(r["due"])) and (d - t).days <= 2]
        projects.append({
            "slug": project,
            "title": project_title(project) if project != UNASSIGNED else "Unassigned (flag: no project)",
            "planned": sum(1 for r in rows if r.get("origin") != "unplanned"),
            "unplanned": sum(1 for r in rows if r.get("origin") == "unplanned"),
            "due_soon": due_soon, "rest": [r for r in rows if r not in due_soon],
        })
    by_team = defaultdict(list)
    for r in items:
        by_team[r["team"] or "Unassigned"].append(r)
    return {
        "date": t.isoformat(), "title": t.strftime("%A %d %b %Y"), "me": me, "counts": counts,
        "project_counts": [(project_title(p), len(v)) for p, v in sorted(by_project.items())],
        "verify": [{"item": r, "quote": _completion_quote(r)} for r in items if r["status"] == "to_verify"],
        "first": [{"item": r, "why": reasons[r["id"]]} for r, _ in first], "claude_error": claude_error,
        "batches": [{"who": who, "items": rows} for who, rows in batches(items)],
        "delegate": [{"item": r, "to": who} for r, who in delegate(items, me)],
        "threads": recurring_threads(),
        "projects": projects,
        "teams": {team: rows for team, rows in sorted(by_team.items())},
    }


def render_md(p: dict) -> str:
    t = date.fromisoformat(p["date"])
    counts = ", ".join(f"{title}: {n}" for title, n in p["project_counts"])
    out = [f"# Morning brief: {p['title']}", "", f"_{p['counts']['open']} open items ({counts})_", ""]

    out.append("## Waiting for your confirmation")
    if p["verify"]:
        for v in p["verify"]:
            r, q = v["item"], v["quote"]
            out.append(f"- `{r['id']}` **{r['owner']}**: {r['task']}" + (f' ("{q}")' if q else "")
                       + " -> set `done` in the Sheet if true")
    else:
        out.append("_Nothing reported complete._")
    out.append("")

    out.append("## Do first")
    if p["first"]:
        if p["claude_error"]:
            out.append(f"_Claude reasons unavailable: {p['claude_error']}_")
        for f in p["first"]:
            r = f["item"]
            out.append(f"- `{r['id']}` **{r['owner']}**: {r['task']}{_due_tag(r, t)}{_draft_tag(r)}")
            out.append(f"  - why: {f['why']}")
            if r.get("next_step"):
                out.append(f"  - next step: {r['next_step']}" + (f" _(basis: {r['basis']})_" if r.get("basis") else ""))
    else:
        out.append("_Nothing open._")
    out.append("")

    out.append("## Batch")
    if p["batches"]:
        for b in p["batches"]:
            ids = ", ".join(f"`{_short(r['id'])}`" for r in b["items"])
            out.append(f"- one call with **{b['who']}** closes {ids}")
            out += [f"  - `{r['id']}` {r['task']}" for r in b["items"]]
    else:
        out.append("_No shared unblockers._")
    out.append("")

    out.append("## Delegate?")
    if p["delegate"]:
        out += [f"- `{d['item']['id']}` {d['item']['task']} -> could go to **{d['to']}**" for d in p["delegate"]]
    else:
        out.append(f"_Nothing of {p['me']}'s looks delegable._")
    out.append("")

    out.append("## Recurring threads")
    if p["threads"]:
        out += [f"- `{th['id']}` **{th['topic']}** ({th['project']}, mentioned {th['mentions']}x, last {th['last_seen']}):"
                f" {th['note']} -> make this a task?" for th in p["threads"]]
    else:
        out.append("_None yet._")
    out.append("")

    for pr in p["projects"]:
        out.append(f"## Project: {pr['title']}")
        out.append(f"_{pr['planned']} planned, {pr['unplanned']} unplanned_")
        if pr["due_soon"]:
            out += ["### Due today / overdue"] + [_line(r, t) for r in pr["due_soon"]]
        if pr["rest"]:
            out += ["### Everything else open"] + [_line(r, t) for r in pr["rest"]]
        out.append("")
    return "\n".join(out)


# ----------------------------------------------------------------------------- HTML page

HTML_CSS = """
:root{--ink:#1f2933;--muted:#6b7280;--line:#e3e7ee;--paper:#f6f7f9;--card:#ffffff;--navy:#1f3864;
--blue:#2a78d6;--p1:#1c5cab;--p2:#5598e7;--p3:#b7d3f6;--red:#c0262a;--amber:#8a6100;--amber-bg:#fff2cc;
--green:#1e7b1e;--green-bg:#e2f3e2;--orange:#9a3f12;--orange-bg:#fde4d7;--red-bg:#fbe3e3}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 Arial,Helvetica,sans-serif}
.hero{background:linear-gradient(135deg,#17294a 0%,#1f3864 55%,#2a4f8f 100%);color:#fff;padding:28px 32px 24px}
.hero .kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;opacity:.75}
.hero h1{margin:4px 0 6px;font-size:26px;font-weight:700}
.hero .sub{opacity:.85;font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:18px 0 0}
.tile{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.18);border-radius:10px;padding:10px 12px}
.tile .n{font-size:26px;font-weight:700;line-height:1.1}
.tile .l{font-size:11px;letter-spacing:.08em;text-transform:uppercase;opacity:.8;margin-top:2px}
.tile.alert .n{color:#ffb4b4}
main{max-width:1080px;margin:0 auto;padding:22px 20px 40px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:0 0 16px;
box-shadow:0 1px 2px rgba(16,24,40,.04)}
section h2{margin:0 0 4px;font-size:15px;letter-spacing:.02em;color:var(--navy);display:flex;align-items:center;gap:10px}
section h2 .count{font-size:12px;font-weight:600;color:var(--muted);background:var(--paper);border-radius:999px;padding:1px 9px}
section .hint{margin:0 0 12px;color:var(--muted);font-size:12.5px}
.empty{color:var(--muted);font-style:italic;margin:6px 0 0}
.card{border:1px solid var(--line);border-left:4px solid var(--blue);border-radius:8px;padding:10px 14px;margin:10px 0}
.card .task{font-weight:600}
.card .meta{color:var(--muted);font-size:12.5px;margin-top:2px}
.card .why{margin-top:6px}
.card .why b{color:var(--navy)}
.num{display:inline-block;width:22px;height:22px;border-radius:50%;background:var(--navy);color:#fff;font-size:12px;
font-weight:700;text-align:center;line-height:22px;margin-right:8px;vertical-align:middle}
.pill{display:inline-block;font-size:11px;font-weight:700;border-radius:999px;padding:1px 8px;line-height:16px;white-space:nowrap}
.pill.P1{background:var(--red-bg);color:#9b1c1c}.pill.P2{background:#fff4d6;color:var(--amber)}.pill.P3{background:#eef1f5;color:var(--muted)}
.pill.overdue{background:var(--red-bg);color:var(--red)}.pill.today{background:var(--amber-bg);color:var(--amber)}
.pill.verify{background:var(--amber-bg);color:var(--amber)}.pill.blocked{background:var(--orange-bg);color:var(--orange)}
.pill.progress{background:#e1ecfb;color:var(--p1)}.pill.slip{background:#eef1f5;color:var(--muted)}
.pill.draft{background:#e1ecfb;color:var(--p1)}
.owner{font-weight:600}
.id{font-family:Consolas,Menlo,monospace;font-size:11px;color:var(--muted)}
table{width:100%;border-collapse:collapse;margin-top:8px;font-size:13px}
th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:6px 8px;border-bottom:2px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.owner{white-space:nowrap}td.due{white-space:nowrap}
h3{margin:14px 0 0;font-size:12.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.quote{color:var(--muted);font-style:italic}
footer{color:var(--muted);font-size:12px;text-align:center;margin-top:8px}
@media print{body{background:#fff}section{break-inside:avoid;box-shadow:none}.hero{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def _h(s) -> str:
    return html.escape(str(s or ""))


def _pills(r: dict, t: date) -> str:
    out = [f'<span class="pill {_h(r["priority"])}">{_h(r["priority"])}</span>']
    due = tracker.parse_date(r["due"])
    if due and due < t:
        out.append(f'<span class="pill overdue">{(t - due).days}d overdue</span>')
    elif due == t:
        out.append('<span class="pill today">due today</span>')
    if r["status"] == "to_verify":
        out.append('<span class="pill verify">to verify</span>')
    elif r["status"] == "blocked":
        out.append('<span class="pill blocked">blocked' + (f": {_h(r['blocked_by'])}" if r["blocked_by"] else "") + "</span>")
    elif r["status"] == "in_progress":
        out.append('<span class="pill progress">in progress</span>')
    n, first = tracker.slip_count(r)
    if n >= 2 and first:
        out.append(f'<span class="pill slip">slipped {n}x since {_h(first)}</span>')
    if (DRAFTS / f"{r['id']}.md").exists():
        out.append(f'<a class="pill draft" href="../drafts/{_h(r["id"])}.md">draft</a>')
    return " ".join(out)


def _table(rows: list[dict], t: date) -> str:
    body = "".join(
        f"<tr><td class='owner'>{_h(r['owner'])}</td><td>{_h(r['task'])}<br><span class='id'>{_h(r['id'])}</span></td>"
        f"<td class='due'>{_h(r['due']) or '<span class=quote>no date</span>'}</td><td>{_pills(r, t)}</td></tr>"
        for r in rows)
    return f"<table><thead><tr><th>Owner</th><th>Task</th><th>Due</th><th>Flags</th></tr></thead><tbody>{body}</tbody></table>"


def render_html(p: dict) -> str:
    t = date.fromisoformat(p["date"])
    c = p["counts"]
    tiles = [("open items", c["open"], False), ("P1", c["p1"], False), ("overdue", c["overdue"], c["overdue"] > 0),
             ("due today", c["due_today"], False), ("to verify", c["to_verify"], False), ("blocked", c["blocked"], False)]
    tiles_html = "".join(f'<div class="tile{" alert" if a else ""}"><div class="n">{n}</div><div class="l">{_h(l)}</div></div>'
                         for l, n, a in tiles)
    counts = " · ".join(f"{_h(title)}: {n}" for title, n in p["project_counts"]) or "no open items"
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Morning brief {_h(p['date'])}</title><style>{HTML_CSS}</style></head><body>",
             f"<div class='hero'><div class='kicker'>Morning brief</div><h1>{_h(p['title'])}</h1>"
             f"<div class='sub'>{counts}</div><div class='tiles'>{tiles_html}</div></div><main>"]

    def section(title, count, hint, body):
        parts.append(f"<section><h2>{_h(title)}<span class='count'>{count}</span></h2>"
                     f"<p class='hint'>{_h(hint)}</p>{body}</section>")

    body = ""
    for v in p["verify"]:
        r = v["item"]
        body += (f"<div class='card' style='border-left-color:#fab219'><div class='task'><span class='owner'>{_h(r['owner'])}</span>: "
                 f"{_h(r['task'])}</div><div class='meta'>{_pills(r, t)} <span class='id'>{_h(r['id'])}</span></div>"
                 + (f"<div class='why quote'>“{_h(v['quote'])}”</div>" if v["quote"] else "") + "</div>")
    section("Waiting for your confirmation", len(p["verify"]),
            "Reported complete in a meeting. Set done in the Sheet if it is true; only you close items.",
            body or "<p class='empty'>Nothing reported complete.</p>")

    body = ""
    for i, f in enumerate(p["first"], 1):
        r = f["item"]
        body += (f"<div class='card'><div class='task'><span class='num'>{i}</span><span class='owner'>{_h(r['owner'])}</span>: "
                 f"{_h(r['task'])}</div><div class='meta'>{_pills(r, t)} <span class='id'>{_h(r['id'])}</span></div>"
                 f"<div class='why'><b>Why:</b> {_h(f['why'])}</div>")
        if r.get("next_step"):
            body += f"<div class='why'><b>Next step:</b> {_h(r['next_step'])}" + (
                f" <span class='quote'>(basis: {_h(r['basis'])})</span>" if r.get("basis") else "") + "</div>"
        body += "</div>"
    if p["claude_error"]:
        body = f"<p class='empty'>Claude reasons unavailable: {_h(p['claude_error'])}</p>" + body
    section("Do first", len(p["first"]), "Up to three items that unblock the most or are closest to a hard date.",
            body or "<p class='empty'>Nothing open.</p>")

    body = ""
    for b in p["batches"]:
        body += (f"<div class='card'><div class='task'>One call with <span class='owner'>{_h(b['who'])}</span> closes "
                 f"{len(b['items'])} items</div><ul>" + "".join(
                     f"<li>{_h(r['task'])} <span class='id'>{_h(r['id'])}</span></li>" for r in b["items"]) + "</ul></div>")
    section("Batch", len(p["batches"]), "Items that share an unblocker.", body or "<p class='empty'>No shared unblockers.</p>")

    body = ""
    if p["delegate"]:
        body = "<table><thead><tr><th>Task</th><th>Could go to</th></tr></thead><tbody>" + "".join(
            f"<tr><td>{_h(d['item']['task'])}<br><span class='id'>{_h(d['item']['id'])}</span></td>"
            f"<td class='owner'>{_h(d['to'])}</td></tr>" for d in p["delegate"]) + "</tbody></table>"
    section("Delegate?", len(p["delegate"]), f"{_h(p['me'])}'s items that the unblocker or the team lead could own.",
            body or f"<p class='empty'>Nothing of {_h(p['me'])}'s looks delegable.</p>")

    body = ""
    for th in p["threads"]:
        body += (f"<div class='card' style='border-left-color:#9085e9'><div class='task'>{_h(th['topic'])} "
                 f"<span class='pill slip'>mentioned {_h(th['mentions'])}x</span></div>"
                 f"<div class='meta'>{_h(th['project'])} · last {_h(th['last_seen'])} · <span class='id'>{_h(th['id'])}</span></div>"
                 f"<div class='why'>{_h(th['note'])} <b>Make this a task?</b></div></div>")
    section("Recurring threads", len(p["threads"]), "Mentioned three or more times without becoming an item.",
            body or "<p class='empty'>None yet.</p>")

    for pr in p["projects"]:
        body = ""
        if pr["due_soon"]:
            body += "<h3>Due today / overdue</h3>" + _table(pr["due_soon"], t)
        if pr["rest"]:
            body += "<h3>Everything else open</h3>" + _table(pr["rest"], t)
        section(f"Project: {pr['title']}", len(pr["due_soon"]) + len(pr["rest"]),
                f"{pr['planned']} planned, {pr['unplanned']} unplanned", body)

    parts.append(f"<footer>Generated {_h(p['date'])} from tracker/actions.xlsx. The Markdown copy is the record the memory reads.</footer>")
    parts.append("</main></body></html>")
    return "".join(parts)


# ----------------------------------------------------------------------------- Sheet tab rows

SHEET_HEADER = ["Section", "Owner", "Task", "Priority", "Due", "Status / flags", "Why / note", "Id"]


def sheet_rows(p: dict) -> list[tuple[str, list[str]]]:
    """(kind, cells) per row for the Morning Brief tab. Kinds: title, subtitle, header, section,
    item, sub, empty. scripts/sheets.py formats each kind; the values alone are readable too."""
    t = date.fromisoformat(p["date"])
    c = p["counts"]
    rows = [("title", [f"Morning brief: {p['title']}"]),
            ("subtitle", [f"{c['open']} open · {c['p1']} P1 · {c['overdue']} overdue · {c['due_today']} due today"
                          f" · {c['to_verify']} to verify · {c['blocked']} blocked"]),
            ("header", SHEET_HEADER)]

    def flags(r):
        f = []
        due = tracker.parse_date(r["due"])
        if due and due < t:
            f.append(f"{(t - due).days}d overdue")
        elif due == t:
            f.append("due today")
        if r["status"] != "open":
            f.append(r["status"].replace("_", " ") + (f": {r['blocked_by']}" if r["status"] == "blocked" and r["blocked_by"] else ""))
        n, first = tracker.slip_count(r)
        if n >= 2 and first:
            f.append(f"slipped {n}x since {first}")
        return ", ".join(f)

    def item(r, section="", note=""):
        return ("item", [section, r["owner"], r["task"], r["priority"], r["due"], flags(r), note, r["id"]])

    rows.append(("section", ["Waiting for your confirmation"]))
    for v in p["verify"]:
        rows.append(item(v["item"], note=(f'"{v["quote"]}" ' if v["quote"] else "") + "set done in Actions if true"))
    if not p["verify"]:
        rows.append(("empty", ["", "Nothing reported complete."]))

    rows.append(("section", ["Do first"]))
    for i, f in enumerate(p["first"], 1):
        r = f["item"]
        rows.append(item(r, section=str(i), note=f["why"]))
        if r.get("next_step"):
            rows.append(("sub", ["", "", f"Next step: {r['next_step']}" + (f" (basis: {r['basis']})" if r.get("basis") else "")]))
    if not p["first"]:
        rows.append(("empty", ["", "Nothing open."]))

    rows.append(("section", ["Batch"]))
    for b in p["batches"]:
        rows.append(("sub", ["", b["who"], f"One call with {b['who']} closes {len(b['items'])} items"]))
        for r in b["items"]:
            rows.append(item(r))
    if not p["batches"]:
        rows.append(("empty", ["", "No shared unblockers."]))

    rows.append(("section", ["Delegate?"]))
    for d in p["delegate"]:
        rows.append(item(d["item"], note=f"could go to {d['to']}"))
    if not p["delegate"]:
        rows.append(("empty", ["", f"Nothing of {p['me']}'s looks delegable."]))

    rows.append(("section", ["Recurring threads"]))
    for th in p["threads"]:
        rows.append(("item", ["", th["project"], th["topic"], "", th["last_seen"], f"mentioned {th['mentions']}x",
                              f"{th['note']} Make this a task?", th["id"]]))
    if not p["threads"]:
        rows.append(("empty", ["", "None yet."]))

    for pr in p["projects"]:
        rows.append(("section", [f"Project: {pr['title']} ({pr['planned']} planned, {pr['unplanned']} unplanned)"]))
        for r in pr["due_soon"]:
            rows.append(item(r, section="Due today / overdue"))
        for r in pr["rest"]:
            rows.append(item(r, section="Open"))
    return rows


def build(ask_fn=None, t: date | None = None) -> tuple[str, dict[str, str]]:
    """Returns (brief markdown, {team: digest markdown}). Kept for callers that only want the text;
    the workflow entry point below also writes the HTML page and the plan JSON."""
    p = plan(ask_fn, t)
    t = date.fromisoformat(p["date"])
    digests = {team: team_digest(team, rows, t) for team, rows in p["teams"].items()}
    return render_md(p), digests


if __name__ == "__main__":
    p = plan()
    t = date.fromisoformat(p["date"])
    brief = render_md(p)
    BRIEFS.mkdir(parents=True, exist_ok=True)
    (BRIEFS / "teams").mkdir(parents=True, exist_ok=True)
    (BRIEFS / f"{today()}.md").write_text(brief, encoding="utf-8")
    (BRIEFS / f"{today()}.html").write_text(render_html(p), encoding="utf-8")
    (BRIEFS / f"{today()}.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
    for team, rows in p["teams"].items():
        (BRIEFS / "teams" / f"{slug(team)}.md").write_text(team_digest(team, rows, t), encoding="utf-8")
    print(brief)
