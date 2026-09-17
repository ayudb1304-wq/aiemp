"""Meeting prep pack: one page from what the memory already knows, before you walk in.

  python scripts/prep.py Laxmikant                  # one person
  python scripts/prep.py Laxmikant Samuel           # several people
  python scripts/prep.py --project intelligent-tracker
  python scripts/prep.py --project lsp Adwaith      # both

Per person: what they own (open, ranked as in the brief), what is waiting on them (they are the
unblocker or named in blocked_by), what they reported complete and you have not confirmed, their
last commitments as quoted in the source documents, decisions they made (current first), threads
they keep raising, slips, and drafts ready to paste. Per project: open items, decisions in force,
active threads and who holds how much.

Nothing is generated: every line carries an id, a date or a quote from a document. Writes
briefs/prep/<date>-<slug>.md and .html and prints the markdown. No API key needed.
"""
import sys
from collections import Counter
from datetime import date

import brief
import search
import tracker
from common import (BRIEFS, CARDS, DRAFTS, THREADS, current_decisions, decision_chains, DECISIONS, name_map,
                    project_title, read_jsonl, roster, slug, today)


def _same(a: str, b: str) -> bool:
    return slug((a or "").split()[0] if a else "") == slug((b or "").split()[0] if b else "")


def canonical(name: str) -> str:
    """The roster spelling of a name, via the name map if needed."""
    low = name.strip().lower()
    real = name_map().get(low) or name_map().get(low.split()[0])
    if real:
        return real
    people = roster()
    return people.get(low.split()[0], name.strip())


def commitments(person: str, n: int = 6) -> list[dict]:
    """The person's most recent cards across all processed documents, with the quote."""
    out = []
    import json

    for p in sorted(CARDS.glob("*.json")) if CARDS.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for c in data.get("cards", []):
            if _same(c.get("owner", ""), person):
                out.append({"date": data.get("date", ""), "source": data.get("source", p.name),
                            "task": c.get("task", ""), "evidence": c.get("evidence", ""),
                            "status": c.get("status", ""), "id": c.get("matches_existing_id") or ""})
    out.sort(key=lambda c: c["date"], reverse=True)
    return out[:n]


def person_pack(person: str, t: date) -> dict:
    items = tracker.open_items()
    me = brief.my_name()
    mine = sorted([r for r in items if _same(r["owner"], person)], key=lambda r: brief._score(r, t))
    waiting_on = [r for r in items if not _same(r["owner"], person)
                  and (_same(r.get("unblocker", ""), person) or person.lower() in (r.get("blocked_by") or "").lower())]
    waiting_on_me = [r for r in mine if _same(r.get("unblocker", ""), me)]
    decisions = [d for d in decision_chains(read_jsonl(DECISIONS)) if _same(d.get("by", ""), person)]
    decisions.sort(key=lambda d: (not d["current"], d.get("date", "")), reverse=False)
    decisions.sort(key=lambda d: d.get("date", ""), reverse=True)
    decisions.sort(key=lambda d: not d["current"])
    threads = [h["record"] for h in search.search(person, kinds=("thread",), limit=8)]
    return {
        "person": person, "date": t.isoformat(),
        "owns": mine,
        "to_verify": [r for r in mine if r["status"] == "to_verify"],
        "waiting_on_them": waiting_on,
        "waiting_on_me": waiting_on_me,
        "slipped": [(r, *tracker.slip_count(r)) for r in mine if tracker.slip_count(r)[0] >= 1],
        "commitments": commitments(person),
        "decisions": decisions[:8],
        "threads": threads,
        "drafts": [r for r in mine if (DRAFTS / f"{r['id']}.md").exists()],
    }


def project_pack(project: str, t: date) -> dict:
    items = sorted(tracker.open_items(project=project), key=lambda r: brief._score(r, t))
    due_soon = [r for r in items if (d := tracker.parse_date(r["due"])) and (d - t).days <= 2]
    threads = [th for th in read_jsonl(THREADS) if slug(th.get("project", "")) == slug(project) and not th.get("promoted_to")]
    threads.sort(key=lambda th: (-int(th.get("mentions", 1)), th.get("last_seen", "")), reverse=False)
    return {
        "project": project, "title": project_title(project), "date": t.isoformat(),
        "due_soon": due_soon, "rest": [r for r in items if r not in due_soon],
        "decisions": current_decisions(project)[:10],
        "threads": threads[:10],
        "load": Counter(r["owner"] or "Unassigned" for r in items).most_common(),
        "flagged": [r for r in items if r.get("flags")],
    }


# ----------------------------------------------------------------------------- markdown

