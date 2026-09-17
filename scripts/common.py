"""Shared helpers for the AI employee scripts.

Paths, tracker columns, the Claude call helpers, document readers, the layered context loader
and the small JSONL memory helpers. Set env AIEMP_ROOT to run every script against another tree
(scripts/selftest.py does this with a temp directory).
"""
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(os.environ.get("AIEMP_ROOT") or Path(__file__).resolve().parent.parent)
CONTEXT_DIR = ROOT / "context"
CONTEXT = CONTEXT_DIR / "CONTEXT.md"          # kept as a pointer so old links work
COMPANY = CONTEXT_DIR / "company.md"
PROJECTS_DIR = CONTEXT_DIR / "projects"
PEOPLE_DIR = CONTEXT_DIR / "people"
INBOX = ROOT / "inbox"
CARDS = ROOT / "cards"
TRACKER = ROOT / "tracker" / "actions.xlsx"
BRIEFS = ROOT / "briefs"
MEMORY = ROOT / "memory"
DECISIONS = MEMORY / "decisions.jsonl"
THREADS = MEMORY / "threads.jsonl"
CORRECTIONS = MEMORY / "corrections.jsonl"
DRAFTS = ROOT / "drafts"
LOGS = ROOT / "logs"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
# Input window the model accepts. Documents that do not fit next to the context are chunked.
MODEL_INPUT_TOKENS = int(os.environ.get("CLAUDE_INPUT_TOKENS", "200000"))
# "AI EMP Action Point Tracker" sheet in the AI-Employee Drive folder. Override with env GSHEET_ID.
GSHEET_ID = "14MAhqV3RLDo5BYRiULRjRj8h9II8i0TENHEf4Ch1bg8"
INPUT_EXTS = {".txt", ".md", ".vtt", ".srt", ".docx", ".pdf", ".eml"}
TRANSCRIPT_EXTS = INPUT_EXTS  # old name, still imported by older scripts
KINDS = ["transcript", "notes", "email", "doc", "chat"]
UNASSIGNED = "unassigned"
CHUNK_TOKENS = 20000   # documents larger than this are processed in chunks of this size

# Column order of tracker/actions.xlsx and the Sheet's Actions tab: what you act on first (task,
# owner, priority, status, due), the advisor columns next, provenance (meeting, source, id) last.
# Rows are read back by header name, so reordering here is safe for existing files.
COLUMNS = [
    "task", "owner", "priority", "status", "due", "project", "team", "type", "next_step",
    "unblocker", "effort", "blocked_by", "prerequisites", "notes", "evidence", "basis",
    "meeting", "origin", "updated", "closed", "created", "source", "id",
]
DATE_COLUMNS = ("created", "due", "updated", "closed")
OPEN_STATUSES = {"open", "in_progress", "blocked", "to_verify"}
STATUSES = ["open", "in_progress", "blocked", "to_verify"]   # what extraction may return
TYPES = ["communicate", "decide", "build", "review", "coordinate", "other"]
EFFORTS = ["15m", "1h", "half-day", "day+", "unknown"]
ORIGINS = ["planned", "unplanned"]


def today() -> str:
    return date.today().isoformat()


def now_id() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H%M%S")


def warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def est_tokens(text: str) -> int:
    return len(text) // 4


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "misc").lower()).strip("-")


def parse_date(s):
    if not s:
        return None
    if isinstance(s, (date, datetime)):
        return s if not isinstance(s, datetime) else s.date()
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ----------------------------------------------------------------------------- Claude

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


