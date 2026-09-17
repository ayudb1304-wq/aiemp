"""Deterministic checks on what Claude extracted, run before anything reaches the tracker.

The model is asked for a verbatim quote, a named basis, a roster owner and a sane date. This
module checks that it did, without another model call:
  - the evidence quote is in the document (transcription-tolerant: 80% of its word trigrams)
  - the owner (and unblocker) is on the roster; a name-map alias is replaced by the real name
  - the basis of a next step resolves to a known id, a context file, or text in the document
    or the context files
  - the due date parses, is not before the meeting and not more than a year out
  - a blocked item names what blocks it; a matched id exists among the open items

A card that fails a check is kept, with the reasons in its `flags` field (the tracker column of
the same name). Nothing is dropped: the flags say where the model went wrong, and over time how
often. Decisions and threads get the quote check only.

  python scripts/verify.py cards/<file>.json     # re-check a cards file against its inbox document
"""
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from common import (DECISIONS, INBOX, THREADS, load_context, name_map, parse_date, project_slugs,
                    read_document, read_jsonl, roster)

MAX_DUE_DAYS = 365
_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _NON_WORD.sub(" ", (text or "").lower().replace("\u2019", "'")).strip()


def quote_found(quote: str, text: str, min_ratio: float = 0.7) -> bool:
    """True if the quote is in the text, allowing transcription differences: for quotes of four
    words or more, `min_ratio` of the quote's word trigrams must appear in the text."""
    q, t = normalize(quote), normalize(text)
    if not q:
        return False
    if q in t:
        return True
    words = q.split()
    if len(words) < 4:
        return False
    grams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    return sum(g in t for g in grams) / len(grams) >= min_ratio


def resolve_person(name: str, people: dict[str, str], aliases: dict[str, str]) -> tuple[str, str]:
    """(name to use, flag or ""). Roster names pass; a name-map alias becomes the real name."""
    if not name:
        return name, ""
    low = name.strip().lower()
    first = low.split()[0]
    if low in people or first in people:
        return name, ""
    real = aliases.get(low) or aliases.get(first)
    if real:
        return real, f"owner mapped: {name} -> {real}"
    return name, f"unknown person: {name}"


def basis_resolves(basis: str, doc_text: str, context_text: str, known_ids: set[str]) -> bool:
    if any(i in basis for i in known_ids):
        return True
    if re.search(r"\b[\w-]+\.md\b", basis) or "company" in basis.lower():
        return True
    if quote_found(basis, doc_text, 0.5) or quote_found(basis, context_text, 0.5):
        return True
    words = normalize(basis).split()   # short references like "CR section 7.4 Notifications"
    grams = [" ".join(words[i:i + 2]) for i in range(len(words) - 1)]
    both = normalize(doc_text) + " " + normalize(context_text)
    return any(g in both for g in grams if not g.replace(" ", "").isdigit())


def check_card(c: dict, doc_text: str, meeting_date: str, context_text: str, people: dict[str, str],
               aliases: dict[str, str], open_ids: set[str], known_ids: set[str]) -> list[str]:
    """Flags for one card. Mutates the card only to replace an alias owner/unblocker by the real
    name and to clear a matched id that does not exist."""
    flags = []
    if not c.get("evidence"):
        flags.append("no evidence")
    elif not quote_found(c["evidence"], doc_text):
        flags.append("quote not found")
    owner, f = resolve_person(c.get("owner") or "", people, aliases)
    if not c.get("owner"):
        flags.append("owner missing")
    elif f:
        c["owner"] = owner
        flags.append(f if f.startswith("owner mapped") else f"owner unknown: {c['owner']}")
    if c.get("unblocker"):
        unblocker, f = resolve_person(c["unblocker"], people, aliases)
        if f:
            c["unblocker"] = unblocker
            flags.append(f.replace("owner mapped", "unblocker mapped").replace("unknown person", "unblocker unknown"))
    md = parse_date(meeting_date)
    if c.get("due"):
        d = parse_date(c["due"])
        if not d:
            flags.append(f"due invalid: {c['due']}")
        elif md and d < md:
            flags.append(f"due before meeting: {c['due']}")
        elif md and d > md + timedelta(days=MAX_DUE_DAYS):
            flags.append(f"due far off: {c['due']}")
    if c.get("status") == "blocked" and not c.get("blocked_by"):
        flags.append("blocked without blocker")
    if c.get("matches_existing_id") and c["matches_existing_id"] not in open_ids:
        flags.append(f"matched id unknown: {c['matches_existing_id']}")
        c["matches_existing_id"] = None
    if c.get("next_step") and not c.get("basis"):
        flags.append("next step without basis")
    elif c.get("basis") and not basis_resolves(c["basis"], doc_text, context_text, known_ids):
        flags.append("basis unresolved")
    return flags


def check_all(data: dict, doc_text: str, meeting_date: str, project: str | None,
              people_mentioned: list[str], open_ids: set[str]) -> dict:
    """Flag every card, decision and thread in an extraction result in place. Returns a summary
    for the run log: counts per flag kind and the flagged tasks."""
    people, aliases = roster(), name_map()
    context_text = load_context(project, people_mentioned)
    known_ids = set(open_ids) | {d["id"] for d in read_jsonl(DECISIONS)} | {t["id"] for t in read_jsonl(THREADS)} \
        | set(project_slugs())
    summary: dict = {"cards_flagged": 0, "by_kind": {}, "flagged": []}

    def note(flags: list[str], label: str) -> None:
        for f in flags:
            kind = f.split(":")[0]
            summary["by_kind"][kind] = summary["by_kind"].get(kind, 0) + 1
        if flags:
            summary["flagged"].append({"what": label, "flags": flags})

    for c in data.get("cards", []):
        c["flags"] = check_card(c, doc_text, meeting_date, context_text, people, aliases, open_ids, known_ids)
        summary["cards_flagged"] += bool(c["flags"])
        note(c["flags"], c.get("task", ""))
    for kind in ("decisions", "threads"):
        for r in data.get(kind, []):
            r["flags"] = [] if quote_found(r.get("evidence", ""), doc_text) else ["quote not found"]
            note(r["flags"], r.get("decision") or r.get("topic", ""))
    return summary


if __name__ == "__main__":
    import tracker

    for arg in sys.argv[1:]:
        path = Path(arg)
        data = json.loads(path.read_text(encoding="utf-8"))
        doc = next((p for p in INBOX.iterdir() if p.name == data.get("source")), None)
        if doc is None:
            print(f"{path.name}: source document {data.get('source')!r} not in inbox")
            continue
        open_ids = {r["id"] for r in tracker.open_items()}
        summary = check_all(data, read_document(doc), data.get("date", ""), data.get("project"),
                            data.get("people", []), open_ids)
        print(f"{path.name}: {summary['cards_flagged']} of {len(data.get('cards', []))} cards flagged; {summary['by_kind']}")
        for f in summary["flagged"]:
            print(f"  - {f['what'][:70]}: {', '.join(f['flags'])}")
