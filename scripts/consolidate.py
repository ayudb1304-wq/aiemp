"""Weekly consolidation: rewrite each active project's state file and propose rule changes.

  python scripts/consolidate.py --dry-run     # print the proposed state files and PR body, touch nothing
  python scripts/consolidate.py               # write context/projects/<slug>.md and logs/consolidate-<date>.md

Never commits. The consolidate workflow puts the result on a branch consolidate/<date> and opens a
pull request; nothing reaches main without a human. company.md rules are never edited here: the
corrections log becomes a "Suggested rule changes" section of the PR body.

For each project with activity in the last 7 days (cards, decisions, threads, briefs), Claude
reads the current state file and the week's activity and REWRITES the file (goal, phase, next
milestone, risks, key people, glossary). Old content is replaced, not appended; git history is the
archive. The "Recent decisions" section (last 10 non-superseded) and the closed-item observations
are computed here, deterministically. Without ANTHROPIC_API_KEY only those deterministic parts run.
"""
import json
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta

import tracker
from common import (BRIEFS, CARDS, CORRECTIONS, DECISIONS, LOGS, PROJECTS_DIR, THREADS, ask_json,
                    brief_sections, have_key, load_context, parse_date, project_slugs, project_title,
                    read_jsonl, slug, today)

REWRITE_SYSTEM = """You maintain the state file of one project for the AI employee described in
<company>. You are given the CURRENT state file and everything that happened this week (cards,
decisions, threads, brief sections). Rewrite the state file so it describes the project as it is
NOW: keep the same headings (# title, ## Goal, ## Phase, ## Next milestone, ## Risks, ## Key people,
## Glossary), replace stale content, drop what is no longer true, keep it under 80 lines. Do NOT
include a "Recent decisions" section; it is appended by the script. Do not invent facts: every
change must come from the week's records or the current file. Reply with ONE JSON object:
state_file (the full markdown), changes (2-4 bullet lines summarising what changed and the
evidence: card ids, decision ids, quotes)."""

RULES_SYSTEM = """You review how the human corrected the AI employee's cards this week (the
corrections log: field, from, to) against the current rules in <company>. Propose rule changes
only where the corrections show a repeated pattern (2+ similar corrections). Each suggestion must
cite the correction ids. Never propose deleting a rule outright; propose the new wording. Reply
with ONE JSON object: suggestions, a list of {rule (the section or line in company.md), change
(proposed new wording), basis (correction ids and what they show)}. Empty list if no pattern."""

_STR = {"type": "string"}
REWRITE_SCHEMA = {"type": "object", "properties": {"state_file": _STR, "changes": _STR},
                  "required": ["state_file", "changes"], "additionalProperties": False}
RULES_SCHEMA = {"type": "object", "properties": {"suggestions": {"type": "array", "items": {
    "type": "object", "properties": {"rule": _STR, "change": _STR, "basis": _STR},
    "required": ["rule", "change", "basis"], "additionalProperties": False}}},
    "required": ["suggestions"], "additionalProperties": False}


def _in_window(d: str, since: str) -> bool:
    return bool(d) and d >= since


