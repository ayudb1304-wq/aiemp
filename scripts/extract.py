"""Turn any inbox document into action cards, decisions and threads, and merge them into memory.

Usage:
  python scripts/extract.py                                  # every inbox file without a cards file yet
  python scripts/extract.py inbox/2026-09-16-activity-tracker-sync.md

Accepts .md .txt .vtt .srt .docx .pdf .eml. Files named note-*.<ext> are unplanned intake
(quick notes) and get the lighter prompt; their items carry origin = unplanned.

Passes:
  1. triage (cheap): first ~2k tokens + project names -> project and people mentioned. Skipped
     when the file name already contains a project slug.
  2. extraction with the layered context (common.build_prompt_context). Documents that do not
     fit next to the context are chunked at paragraph boundaries; every chunk sees the cards
     produced so far and the results are merged. Nothing is truncated.
  3. drafts for communicate / review items (one call per document, only when there are any).

Guards: at most 3 P1 per document (Claude re-ranks once, then the earliest due dates win);
the agent never sets `done`; re-running the same file creates no duplicates. Every card is
fact-checked by scripts/verify.py (quote in document, owner on roster, basis resolves, due date
sane) and carries the failures in its `flags` field. Every run writes logs/<run-id>.json.
"""
import json
import sys
from pathlib import Path

import tracker
import verify
from common import (CARDS, CHUNK_TOKENS, DECISIONS, DRAFTS, EFFORTS, INBOX, INPUT_EXTS, KINDS, MODEL_INPUT_TOKENS,
                    STATUSES, THREADS, TYPES, UNASSIGNED, ask_json, build_prompt_context, chunk_text,
                    date_from_filename, est_tokens, is_note, load_context, now_id, people_in,
                    project_from_name, project_slugs, project_title, read_document, read_jsonl,
                    slug, warn, write_jsonl, write_run_log)

SYSTEM = """You are the AI employee of the person described in <context>. You read their documents
(meeting transcripts, notes, emails, chats, design docs) and extract action items, decisions and
open threads exactly the way they would, applying their priority rules, roster and standing
decisions strictly.

Reply with ONE JSON object matching the schema you were given:
- meeting: short name for the source, e.g. "Activity Tracker sync" or the email subject.
- kind: transcript | notes | email | doc | chat.
- cards: one per commitment. Fields:
  team, project (a slug from <context>, else "unassigned"), owner (one person, first name from the
  roster, or the team lead if unassigned), task (one sentence, imperative, verifiable), due
  (YYYY-MM-DD or null), priority (P1|P2|P3), status (open|in_progress|blocked|to_verify),
  blocked_by, evidence (verbatim quote under 25 words, no double quotes inside),
  matches_existing_id (id of an OPEN item this is the same task as, else null), update_note
  (if matched: what changed; else null),
  type (communicate|decide|build|review|coordinate|other),
  next_step (ONE sentence: the concrete next action that moves the item to done, or null),
  prerequisites (list of things that must exist first, or null), unblocker (the one person who
  can unblock it, or null), effort (15m|1h|half-day|day+|unknown),
  basis (the item id, decision id, transcript quote or context file the advice rests on, or null).
- decisions: a decision is a choice that closes a question ("we will / we won't / X is now Y"),
  not a task. Fields: decision (one sentence), by (who made it), evidence (quote), supersedes
  (id of an earlier decision in <history> on the same topic that this one contradicts, else null).
- threads: things said that are not commitments but might matter later (a flaky export, a
  concern, a maybe-later idea). Threshold is low. Fields: topic (2-5 words), note (under 30
  words), evidence (quote), matches_thread_id (id of a thread in <history> on the same topic,
  else null).

Rules:
- Evidence must be a short excerpt; never include double quotes inside it.
- One card per commitment. Do not invent tasks. Decisions with no follow-up go in decisions, not cards.
- Use matches_existing_id whenever the document refers to an item already in <open_items>, even
  if worded differently. Compare against the item's task, evidence and notes. In that case only
  fill the fields that changed, plus update_note.
- If the document says an OPEN item was completed, return it with matches_existing_id,
  status: to_verify, and the completion quote in update_note. Never return done.
- P1 only if it blocks the sign-off gate / start of development, or someone is blocked waiting
  on it. At most 3 P1 per document; if more qualify, keep the ones closest to the gate, the rest
  are P2.
- If you have no concrete basis in the provided context for next_step, set next_step to null.
  Generic advice is worse than none. basis must name where the advice comes from.
- Relative dates ("Friday", "end of week") must be resolved using <meeting_date>.
- <corrections> shows how the human corrected earlier cards. Follow those preferences.
- A decision in <history> with "current": false has been replaced by the decision named in
  replaced_by. Treat only current decisions as the rule; a replaced one is history, useful for
  supersedes and for recognising an old topic, never as a basis.
- If nothing actionable was said, return empty arrays."""