def have_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def parse_json(text: str):
    """Tolerate ```json fences and leading prose around a JSON object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model reply:\n{text[:400]}")
    return json.loads(text[start:end + 1])


# ----------------------------------------------------------------------------- documents

def read_document(path: Path) -> str:
    """Plain text of any supported inbox file. Never truncates."""
    ext = path.suffix.lower()
    if ext == ".docx":
        import docx  # python-docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    if ext == ".pdf":
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            return "\n\n".join((page.extract_text() or "") for page in pdf.pages)
    if ext == ".eml":
        return read_eml(path)
    if ext in (".vtt", ".srt"):
        from captions import parse_captions

        return parse_captions(path.read_text(encoding="utf-8-sig", errors="replace"))
    return path.read_text(encoding="utf-8", errors="replace")


read_transcript = read_document  # old name


def read_eml(path: Path) -> str:
    import email
    from email import policy

    msg = email.message_from_bytes(path.read_bytes(), policy=policy.default)
    head = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
    return "\n".join(head) + "\n\n" + text.strip()


def is_note(path: Path) -> bool:
    """Unplanned intake: inbox files named note-*.<ext>."""
    return path.name.lower().startswith("note-")


def date_from_filename(path: Path, text: str = "") -> str:
    """YYYY-MM-DD in the filename (after an optional 'note-' prefix), else a YYYYMMDD stamp in
    the name or first lines (Teams names recordings like '...-20260916_110406-Meeting Recording'),
    else the file's mtime with a warning."""
    name = re.sub(r"^note-", "", path.name, flags=re.I)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
    if m:
        return m.group(1)
    m = re.search(r"(20\d{2})(\d{2})(\d{2})_\d{6}", path.name + "\n" + text[:500])
    if m:
        return "-".join(m.groups())
    try:
        d = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    except OSError:
        d = today()
    warn(f"{path.name}: no YYYY-MM-DD prefix, using file date {d}")
    return d


def chunk_text(text: str, max_tokens: int = 20000) -> list[str]:
    """Split at paragraph boundaries into pieces of at most ~max_tokens. Nothing is dropped."""
    if est_tokens(text) <= max_tokens:
        return [text]
    chunks, cur, cur_len = [], [], 0
    for para in re.split(r"\n\s*\n", text):
        plen = est_tokens(para) + 1
        if cur and cur_len + plen > max_tokens:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
        if plen > max_tokens:  # one huge paragraph: split on lines
            lines, buf, blen = para.splitlines(), [], 0
            for ln in lines:
                if buf and blen + est_tokens(ln) + 1 > max_tokens:
                    chunks.append("\n".join(buf))
                    buf, blen = [], 0
                buf.append(ln)
                blen += est_tokens(ln) + 1
            cur, cur_len = buf, blen
            continue
        cur.append(para)
        cur_len += plen
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


# ----------------------------------------------------------------------------- context folder

def project_slugs() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROJECTS_DIR.glob("*.md"))


def project_title(slug_: str) -> str:
    p = PROJECTS_DIR / f"{slug_}.md"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.startswith("# "):
                return ln[2:].strip()
    return slug_


def project_from_name(name: str) -> str | None:
    """A file or card whose name contains a project slug belongs to that project."""
    low = name.lower()
    for s in project_slugs():
        if s in low:
            return s
    return None


def people_slugs() -> list[str]:
    if not PEOPLE_DIR.exists():
        return []
    return sorted(p.stem for p in PEOPLE_DIR.glob("*.md"))


def people_in(text: str) -> list[str]:
    """People with a context/people file whose name appears in the text."""
    low = text.lower()
    return [p for p in people_slugs() if p.replace("-", " ") in low or p in low]


