"""Turn meeting transcripts into JSON action cards and merge them into the tracker.

Usage:
  python scripts/extract.py                      # every transcript without a card yet
  python scripts/extract.py transcripts/2026-09-16-platform-sync.md
"""
import json
import sys
from pathlib import Path

import tracker
from common import CARDS, TRANSCRIPTS, ask, date_from_filename, load_context, parse_json

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
- One card per commitment. Do not invent tasks. Do not include decisions with no follow-up.
- Use matches_existing_id whenever the transcript refers to an item already in OPEN ITEMS, even
  if worded differently. In that case only fill the fields that changed, plus update_note.
- Relative dates ("Friday", "end of week") must be resolved using the meeting date.
- If nothing actionable was said, return {"meeting": "...", "cards": []}."""


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
    meeting_date = date_from_filename(path)
    reply = ask(SYSTEM, build_prompt(path.read_text(encoding="utf-8"), meeting_date), max_tokens=6000)
    data = parse_json(reply)
    data.update({"date": meeting_date, "source": path.name})
    out = CARDS / f"{path.stem}.json"
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    c, u = tracker.upsert(data["cards"], data["meeting"], meeting_date, path.name)
    print(f"{path.name}: {len(data['cards'])} cards -> {c} created, {u} updated")
    return out


def pending() -> list[Path]:
    exts = {".txt", ".md", ".vtt", ".srt"}
    return sorted(
        p for p in TRANSCRIPTS.iterdir()
        if p.suffix.lower() in exts and not (CARDS / f"{p.stem}.json").exists()
    )


if __name__ == "__main__":
    targets = [Path(a) for a in sys.argv[1:]] or pending()
    if not targets:
        print("nothing to process")
    for t in targets:
        process(t)