NOTE_SYSTEM = """You are the AI employee of the person described in <context>. The document is a quick
note they wrote for themselves (phone note, scratch list). Every line or paragraph that reads as a
task becomes one card; do not merge lines. Keep it light:
- kind is always "notes". meeting is a short label for the note.
- owner defaults to the author (the "Me" in <context>) unless the line names someone else.
- priority defaults to P2, status open, due null unless the line says otherwise.
- matches_existing_id if the line clearly refers to an item in <open_items>.
- type, effort and next_step as best you can; next_step null when you have no basis; basis
  must name the item id, decision id, quote or context file the advice rests on.
- decisions and threads are usually empty for notes; include one only if the line is clearly a
  decision ("we will X") or an observation worth remembering, not a task.
- Evidence is the line itself (under 25 words, no double quotes inside).
Reply with ONE JSON object matching the schema."""

TRIAGE_SYSTEM = """You read the start of a document and say which project it is about and who is
mentioned. Reply with ONE JSON object: project (one of the slugs given, or "unassigned" if none
fits), people (first names mentioned, as they appear in the roster if one is given)."""

RERANK_SYSTEM = """You apply this priority rule strictly: "P1 only if it blocks the sign-off gate or
the start of development, or someone is blocked waiting on it. At most 3 P1 per meeting. If more
qualify, keep the ones closest to the gate; the rest are P2." You are given the P1 cards from one
document. Reply with ONE JSON object: keep_p1, the indexes (0-based) of at most 3 cards that stay
P1. Everything else becomes P2."""

DRAFT_SYSTEM = """You are the AI employee of the person described in <context>. For each item in
<items> (type communicate or review) write the draft the item asks for: a message, review
comments, a summary, user stories, an agenda, whatever fits the task. Build it from the document
and the context only; do not invent facts. Address it to the right person, in the voice of the
item's owner. Keep it short and ready to paste. Reply with ONE JSON object: drafts, a list of
{id, title, content} in markdown. Drafts are never sent anywhere automatically."""