def my_name() -> str:
    """First name from the 'Name:' line of company.md, e.g. 'Ayush'."""
    if COMPANY.exists():
        m = re.search(r"^- Name:\s*([A-Za-z]+)", COMPANY.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    return "me"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_context(project: str | None = None, people: list[str] | None = None) -> str:
    """company.md + projects/<project>.md + people/<name>.md, in that order. Missing files are
    skipped silently. Falls back to CONTEXT.md if company.md does not exist (old layout)."""
    parts = [_read(COMPANY) or _read(CONTEXT)]
    if project:
        parts.append(_read(PROJECTS_DIR / f"{slug(project)}.md"))
    for name in people or []:
        parts.append(_read(PEOPLE_DIR / f"{slug(name)}.md"))
    return "\n\n".join(p for p in parts if p)


def brief_sections(project: str, n: int = 2) -> str:
    """The '## Project: <name>' sections of the last n briefs, for context layer 5."""
    if not BRIEFS.exists():
        return ""
    title = project_title(project)
    out = []
    files = sorted(BRIEFS.glob("????-??-??.md"), reverse=True)[:n]
    for f in files:
        text = f.read_text(encoding="utf-8")
        m = re.search(rf"^## Project: {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        if m:
            out.append(f"### Brief {f.stem}\n{m.group(1).strip()}")
    return "\n\n".join(out)


def build_prompt_context(project: str | None, people: list[str], doc_text: str,
                         budget_tokens: int = 60000, open_rows: list[dict] | None = None,
                         history: str | None = None) -> tuple[str, dict]:
    """Layered context under a token budget. Returns (prompt_text, log).

    Layers, in load order. When the budget is exceeded the LOWEST layer is dropped first;
    layer 1 and the document are never dropped.
      1 company.md + projects/<project>.md
      2 open items for that project (id, owner, task, due, priority, status, evidence, notes[:200])
      3 people/<name>.md for people mentioned
      4 retrieved history: decisions/threads for the project that match the document, plus the
        project's active threads (for matching) and up to 20 recent corrections
      5 the project's sections of the last 2 briefs
    """
    layers: list[tuple[str, str]] = []
    layers.append(("1:company+project", "<context>\n" + load_context(project) + "\n</context>"))
    if open_rows is None:
        import tracker  # local import: tracker imports common

        open_rows = tracker.open_items(project=project)
    items = [{k: (r.get(k) or "")[:200] if k in ("notes", "evidence") else r.get(k, "")
              for k in ("id", "owner", "task", "due", "priority", "status", "evidence", "notes")}
             for r in open_rows]
    layers.append(("2:open_items", "<open_items>\n" + json.dumps(items, indent=1, ensure_ascii=False)
                   + "\n</open_items>"))
    ppl = "\n\n".join(_read(PEOPLE_DIR / f"{slug(n)}.md") for n in people)
    layers.append(("3:people", f"<people>\n{ppl}\n</people>" if ppl.strip() else ""))
    if history is None:
        import search  # local import

        history = search.history_block(project, doc_text)
    layers.append(("4:history", history or ""))
    if project:
        bs = brief_sections(project)
        layers.append(("5:briefs", f"<recent_briefs>\n{bs}\n</recent_briefs>" if bs else ""))
    else:
        layers.append(("5:briefs", ""))

    # The document counts against the budget, but only one chunk of it: a document larger than
    # CHUNK_TOKENS is sent chunk by chunk (extract.py), each with the same context.
    doc_tokens = min(est_tokens(doc_text), CHUNK_TOKENS)
    log = {"budget_tokens": budget_tokens, "document_tokens": est_tokens(doc_text),
           "document_counted": doc_tokens, "project": project,
           "people": people, "layers": {}, "dropped": [], "warnings": []}
    sizes = {name: est_tokens(text) for name, text in layers}
    for name, text in layers:
        log["layers"][name] = {"tokens": sizes[name], "loaded": bool(text)}
    if sizes["1:company+project"] > 0.4 * budget_tokens:
        msg = f"CONSOLIDATE: projects/{project}.md is too large ({sizes['1:company+project']} tokens)"
        log["warnings"].append(msg)
        warn(msg)
    kept = [name for name, text in layers if text]
    while kept[1:] and sum(sizes[n] for n in kept) + doc_tokens > budget_tokens:
        dropped = kept.pop()  # lowest layer goes first
        log["dropped"].append(dropped)
        log["layers"][dropped]["loaded"] = False
    log["context_tokens"] = sum(sizes[n] for n in kept)
    text = "\n\n".join(t for n, t in layers if n in kept)
    return text, log


def write_run_log(run_id: str, data: dict) -> Path:
    LOGS.mkdir(parents=True, exist_ok=True)
    p = LOGS / f"{run_id}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


# ----------------------------------------------------------------------------- memory files

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
