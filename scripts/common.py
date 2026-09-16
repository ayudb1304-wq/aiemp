"""Shared helpers for the AI employee scripts."""
import json
import os
import re
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTEXT = ROOT / "context" / "CONTEXT.md"
TRANSCRIPTS = ROOT / "transcripts"
CARDS = ROOT / "cards"
TRACKER = ROOT / "tracker" / "actions.xlsx"
BRIEFS = ROOT / "briefs"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

COLUMNS = [
    "id", "created", "meeting", "team", "owner", "task", "due",
    "priority", "status", "blocked_by", "evidence", "source", "updated", "notes",
]
OPEN_STATUSES = {"open", "in_progress", "blocked"}


def today() -> str:
    return date.today().isoformat()


def load_context() -> str:
    return CONTEXT.read_text(encoding="utf-8")


def ask(system: str, user: str, max_tokens: int = 4000) -> str:
    """Single Claude call, returns the text of the reply."""
    import anthropic  # imported lazily so deterministic scripts don't need a key

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


def parse_json(text: str):
    """Tolerate ```json fences and leading prose around a JSON object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model reply:\n{text[:400]}")
    return json.loads(text[start:end + 1])


def date_from_filename(path: Path) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else today()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "misc").lower()).strip("-")


def parse_date(s):
    if not s:
        return None
    if isinstance(s, (date, datetime)):
        return s if isinstance(s, date) else s.date()
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None