def _md_person(pk: dict, t: date) -> list[str]:
    out = [f"## {pk['person']}", ""]
    out.append(f"### Owns ({len(pk['owns'])} open)")
    out += [brief._line(r, t) for r in pk["owns"]] or ["_Nothing open._"]
    if pk["to_verify"]:
        out += ["", "### Reported complete, waiting for your confirmation"]
        out += [f"- `{r['id']}` {r['task']} (\"{brief._completion_quote(r)}\")" for r in pk["to_verify"]]
    if pk["waiting_on_them"]:
        out += ["", f"### Waiting on {pk['person']}"]
        out += [brief._line(r, t) for r in pk["waiting_on_them"]]
    if pk["waiting_on_me"]:
        out += ["", "### Their items waiting on you"]
        out += [brief._line(r, t) for r in pk["waiting_on_me"]]
    if pk["slipped"]:
        out += ["", "### Slips"]
        out += [f"- `{r['id']}` {r['task']}: due moved {n}x since {first}" for r, n, first in pk["slipped"]]
    out += ["", "### Last commitments, as said"]
    out += [f"- {c['date']}: {c['task']} (\"{c['evidence']}\", {c['source']})" for c in pk["commitments"]] or ["_None recorded._"]
    if pk["decisions"]:
        out += ["", "### Decisions they made"]
        out += [f"- `{d['id']}` {d['decision']} ({d['date']}{'' if d['current'] else ', replaced by ' + d['replaced_by']})"
                for d in pk["decisions"]]
    if pk["threads"]:
        out += ["", "### Threads mentioning them"]
        out += [f"- `{th['id']}` **{th['topic']}** ({th['mentions']}x, last {th['last_seen']}): {th['note']}" for th in pk["threads"]]
    if pk["drafts"]:
        out += ["", "### Drafts ready"]
        out += [f"- `{r['id']}` {r['task']} [draft](../../drafts/{r['id']}.md)" for r in pk["drafts"]]
    return out + [""]


def _md_project(pp: dict, t: date) -> list[str]:
    out = [f"## Project: {pp['title']}", "",
           "_" + ", ".join(f"{o}: {n}" for o, n in pp["load"]) + "_" if pp["load"] else "_No open items._", ""]
    if pp["decisions"]:
        out += ["### Decisions in force"] + [f"- `{d['id']}` {d['decision']} (by {d['by']}, {d['date']})" for d in pp["decisions"]] + [""]
    if pp["due_soon"]:
        out += ["### Due today / overdue"] + [brief._line(r, t) for r in pp["due_soon"]] + [""]
    if pp["rest"]:
        out += ["### Everything else open"] + [brief._line(r, t) for r in pp["rest"]] + [""]
    if pp["threads"]:
        out += ["### Active threads"] + [f"- `{th['id']}` **{th['topic']}** ({th['mentions']}x, last {th['last_seen']}): {th['note']}"
                                         for th in pp["threads"]] + [""]
    if pp["flagged"]:
        out += ["### Flagged by the fact checks"] + [f"- `{r['id']}` {r['task']} -> {r['flags']}" for r in pp["flagged"]] + [""]
    return out


def render_md(packs: list[dict], t: date) -> str:
    out = [f"# Meeting prep: {t.strftime('%A %d %b %Y')}", ""]
    for pk in packs:
        out += _md_person(pk, t) if "person" in pk else _md_project(pk, t)
    return "\n".join(out)


# ----------------------------------------------------------------------------- html

