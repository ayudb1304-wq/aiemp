"""Turn WebVTT / SRT caption files into a readable transcript.

Teams exports one cue per few seconds, each wrapped in <v Speaker Name>...</v>, with a cue id
and a timestamp line above it. The model does not need any of that. This keeps only the words,
prefixes each turn with the speaker, and merges consecutive cues from the same speaker into one
paragraph, so a .vtt reads like the .docx transcripts.

    WEBVTT

    abc/149-0
    00:00:03.422 --> 00:00:07.058
    <v Priya>Runbook is about 70% done.</v>

    abc/149-1
    00:00:07.058 --> 00:00:08.702
    <v Priya>I'll finish it by Friday.</v>

becomes

    Priya: Runbook is about 70% done. I'll finish it by Friday.
"""
import html
import re

STAMP = r"(\d{1,2}):(\d{2})(?::(\d{2}))?[.,](\d{1,3})"
TIMING = re.compile(rf"^{STAMP}\s+-->\s+{STAMP}")
VOICE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]*)>")
TAG = re.compile(r"</?[^>]+>")
PAUSE_SECONDS = 3.0  # a gap this long starts a new paragraph even for the same speaker


def _seconds(h_or_m: str, m_or_s: str, s: str | None, frac: str) -> float:
    parts = [int(h_or_m), int(m_or_s)] + ([int(s)] if s is not None else [])
    total = 0
    for p in parts:
        total = total * 60 + p
    return total + int(frac.ljust(3, "0")) / 1000


def parse_captions(text: str) -> str:
    """Plain 'Speaker: words' transcript from .vtt or .srt content."""
    turns: list[list] = []  # [speaker, words, end_seconds]
    cue: list[str] = []
    in_cue = False
    start = end = 0.0

    def flush() -> None:
        if cue:
            raw = " ".join(cue)
            m = VOICE.search(raw)
            speaker = m.group(1).strip() if m else ""
            words = html.unescape(TAG.sub("", raw))
            words = re.sub(r"\s+", " ", words).strip()
            if words:
                if turns and turns[-1][0] == speaker and start - turns[-1][2] < PAUSE_SECONDS:
                    turns[-1][1] += " " + words
                    turns[-1][2] = end
                else:
                    turns.append([speaker, words, end])
        cue.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s:
            flush()
            in_cue = False
            continue
        t = TIMING.match(s)
        if t:
            flush()
            start, end = _seconds(*t.groups()[:4]), _seconds(*t.groups()[4:])
            in_cue = True
            continue
        if in_cue:
            cue.append(s)
        # anything else (WEBVTT header, NOTE blocks, cue ids, SRT counters) is dropped
    flush()
    return "\n\n".join(f"{sp}: {w}" if sp else w for sp, w, _ in turns)