def week_activity(project: str, since: str) -> dict:
    cards = []
    for p in sorted(CARDS.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if slug(data.get("project", "")) == project and _in_window(data.get("date", ""), since):
            cards.append({"source": data.get("source"), "date": data.get("date"), "kind": data.get("kind"),
                          "cards": [{k: c.get(k) for k in ("owner", "task", "due", "priority", "status",
                                                          "matches_existing_id", "update_note")}
                                    for c in data.get("cards", [])]})
    decisions = [d for d in read_jsonl(DECISIONS) if slug(d.get("project", "")) == project
                 and _in_window(d.get("date", ""), since)]
    threads = [t for t in read_jsonl(THREADS) if slug(t.get("project", "")) == project
               and _in_window(t.get("last_seen") or t.get("date", ""), since)]
    briefs = brief_sections(project, n=5) if BRIEFS.exists() else ""
    return {"cards": cards, "decisions": decisions, "threads": threads, "briefs": briefs}


def recent_decisions_section(project: str, n: int = 10) -> str:
    recs = read_jsonl(DECISIONS)
    superseded = {r["supersedes"] for r in recs if r.get("supersedes")}
    mine = [r for r in recs if slug(r.get("project", "")) == project and r["id"] not in superseded]
    mine.sort(key=lambda r: r["date"], reverse=True)
    lines = ["## Recent decisions"]
    if not mine:
        lines.append("_Rewritten by scripts/consolidate.py from memory/decisions.jsonl._")
    for r in mine[:n]:
        lines.append(f"- {r['date']} `{r['id']}`: {r['decision']} (by {r['by']})")
    return "\n".join(lines) + "\n"


def strip_recent_decisions(text: str) -> str:
    head, sep, _ = text.partition("## Recent decisions")
    return head.rstrip() + "\n"


def rewrite_state(project: str, activity: dict, current: str) -> tuple[str, str]:
    """(new body without Recent decisions, changes summary)."""
    if not have_key():
        return strip_recent_decisions(current), "no API key: state text unchanged, recent decisions refreshed"
    reply = ask_json(REWRITE_SYSTEM,
                     f"<company>\n{load_context()}\n</company>\n\n<current_state_file>\n{current}\n</current_state_file>\n\n"
                     f"<week>\n{json.dumps(activity, indent=1, ensure_ascii=False, default=str)}\n</week>",
                     REWRITE_SCHEMA, max_tokens=8000)
    return strip_recent_decisions(reply["state_file"].strip() + "\n"), reply.get("changes", "")


def closed_item_patterns(project: str, rows: list[dict]) -> list[str]:
    """Observations: median days-to-close by type and by unblocker, plus the most slipped items."""
    mine = [r for r in rows if slug(r.get("project", "")) == project]
    done = [r for r in mine if r["status"] == "done" and parse_date(r.get("closed")) and parse_date(r.get("created"))]
    out = []
    by_type, by_unb = defaultdict(list), defaultdict(list)
    for r in done:
        days = (parse_date(r["closed"]) - parse_date(r["created"])).days
        by_type[r.get("type") or "untyped"].append(days)
        if r.get("unblocker"):
            by_unb[r["unblocker"]].append(days)
    for label, groups in (("type", by_type), ("unblocker", by_unb)):
        for k, v in sorted(groups.items(), key=lambda kv: -statistics.median(kv[1])):
            out.append(f"- items by {label} **{k}** take a median of {statistics.median(v):.0f} days to close"
                       f" (n={len(v)})" + (" -> raise them day one" if statistics.median(v) >= 3 else ""))
    slips = sorted(((tracker.slip_count(r)[0], r) for r in mine), key=lambda x: -x[0])
    slips = [(n, r) for n, r in slips if n >= 2][:5]
    for n, r in slips:
        out.append(f"- `{r['id']}` slipped {n} times ({r['owner']}: {r['task'][:60]})")
    if not done and not slips:
        out.append("- no closed items yet, nothing to learn from")
    return out


def suggested_rule_changes(since: str) -> list[str]:
    corrections = [c for c in read_jsonl(CORRECTIONS) if _in_window(c.get("date", ""), since)]
    if not corrections:
        return ["- no corrections this week"]
    by_field = defaultdict(int)
    for c in corrections:
        by_field[c.get("field", "?")] += 1
    lines = ["- corrections this week: " + ", ".join(f"{k} x{v}" for k, v in sorted(by_field.items()))]
    if not have_key():
        return lines + ["- (no API key: no wording proposed)"]
    numbered = [{"n": i + 1, **c} for i, c in enumerate(corrections)]
    reply = ask_json(RULES_SYSTEM,
                     f"<company>\n{load_context()}\n</company>\n\n<corrections>\n"
                     f"{json.dumps(numbered, indent=1, ensure_ascii=False)}\n</corrections>",
                     RULES_SCHEMA, max_tokens=3000)
    for s in reply.get("suggestions", []):
        lines.append(f"- **{s['rule']}**: {s['change']}\n  - basis: {s['basis']}")
    return lines


def run(dry_run: bool, days: int = 7, on: str | None = None) -> dict[str, str]:
    t = parse_date(on) or date.today()
    since = (t - timedelta(days=days)).isoformat()
    rows = tracker.load_rows()
    proposals, body = {}, [f"# Consolidation {t.isoformat()}", "",
                           f"_Activity since {since}. Proposal only: review and merge, or close._", ""]
    for project in project_slugs():
        activity = week_activity(project, since)
        active = any((activity["cards"], activity["decisions"], activity["threads"]))
        if not active:
            continue
        path = PROJECTS_DIR / f"{project}.md"
        current = path.read_text(encoding="utf-8")
        new_body, changes = rewrite_state(project, activity, current)
        proposed = new_body.rstrip() + "\n\n" + recent_decisions_section(project)
        proposals[project] = proposed
        body += [f"## {project_title(project)} (`context/projects/{project}.md`)", "",
                 f"{len(activity['cards'])} documents, {len(activity['decisions'])} decisions, "
                 f"{len(activity['threads'])} threads this week.", "", changes, "", "### Observations"]
        body += closed_item_patterns(project, rows) + [""]
    body += ["## Suggested rule changes (context/company.md, not applied)", ""]
    body += suggested_rule_changes(since) + [""]
    pr_body = "\n".join(body)
    if dry_run:
        for project, text in proposals.items():
            print(f"===== proposed context/projects/{project}.md =====\n{text}")
        print(f"===== pull request body =====\n{pr_body}")
        print(f"dry run: {len(proposals)} project file(s) would change; nothing written")
        return proposals
    for project, text in proposals.items():
        (PROJECTS_DIR / f"{project}.md").write_text(text, encoding="utf-8")
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f"consolidate-{t.isoformat()}.md").write_text(pr_body, encoding="utf-8")
    print(f"consolidate: rewrote {len(proposals)} project file(s); PR body in logs/consolidate-{t.isoformat()}.md")
    return proposals


if __name__ == "__main__":
    args = sys.argv[1:]
    run(dry_run="--dry-run" in args)