def _ul(lines: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{ln}</li>" for ln in lines) + "</ul>"


def _html_person(pk: dict, t: date, section) -> None:
    h = brief._h
    body = ""
    if pk["to_verify"]:
        body += "<h3>Reported complete, waiting for your confirmation</h3>" + _ul(
            [f"{h(r['task'])} <span class='quote'>“{h(brief._completion_quote(r))}”</span> <span class='id'>{h(r['id'])}</span>"
             for r in pk["to_verify"]])
    body += "<h3>Owns</h3>" + (brief._table(pk["owns"], t) if pk["owns"] else "<p class='empty'>Nothing open.</p>")
    if pk["waiting_on_them"]:
        body += f"<h3>Waiting on {h(pk['person'])}</h3>" + brief._table(pk["waiting_on_them"], t)
    if pk["waiting_on_me"]:
        body += "<h3>Their items waiting on you</h3>" + brief._table(pk["waiting_on_me"], t)
    if pk["slipped"]:
        body += "<h3>Slips</h3>" + _ul([f"{h(r['task'])} <span class='pill slip'>due moved {n}x since {h(first)}</span>"
                                        for r, n, first in pk["slipped"]])
    body += "<h3>Last commitments, as said</h3>" + (_ul(
        [f"<b>{h(c['date'])}</b> {h(c['task'])}<br><span class='quote'>“{h(c['evidence'])}”</span> "
         f"<span class='id'>{h(c['source'])}</span>" for c in pk["commitments"]]) if pk["commitments"] else "<p class='empty'>None recorded.</p>")
    if pk["decisions"]:
        body += "<h3>Decisions they made</h3>" + _ul(
            [f"{h(d['decision'])} <span class='quote'>{h(d['date'])}</span> "
             + ("" if d["current"] else f"<span class='pill slip'>replaced by {h(d['replaced_by'])}</span> ")
             + f"<span class='id'>{h(d['id'])}</span>" for d in pk["decisions"]])
    if pk["threads"]:
        body += "<h3>Threads mentioning them</h3>" + _ul(
            [f"<b>{h(th['topic'])}</b> <span class='pill slip'>{h(th['mentions'])}x, last {h(th['last_seen'])}</span> {h(th['note'])} "
             f"<span class='id'>{h(th['id'])}</span>" for th in pk["threads"]])
    if pk["drafts"]:
        body += "<h3>Drafts ready</h3>" + _ul([f"<a class='pill draft' href='../../drafts/{h(r['id'])}.md'>draft</a> {h(r['task'])}" for r in pk["drafts"]])
    section(pk["person"], len(pk["owns"]), f"{len(pk['waiting_on_them'])} waiting on them, {len(pk['to_verify'])} to verify, "
            f"{len(pk['slipped'])} slipped", body)


def _html_project(pp: dict, t: date, section) -> None:
    h = brief._h
    body = ""
    if pp["decisions"]:
        body += "<h3>Decisions in force</h3>" + _ul(
            [f"{h(d['decision'])} <span class='quote'>by {h(d['by'])}, {h(d['date'])}</span> <span class='id'>{h(d['id'])}</span>"
             for d in pp["decisions"]])
    if pp["due_soon"]:
        body += "<h3>Due today / overdue</h3>" + brief._table(pp["due_soon"], t)
    if pp["rest"]:
        body += "<h3>Everything else open</h3>" + brief._table(pp["rest"], t)
    if pp["threads"]:
        body += "<h3>Active threads</h3>" + _ul(
            [f"<b>{h(th['topic'])}</b> <span class='pill slip'>{h(th['mentions'])}x, last {h(th['last_seen'])}</span> {h(th['note'])} "
             f"<span class='id'>{h(th['id'])}</span>" for th in pp["threads"]])
    if pp["flagged"]:
        body += "<h3>Flagged by the fact checks</h3>" + _ul(
            [f"{h(r['task'])} <span class='pill verify'>{h(r['flags'])}</span> <span class='id'>{h(r['id'])}</span>" for r in pp["flagged"]])
    section(f"Project: {pp['title']}", len(pp["due_soon"]) + len(pp["rest"]),
            ", ".join(f"{o}: {n}" for o, n in pp["load"]) or "no open items", body or "<p class='empty'>Nothing open.</p>")


def render_html(packs: list[dict], t: date) -> str:
    h = brief._h
    who = ", ".join(pk.get("person") or pk.get("title") for pk in packs)
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Prep {h(t.isoformat())} {h(who)}</title><style>{brief.HTML_CSS}</style></head><body>",
             f"<div class='hero'><div class='kicker'>Meeting prep</div><h1>{h(who)}</h1>"
             f"<div class='sub'>{h(t.strftime('%A %d %b %Y'))}. Every line carries an id, a date or a quote; nothing is generated.</div></div><main>"]

    def section(title, count, hint, body):
        parts.append(f"<section><h2>{h(title)}<span class='count'>{count}</span></h2><p class='hint'>{h(hint)}</p>{body}</section>")

    for pk in packs:
        (_html_person if "person" in pk else _html_project)(pk, t, section)
    parts.append("<footer>Built from tracker/actions.xlsx, cards/, memory/ and drafts/.</footer></main></body></html>")
    return "".join(parts)


def build(names: list[str], projects: list[str], t: date | None = None) -> tuple[list[dict], str, str]:
    t = t or date.today()
    packs = [project_pack(p, t) for p in projects] + [person_pack(canonical(n), t) for n in names]
    return packs, render_md(packs, t), render_html(packs, t)


def main(argv: list[str]) -> int:
    names, projects = [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--project" and i + 1 < len(argv):
            projects.append(slug(argv[i + 1]))
            i += 2
        else:
            names.append(argv[i])
            i += 1
    if not names and not projects:
        print(__doc__)
        return 1
    packs, md, page = build(names, projects)
    out = BRIEFS / "prep"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{today()}-" + "-".join(slug(pk.get("project") or pk["person"]) for pk in packs)
    (out / f"{stem}.md").write_text(md, encoding="utf-8")
    (out / f"{stem}.html").write_text(page, encoding="utf-8")
    print(md)
    print(f"\n_written: {out / (stem + '.md')} and .html_")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
