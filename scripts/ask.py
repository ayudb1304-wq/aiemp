"""Ask the memory a question.

  python scripts/ask.py "when did we last discuss the currency selector"
  python scripts/ask.py --project lsp "what did we decide about column changes"

1. keywords from the question (stop words removed; Claude is not needed for this)
2. keyword + fuzzy search over cards, decisions, threads and briefs
3. with ANTHROPIC_API_KEY set, Claude summarises the hits in date order with quotes and dates;
   without it, the dated hits are printed as markdown.
"""
import sys

from common import ask, have_key
from search import format_hits, keywords, search

SYSTEM = """You answer questions about a team's history using ONLY the dated records provided.
Write markdown: a short answer first, then the relevant records in date order, each with its date,
a short quote from the record and the id in brackets. If the records do not answer the question,
say so in one line. Never invent dates, quotes or ids."""


def main(argv: list[str]) -> int:
    project = None
    if argv[:1] == ["--project"] and len(argv) > 2:
        project, argv = argv[1], argv[2:]
    question = " ".join(argv).strip()
    if not question:
        print(__doc__)
        return 1
    kws = keywords(question)
    hits = search(question, project=project)
    print(f"_keywords: {', '.join(kws) or 'none'}; {len(hits)} hits_\n")
    if hits and have_key():
        print(ask(SYSTEM, f"<question>{question}</question>\n\n<records>\n{format_hits(hits)}\n</records>",
                  max_tokens=1500).strip())
    else:
        print(format_hits(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
