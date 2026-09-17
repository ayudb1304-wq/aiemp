"""Search over cards, decisions, threads and briefs. No embeddings, no network.

Used by scripts/ask.py (the CLI), scripts/prep.py and common.build_prompt_context (context
layer 4). Ranking is BM25 over canonical tokens:
  - the name map in company.md folds transcription aliases into the real name ("Lakshmi" ->
    laxmikant), so a question about Laxmikant finds records that spelled him differently
  - the project glossaries fold short expansions into their term ("change request" -> cr)
  - a light stemmer folds plurals and verb endings (selectors -> selector)
  - a query token that is in no record is matched fuzzily against the vocabulary (ratio >= 0.85)
Decisions are returned with their chain: current (still in force) or replaced_by <id>.
"""
import difflib
import json
import math
import re
from collections import Counter

from common import (BRIEFS, CARDS, CORRECTIONS, DECISIONS, THREADS, decision_chains, glossary, name_map,
                    read_jsonl, slug)

STOP = set("""a an the and or but if then else of to in on at by for with from as is are was were be
been being do does did doing have has had having i we you he she it they me us him her them my our
your his its their this that these those what when where who whom which why how did do about last
time discuss discussed discussion talk talked say said tell told decide decided decision agree agreed
any some all no not yes will would can could should shall may might must get got go went come came
there here again also just still very much more most so than too up down out over under into onto
one two three first next new old""".split())
WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]+")
K1, B = 1.5, 0.75


def keywords(question: str) -> list[str]:
    """Nouns and names, cheaply: every word that is not a stop word."""
    out = []
    for w in WORD.findall(question):
        lw = w.lower().strip("'-")
        if len(lw) < 3 or lw in STOP:
            continue
        if lw not in out:
            out.append(lw)
    return out


def _stem(w: str) -> str:
    if len(w) > 5 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def synonyms() -> tuple[dict[str, str], dict[str, str]]:
    """(phrases, words): multi-word phrases and single words to fold into a canonical token.
    Built from the name map and the glossaries; short glossary expansions (up to three words)
    count as synonyms, longer ones are descriptions and are ignored."""
    phrases, words = {}, {}
    for alias, real in name_map().items():
        target = slug(real.split()[0])
        (phrases if " " in alias else words)[alias] = target
    for term, expansion in glossary().items():
        target = slug(term)
        for form in (term, expansion.lower()):
            form = form.strip()
            if not form or form == target or len(form.split()) > 3:
                continue
            (phrases if " " in form else words)[form] = target
    return phrases, words


def tokens(text: str, syn: tuple[dict, dict] | None = None) -> list[str]:
    """Canonical tokens of a text: aliases and glossary phrases folded, stop words out, stemmed."""
    phrases, words = syn if syn is not None else synonyms()
    targets = set(phrases.values()) | set(words.values())
    low = text.lower()
    for phrase, target in sorted(phrases.items(), key=lambda kv: -len(kv[0])):
        low = low.replace(phrase, f" {target} ")
    out = []
    for w in WORD.findall(low):
        w = w.strip("'-")
        w = words.get(w, w)
        if w in targets:          # a canonical token is kept as is, however short ("cr")
            out.append(w)
        elif len(w) >= 3 and w not in STOP:
            out.append(_stem(w))
    return out


