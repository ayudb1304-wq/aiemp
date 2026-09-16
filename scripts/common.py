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
# "AI EMP Action Point Tracker" sheet in the AI-Employee Drive folder. Override with env GSHEET_ID.
GSHEET_ID = "14MAhqV3RLDo5BYRiULRjRj8h9II8i0TENHEf4Ch1bg8"
TRANSCRIPT_EXTS = {".txt", ".md", ".vtt", ".srt", ".docx"}

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


def ask_json(system: str, user: str, schema: dict, max_tokens: int = 32000) -> dict:
    """Single Claude call constrained to a JSON schema. The API guarantees the reply text is
    valid JSON matching the schema, so unescaped quotes in transcript excerpts cannot break it.
    Streams because long transcripts produce long replies (thinking tokens count too)."""
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(f"Model reply truncated at {max_tokens} tokens; raise max_tokens")
    text = "".join(b.text for b in resp.content if b.type == "text")
    return parse_json(text)


def parse_json(text: str):
    """Tolerate ```json fences and leading prose around a JSON object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model reply:\n{text[:400]}")
    return json.loads(text[start:end + 1])


def read_transcript(path: Path) -> str:
    """Plain text of a transcript. Teams exports (.docx) are read paragraph by paragraph."""
    if path.suffix.lower() == ".docx":
        import docx  # python-docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8")


def date_from_filename(path: Path, text: str = "") -> str:
    """YYYY-MM-DD prefix of the filename, else a YYYYMMDD stamp in the first lines
    (Teams names recordings like '...-20260916_110406-Meeting Recording'), else today."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    if m:
        return m.group(1)
    m = re.search(r"(20\d{2})(\d{2})(\d{2})_\d{6}", path.name + "\n" + text[:500])
    if m:
        return "-".join(m.groups())
    return today()


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
