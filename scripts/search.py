"""Keyword search over cards, decisions, threads and briefs. No embeddings.

Used by scripts/ask.py (the CLI) and by common.build_prompt_context (context layer 4).
Matching: case-insensitive substring, plus difflib ratio >= 0.8 between a keyword and each word
of the text, so "selector" still finds "selectors" and transcription variants.
"""
import difflib
import json
import re
from pathlib import Path

from common import BRIEFS, CARDS, CORRECTIONS, DECISIONS, THREADS, read_jsonl, slug

STOP = set("""a an the and or but if then else of to in on at by for with from as is are was were be
been being do does did doing have has had having i we you he she it they me us him her them my our
your his its their this that these those what when where who whom which why how did do about last
time discuss discussed discussion talk talked say said tell told decide decided decision agree agreed
any some all no not yes will would can could should shall may might must get got go went come came
there here again also just still very much more most so than too up down out over under into onto
one two three first next new old""".split())
WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]+")


def keywords(question: str) -> list[str]:
    """Nouns and names, cheaply: every word that is not a stop word, plus capitalised words."""
    out = []
    for w in WORD.findall(question):
        lw = w.lower().strip("'-")
        if len(lw) < 3 or lw in STOP:
            continue
        if lw not in out:
            out.append(lw)
    return out


def _matches(kw: str, text: str, ratio: float = 0.8) -> bool:
    low = text.lower()
    if kw in low:
        return True
    for w in WORD.findall(low):
        if abs(len(w) - len(kw)) <= 3 and difflib.SequenceMatcher(None, kw, w).ratio() >= ratio:
            return True
    return False


def score(kws: list[str], text: str) -> int:
    return sum(1 for k in kws if _matches(k, text))


def _records() -> list[dict]:
    """Every searchable record as {kind, id, date, project, source, text}."""
    recs = []
    for p in sorted(CARDS.glob("*.json")) if CARDS.exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for i, c in enumerate(data.get("cards", []), 1):
            recs.append({"kind": "card", "id": c.get("matches_existing_id") or f"{p.stem}#{i}",
                         "date": data.get("date", ""), "project": data.get("project", ""),
                         "source": data.get("source", p.name),
                         "text": f"{c.get('owner', '')}: {c.get('task', '')} [{c.get('evidence', '')}]"
                                 + (f" ({c['update_note']})" if c.get("update_note") else "")})
    for d in read_jsonl(DECISIONS):
        recs.append({"kind": "decision", "id": d["id"], "date": d.get("date", ""),
                     "project": d.get("project", ""), "source": d.get("source", ""),
                     "text": f"{d.get('decision', '')} (by {d.get('by', '')}) [{d.get('evidence', '')}]"
                             + (f" supersedes {d['supersedes']}" if d.get("supersedes") else ""),
                     "record": d})
    for t in read_jsonl(THREADS):
        recs.append({"kind": "thread", "id": t["id"], "date": t.get("last_seen") or t.get("date", ""),
                     "project": t.get("project", ""), "source": t.get("source", ""),
                     "text": f"{t.get('topic', '')}: {t.get('note', '')} [{t.get('evidence', '')}]"
                             f" (mentioned {t.get('mentions', 1)}x)", "record": t})
    if BRIEFS.exists():
        for p in sorted(BRIEFS.glob("????-??-??.md")):
            for ln in p.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln.startswith("- ") or ln.startswith("* "):
                    recs.append({"kind": "brief", "id": p.stem, "date": p.stem, "project": "",
                                 "source": p.name, "text": ln[2:]})
    return recs


def search(question: str, project: str | None = None, limit: int = 40,
           kinds: tuple[str, ...] | None = None, kws: list[str] | None = None) -> list[dict]:
    """Hits sorted by date (oldest first). Each hit has kind, id, date, project, source, text, score."""
    kws = kws if kws is not None else keywords(question)
    if not kws:
        return []
    hits = []
    for r in _records():
        if kinds and r["kind"] not in kinds:
            continue
        if project and r["project"] and slug(r["project"]) != slug(project):
            continue
        s = score(kws, r["text"])
        if s:
            hits.append({**r, "score": s})
    hits.sort(key=lambda h: (-h["score"], h["date"]))
    hits = hits[:limit]
    hits.sort(key=lambda h: h["date"])
    return hits


def history_block(project: str | None, doc_text: str, limit: int = 40) -> str:
    """Context layer 4: decisions and threads for the project that match names/topics in the
    document (newest first, max `limit`), plus the project's active threads so the extraction
    can match against them, plus up to 20 recent corrections for the project."""
    kws = keywords(doc_text)[:200]
    hits = search("", project=project, limit=limit, kinds=("decision", "thread"), kws=kws)
    hits.sort(key=lambda h: h["date"], reverse=True)
    seen = {h["id"] for h in hits}
    records = [h["record"] for h in hits]
    for t in read_jsonl(THREADS):
        if t.get("promoted_to") or t["id"] in seen:
            continue
        if project and slug(t.get("project", "")) != slug(project):
            continue
        records.append(t)
    corrections = [c for c in read_jsonl(CORRECTIONS)
                   if not project or slug(c.get("project", "")) == slug(project)][-20:]
    parts = []
    if records:
        parts.append("<history>\n" + "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
                     + "\n</history>")
    if corrections:
        parts.append("<corrections>\n" + "\n".join(json.dumps(c, ensure_ascii=False) for c in corrections)
                     + "\n</corrections>")
    return "\n\n".join(parts)


def format_hits(hits: list[dict]) -> str:
    if not hits:
        return "_No matches._"
    return "\n".join(f"- {h['date'] or '????-??-??'} [{h['kind']} {h['id']}] {h['text']}"
                     f" ({h['source']})" for h in hits)
