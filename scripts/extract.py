"""Turn meeting transcripts into JSON action cards and merge them into the tracker.

Usage:
  python scripts/extract.py                      # every transcript without a card yet
  python scripts/extract.py transcripts/2026-09-16-activity-tracker-sync.md

Accepts .md, .txt, .vtt, .srt and Teams .docx exports.
"""
import json
import sys
from pathlib import Path

import tracker
from common import CARDS, TRANSCRIPTS, TRANSCRIPT_EXTS, ask_json, date_from_filename, load_context, read_transcript

SYSTEM = """You are the AI employee of the person described in CONTEXT. You read their meeting
transcripts and extract action items exactly the way they would, applying their priority rules,
team roster and standing decisions strictly.

Reply with ONE JSON object and nothing else:
{
  "meeting": "short meeting name, e.g. Platform sync",
  "cards": [
    {
      "team": "team name from CONTEXT",
      "owner": "one person, full first name from the roster, or the team lead if unassigned",
      "task": "one sentence, imperative, specific enough that someone else could verify it's done",
      "due": "YYYY-MM-DD or null",
      "priority": "P1|P2|P3",
      "status": "open|in_progress|blocked",
      "blocked_by": "person or thing, or null",
      "evidence": "short verbatim quote (under 25 words) from the transcript",
      "matches_existing_id": "id of an OPEN item this is the same task as, else null",
      "update_note": "if matches_existing_id: what changed (slipped, reassigned, now blocked...), else null"
    }
  ]
}

Rules:
- Evidence must be a short excerpt; never include double quotes inside it.
- One card per commitment. Do not invent tasks. Do not include decisions with no follow-up.
- Use matches_existing_id whenever the transcript refers to an item already in OPEN ITEMS, even
  if worded differently. In that case only fill the fields that changed, plus update_note.
- Relative dates ("Friday", "end of week") must be resolved using the meeting date.
- If nothing actionable was said, return {"meeting": "...", "cards": []}."""

_STR_OR_NULL = {"anyOf": [{"type": "string"}, {"type": "null"}]}
CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "meeting": {"type": "string"},
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "team": {"type": "string"},
                    "owner": {"type": "string"},
                    "task": {"type": "string"},
                    "due": _STR_OR_NULL,
                    "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                    "status": {"type": "string", "enum": ["open", "in_progress", "blocked"]},
                    "blocked_by": _STR_OR_NULL,
                    "evidence": {"type": "string"},
                    "matches_existing_id": _STR_OR_NULL,
                    "update_note": _STR_OR_NULL,
                },
                "required": ["team", "owner", "task", "due", "priority", "status", "blocked_by",
                             "evidence", "matches_existing_id", "update_note"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["meeting", "cards"],
    "additionalProperties": False,
}


def build_prompt(transcript: str, meeting_date: str) -> str:
    open_items = [
        {k: r[k] for k in ("id", "team", "owner", "task", "due", "priority", "status")}
        for r in tracker.open_items()
    ]
    return (
        f"<context>\n{load_context()}\n</context>\n\n"
        f"<open_items>\n{json.dumps(open_items, indent=1)}\n</open_items>\n\n"
        f"<meeting_date>{meeting_date}</meeting_date>\n\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )


def process(path: Path) -> Path:
    text = read_transcript(path)
    meeting_date = date_from_filename(path, text)
    try:
        data = ask_json(SYSTEM, build_prompt(text, meeting_date), CARD_SCHEMA)
    except ValueError as e:  # includes JSONDecodeError; keep the raw text for debugging
        (CARDS / f"{path.stem}.raw.txt").write_text(str(e), encoding="utf-8")
        raise
    data.update({"date": meeting_date, "source": path.name})
    out = CARDS / f"{path.stem}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    c, u = tracker.upsert(data["cards"], data["meeting"], meeting_date, path.name)
    print(f"{path.name}: {len(data['cards'])} cards -> {c} created, {u} updated")
    return out


def pending() -> list[Path]:
    return sorted(
        p for p in TRANSCRIPTS.iterdir()
        if p.suffix.lower() in TRANSCRIPT_EXTS and not (CARDS / f"{p.stem}.json").exists()
    )


if __name__ == "__main__":
    targets = [Path(a) for a in sys.argv[1:]] or pending()
    if not targets:
        print("nothing to process")
    failed = []
    for t in targets:
        try:
            process(t)
        except Exception as e:  # keep going so one bad transcript does not block the rest
            failed.append(t.name)
            print(f"{t.name}: FAILED: {e}")
    if failed:
        print(f"{len(failed)} transcript(s) failed, will retry next run: {', '.join(failed)}")
        sys.exit(1)