def _records() -> list[dict]:
    """Every searchable record as {kind, id, date, project, source, text, record?}."""
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
                                 + (f" ({c['update_note']})" if c.get("update_note") else ""), "record": c})
    for d in decision_chains(read_jsonl(DECISIONS)):
        state = "current" if d["current"] else f"replaced by {d['replaced_by']}"
        recs.append({"kind": "decision", "id": d["id"], "date": d.get("date", ""),
                     "project": d.get("project", ""), "source": d.get("source", ""),
                     "text": f"{d.get('decision', '')} (by {d.get('by', '')}) [{d.get('evidence', '')}] ({state})"
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


def _expand(query: list[str], vocab: set[str]) -> dict[str, set[str]]:
    """Each query token -> the vocabulary tokens it matches (itself, or fuzzy neighbours when it
    is in no record at all)."""
    out = {}
    for q in dict.fromkeys(query):
        if q in vocab:
            out[q] = {q}
            continue
        near = {v for v in vocab if abs(len(v) - len(q)) <= 2
                and difflib.SequenceMatcher(None, q, v).ratio() >= 0.85}
        if near:
            out[q] = near
    return out


def rank(query_tokens: list[str], docs: list[list[str]]) -> list[float]:
    """BM25 score of every doc for the query. Fuzzy-expanded query tokens share one weight."""
    n = len(docs)
    if not n or not query_tokens:
        return [0.0] * n
    avg = sum(len(d) for d in docs) / n
    tfs = [Counter(d) for d in docs]
    df = Counter()
    for tf in tfs:
        df.update(tf.keys())
    vocab = set(df)
    groups = _expand(query_tokens, vocab)
    scores = [0.0] * n
    for q, terms in groups.items():
        idf = math.log(1 + (n - sum(df[t] for t in terms) + 0.5) / (sum(df[t] for t in terms) + 0.5))
        for i, tf in enumerate(tfs):
            f = sum(tf[t] for t in terms)
            if f:
                scores[i] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * len(docs[i]) / avg))
    return scores


def search(question: str, project: str | None = None, limit: int = 40,
           kinds: tuple[str, ...] | None = None, kws: list[str] | None = None) -> list[dict]:
    """Hits sorted by date (oldest first). Each hit has kind, id, date, project, source, text, score.
    `kws` may be a pre-tokenised query (used for whole documents)."""
    syn = synonyms()
    query = tokens(" ".join(kws), syn) if kws is not None else tokens(question, syn)
    if not query:
        return []
    recs = [r for r in _records()
            if not (kinds and r["kind"] not in kinds)
            and not (project and r["project"] and slug(r["project"]) != slug(project))]
    scores = rank(query, [tokens(r["text"], syn) for r in recs])
    hits = [{**r, "score": round(s, 3)} for r, s in zip(recs, scores) if s > 0]
    hits.sort(key=lambda h: (-h["score"], h["date"]))
    hits = hits[:limit]
    hits.sort(key=lambda h: h["date"])
    return hits


def history_records(project: str | None, doc_text: str, limit: int = 40) -> tuple[list[dict], list[dict]]:
    """(records, corrections) for context layer 4: decisions and threads of the project that the
    document is about, best matches first, then the project's other active threads so the
    extraction can match against them; plus up to 20 recent corrections for the project."""
    hits = search("", project=project, limit=limit, kinds=("decision", "thread"), kws=[doc_text])
    hits.sort(key=lambda h: -h["score"])
    seen = {h["id"] for h in hits}
    records = [h["record"] for h in hits]
    # A superseded decision only travels with the one that replaced it, never alone.
    chains = {d["id"]: d for d in decision_chains(read_jsonl(DECISIONS))}
    for h in hits:
        if h["kind"] == "decision" and h["record"].get("replaced_by") and h["record"]["replaced_by"] not in seen:
            records.append(chains[h["record"]["replaced_by"]])
            seen.add(h["record"]["replaced_by"])
    for t in read_jsonl(THREADS):
        if t.get("promoted_to") or t["id"] in seen:
            continue
        if project and slug(t.get("project", "")) != slug(project):
            continue
        records.append(t)
    corrections = [c for c in read_jsonl(CORRECTIONS)
                   if not project or slug(c.get("project", "")) == slug(project)][-20:]
    return records, corrections


def history_block(project: str | None, doc_text: str, limit: int = 40) -> tuple[str, int]:
    """(prompt text, number of matched records). Decisions carry current / replaced_by."""
    records, corrections = history_records(project, doc_text, limit)
    parts = []
    if records:
        parts.append("<history>\n" + "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
                     + "\n</history>")
    if corrections:
        parts.append("<corrections>\n" + "\n".join(json.dumps(c, ensure_ascii=False) for c in corrections)
                     + "\n</corrections>")
    return "\n\n".join(parts), len(records)


def format_hits(hits: list[dict]) -> str:
    if not hits:
        return "_No matches._"
    return "\n".join(f"- {h['date'] or '????-??-??'} [{h['kind']} {h['id']}] {h['text']}"
                     f" ({h['source']})" for h in hits)