_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_STR_LIST_OR_NULL = {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
CARD_PROPS = {
    "team": {"type": "string"},
    "project": {"type": "string"},
    "owner": {"type": "string"},
    "task": {"type": "string"},
    "due": _STR_OR_NULL,
    "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
    "status": {"type": "string", "enum": STATUSES},
    "blocked_by": _STR_OR_NULL,
    "evidence": {"type": "string"},
    "matches_existing_id": _STR_OR_NULL,
    "update_note": _STR_OR_NULL,
    "type": {"type": "string", "enum": TYPES},
    "next_step": _STR_OR_NULL,
    "prerequisites": _STR_LIST_OR_NULL,
    "unblocker": _STR_OR_NULL,
    "effort": {"type": "string", "enum": EFFORTS},
    "basis": _STR_OR_NULL,
}
DECISION_PROPS = {
    "decision": {"type": "string"},
    "by": {"type": "string"},
    "evidence": {"type": "string"},
    "supersedes": _STR_OR_NULL,
}
THREAD_PROPS = {
    "topic": {"type": "string"},
    "note": {"type": "string"},
    "evidence": {"type": "string"},
    "matches_thread_id": _STR_OR_NULL,
}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


EXTRACT_SCHEMA = _obj({
    "meeting": {"type": "string"},
    "kind": {"type": "string", "enum": KINDS},
    "cards": {"type": "array", "items": _obj(CARD_PROPS)},
    "decisions": {"type": "array", "items": _obj(DECISION_PROPS)},
    "threads": {"type": "array", "items": _obj(THREAD_PROPS)},
})
TRIAGE_SCHEMA = _obj({"project": {"type": "string"}, "people": {"type": "array", "items": {"type": "string"}}})
RERANK_SCHEMA = _obj({"keep_p1": {"type": "array", "items": {"type": "integer"}}})
DRAFT_SCHEMA = _obj({"drafts": {"type": "array", "items": _obj({
    "id": {"type": "string"}, "title": {"type": "string"}, "content": {"type": "string"}})}})

OUTPUT_RESERVE = 12000  # system prompt + reply headroom inside the model window
MAX_P1 = 3


# ----------------------------------------------------------------------------- pass 1

def triage(text: str, path: Path) -> tuple[str | None, list[str], dict]:
    """(project slug or None, people, log). Cheap: 2k tokens of the document + project names."""
    slugs = project_slugs()
    people = people_in(text)
    known = project_from_name(path.name)
    if known:
        return known, people, {"triage": "from filename"}
    if not slugs:
        return None, people, {"triage": "no projects defined"}
    projects = "\n".join(f"- {s}: {project_title(s)}" for s in slugs)
    head = text[:8000]
    reply = ask_json(
        TRIAGE_SYSTEM,
        f"<projects>\n{projects}\n</projects>\n\n<document_start>\n{head}\n</document_start>",
        TRIAGE_SCHEMA, max_tokens=500)
    project = slug(reply.get("project", "")) if reply.get("project") else None
    if project and project not in slugs:
        warn(f"{path.name}: triage returned unknown project {project!r}; using {UNASSIGNED}")
        project = None
    for p in reply.get("people", []):
        if slug(p) not in [slug(x) for x in people]:
            people.append(p)
    return project, people, {"triage": "claude", "reply": reply}


# ----------------------------------------------------------------------------- pass 2

def _prompt(context_text: str, meeting_date: str, project: str | None, chunk: str,
            chunk_no: int, chunk_count: int, cards_so_far: list[dict]) -> str:
    parts = [context_text, f"<meeting_date>{meeting_date}</meeting_date>",
             f"<project>{project or UNASSIGNED}</project>"]
    if chunk_count > 1:
        parts.append(f"<cards_so_far>\n{json.dumps(cards_so_far, indent=1, ensure_ascii=False)}\n</cards_so_far>")
        parts.append(f"<document part=\"{chunk_no}/{chunk_count}\">\n{chunk}\n</document>")
    else:
        parts.append(f"<document>\n{chunk}\n</document>")
    return "\n\n".join(parts)


def merge_cards(so_far: list[dict], new: list[dict]) -> list[dict]:
    """Dedupe across chunks by matches_existing_id and by exact task text."""
    out = list(so_far)
    for c in new:
        dup = None
        for o in out:
            if c.get("matches_existing_id") and o.get("matches_existing_id") == c["matches_existing_id"]:
                dup = o
            elif c.get("task", "").strip().lower() == o.get("task", "").strip().lower():
                dup = o
            if dup:
                break
        if dup is None:
            out.append(c)
            continue
        for k, v in c.items():  # a later chunk may add detail (due, status, note)
            if v not in (None, "", []) and dup.get(k) in (None, "", []):
                dup[k] = v
        if c.get("update_note") and dup.get("update_note") and c["update_note"] != dup["update_note"]:
            dup["update_note"] = f"{dup['update_note']}; {c['update_note']}"
    return out


def enforce_p1_cap(cards: list[dict], log: dict) -> list[dict]:
    p1 = [i for i, c in enumerate(cards) if c.get("priority") == "P1"]
    if len(p1) <= MAX_P1:
        return cards
    log["p1_before_cap"] = len(p1)
    try:
        reply = ask_json(RERANK_SYSTEM,
                         "<p1_cards>\n" + json.dumps([cards[i] for i in p1], indent=1, ensure_ascii=False)
                         + "\n</p1_cards>", RERANK_SCHEMA, max_tokens=300)
        keep = [p1[k] for k in reply.get("keep_p1", []) if 0 <= k < len(p1)][:MAX_P1]
        log["p1_rerank"] = reply
    except Exception as e:  # the deterministic fallback below still applies
        warn(f"P1 re-rank failed: {e}")
        keep = []
    keep = list(dict.fromkeys(keep))  # unique, order kept
    if not keep or len(keep) > MAX_P1:
        # Still more than 3 (or no usable answer): keep the 3 with the earliest due, no due last
        keep = sorted(p1, key=lambda i: cards[i].get("due") or "9999-99-99")[:MAX_P1]
        log["p1_fallback"] = "earliest due"
    downgraded = []
    for i in p1:
        if i not in keep:
            cards[i]["priority"] = "P2"
            cards[i]["update_note"] = ((cards[i].get("update_note") or "") + " (P1 cap: downgraded to P2)").strip()
            downgraded.append(cards[i].get("task"))
    log["p1_downgraded"] = downgraded
    for t in downgraded:
        print(f"  P1 cap: downgraded to P2: {t}")
    return cards


def extract(text: str, meeting_date: str, project: str | None, people: list[str],
            system: str, log: dict) -> dict:
    context_text, ctx_log = build_prompt_context(project, people, text)
    log["context"] = ctx_log
    room = MODEL_INPUT_TOKENS - ctx_log["context_tokens"] - OUTPUT_RESERVE
    chunks = chunk_text(text, CHUNK_TOKENS) if est_tokens(text) > room else [text]
    log["chunks"] = len(chunks)
    data = {"meeting": "", "kind": "", "cards": [], "decisions": [], "threads": []}
    for i, chunk in enumerate(chunks, 1):
        reply = ask_json(system, _prompt(context_text, meeting_date, project, chunk, i, len(chunks),
                                         data["cards"]), EXTRACT_SCHEMA)
        data["meeting"] = data["meeting"] or reply.get("meeting", "")
        data["kind"] = data["kind"] or reply.get("kind", "")
        data["cards"] = merge_cards(data["cards"], reply.get("cards", []))
        data["decisions"] += reply.get("decisions", [])
        data["threads"] += reply.get("threads", [])
    data["cards"] = enforce_p1_cap(data["cards"], log)
    return data


# ----------------------------------------------------------------------------- memory

def _next_memory_id(records: list[dict], prefix: str) -> str:
    n = sum(1 for r in records if r["id"].startswith(prefix)) + 1
    return f"{prefix}{n:02d}"


def record_decisions(decisions: list[dict], project: str, meeting_date: str, source: str) -> list[str]:
    records = read_jsonl(DECISIONS)
    ids = {r["id"] for r in records}
    out = []
    for d in decisions:
        if any(r["source"] == source and r["decision"] == d.get("decision") for r in records):
            continue  # same file processed again
        sup = d.get("supersedes") if d.get("supersedes") in ids else None
        rec = {"id": _next_memory_id(records, f"D-{meeting_date}-"), "date": meeting_date,
               "project": project, "decision": d.get("decision", ""), "by": d.get("by", ""),
               "evidence": d.get("evidence", ""), "source": source, "supersedes": sup}
        records.append(rec)
        ids.add(rec["id"])
        out.append(rec["id"])
    write_jsonl(DECISIONS, records)
    return out


def record_threads(threads: list[dict], project: str, meeting_date: str, source: str) -> list[str]:
    records = read_jsonl(THREADS)
    by_id = {r["id"]: r for r in records}
    out = []
    for t in threads:
        match = by_id.get(t.get("matches_thread_id") or "")
        if match:
            if source in match.setdefault("sources", [match["source"]]):
                continue  # same file processed again
            match["mentions"] = int(match.get("mentions", 1)) + 1
            match["last_seen"] = max(match.get("last_seen", ""), meeting_date)
            match["sources"].append(source)
            if t.get("note"):
                match["note"] = t["note"]
            out.append(match["id"])
            continue
        if any(r["source"] == source and r["topic"] == t.get("topic") for r in records):
            continue
        rec = {"id": _next_memory_id(records, f"T-{meeting_date}-"), "date": meeting_date,
               "project": project, "topic": t.get("topic", ""), "note": t.get("note", ""),
               "evidence": t.get("evidence", ""), "source": source, "mentions": 1,
               "last_seen": meeting_date, "promoted_to": None, "sources": [source]}
        records.append(rec)
        by_id[rec["id"]] = rec
        out.append(rec["id"])
    write_jsonl(THREADS, records)
    return out


# ----------------------------------------------------------------------------- drafts

def write_drafts(ids: list[str], text: str, project: str | None, people: list[str],
                 source: str, meeting_date: str, log: dict) -> list[str]:
    rows = {r["id"]: r for r in tracker.load_rows()}
    items = [rows[i] for i in ids if i in rows and rows[i].get("type") in ("communicate", "review")
             and not (DRAFTS / f"{i}.md").exists()]
    if not items:
        return []
    doc = text
    if est_tokens(text) > MODEL_INPUT_TOKENS - 30000:  # keep only the chunks the items quote
        chunks = chunk_text(text, CHUNK_TOKENS)
        keep = [ch for ch in chunks if any((it["evidence"] or "")[:30].lower() in ch.lower() for it in items)]
        doc = "\n\n[...]\n\n".join(keep or chunks[:1])
    fields = ("id", "owner", "task", "type", "next_step", "unblocker", "evidence", "basis")
    reply = ask_json(DRAFT_SYSTEM,
                     f"<context>\n{load_context(project, people)}\n</context>\n\n"
                     f"<items>\n{json.dumps([{k: it[k] for k in fields} for it in items], indent=1, ensure_ascii=False)}\n</items>\n\n"
                     f"<document>\n{doc}\n</document>", DRAFT_SCHEMA, max_tokens=16000)
    written = []
    DRAFTS.mkdir(parents=True, exist_ok=True)
    for d in reply.get("drafts", []):
        if d["id"] not in rows:
            continue
        r = rows[d["id"]]
        (DRAFTS / f"{d['id']}.md").write_text(
            f"# {d.get('title') or r['task']}\n\n_Draft for `{d['id']}` ({r['owner']}: {r['task']}). "
            f"Written {meeting_date} from {source}. Not sent anywhere; copy what you need._\n\n"
            f"{d.get('content', '').strip()}\n", encoding="utf-8")
        written.append(d["id"])
    log["drafts"] = written
    return written


# ----------------------------------------------------------------------------- one file

def process(path: Path) -> Path:
    run_id = f"{now_id()}-{slug(path.stem)}"
    log = {"run_id": run_id, "source": path.name}
    text = read_document(path)
    meeting_date = date_from_filename(path, text)
    note = is_note(path)
    project, people, tlog = triage(text, path)
    log.update(tlog, project=project, people=people, date=meeting_date, note=note)
    try:
        data = extract(text, meeting_date, project, people, NOTE_SYSTEM if note else SYSTEM, log)
    except ValueError as e:  # includes JSONDecodeError; keep the raw text for debugging
        (CARDS / f"{path.stem}.raw.txt").write_text(str(e), encoding="utf-8")
        write_run_log(run_id, {**log, "error": str(e)})
        raise
    if note:
        data["kind"] = "notes"
    proj = project or UNASSIGNED
    for c in data["cards"]:
        if not c.get("project") or slug(c["project"]) not in project_slugs():
            c["project"] = proj
    origin = "unplanned" if note else "planned"
    open_ids = {r["id"] for r in tracker.open_items()}
    log["checks"] = verify.check_all(data, text, meeting_date, project, people, open_ids)
    for f in log["checks"]["flagged"]:
        print(f"  flagged: {f['what'][:60]}: {', '.join(f['flags'])}")
    data.update({"date": meeting_date, "source": path.name, "project": proj, "people": people,
                 "origin": origin, "run_id": run_id})
    created, updated = tracker.upsert(data["cards"], data["meeting"], meeting_date, path.name,
                                      project=proj, origin=origin)
    data["decision_ids"] = record_decisions(data["decisions"], proj, meeting_date, path.name)
    data["thread_ids"] = record_threads(data["threads"], proj, meeting_date, path.name)
    out = CARDS / f"{path.stem}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.update(cards=len(data["cards"]), created=created, updated=updated,
               decisions=data["decision_ids"], threads=data["thread_ids"])
    try:
        write_drafts(created + updated, text, project, people, path.name, meeting_date, log)
    except Exception as e:  # a failed draft must not lose the cards already saved
        warn(f"{path.name}: drafts failed: {e}")
        log["drafts_error"] = str(e)
    write_run_log(run_id, log)
    print(f"{path.name}: {data['kind']} / {proj}: {len(data['cards'])} cards -> {len(created)} created, "
          f"{len(updated)} updated; {len(data['decision_ids'])} decisions, {len(data['thread_ids'])} threads"
          f" (log {run_id})")
    return out


def pending() -> list[Path]:
    if not INBOX.exists():
        return []
    return sorted(
        p for p in INBOX.iterdir()
        if p.suffix.lower() in INPUT_EXTS and not (CARDS / f"{p.stem}.json").exists()
    )


if __name__ == "__main__":
    targets = [Path(a) for a in sys.argv[1:]] or pending()
    if not targets:
        print("nothing to process")
    failed = []
    for t in targets:
        try:
            process(t)
        except Exception as e:  # keep going so one bad file does not block the rest
            failed.append(t.name)
            print(f"{t.name}: FAILED: {e}")
    if failed:
        print(f"{len(failed)} file(s) failed, will retry next run: {', '.join(failed)}")
        sys.exit(1)
