"""Find the parts of a watched video that answer a question.

Plain BM25 over two kinds of passages: windows of transcript lines (what was
said, with timestamps) and the guide's steps (which also carry what is visible
in each step's keyframe). Deterministic and offline; the assistant's own model
reads the returned passages and frames and phrases the answer.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .extract import Watched

STOP = set(
    """a an the and or but if then so to of in on at for with from by is are was were be been being it its this that
    these those i you he she we they me my your our their do does did doing have has had what which who whom how
    when where why can could should would will just about into out up down over again very too as not no yes than
    there here all any each some such only own same few more most other let lets get got going gonna want wanna
    okay ok kind like really little bit thing things now well""".split()
)

# A few cooking words automatic transcripts commonly mis-hear.
ALIASES = {"orecchiette": ["orcetti", "orchetti"], "silpat": ["siltat"]}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    out: list[str] = []
    for w in words:
        if w in STOP:
            continue
        out.append(_stem(w))
        for alias in ALIASES.get(w, []):
            out.append(_stem(alias))
    return out


@dataclass
class Passage:
    kind: str  # "said" (transcript window) or "step"
    start: float
    end: float
    text: str
    step_number: int | None = None
    lines: list[int] = field(default_factory=list)
    toks: list[str] = field(default_factory=list)


def build_passages(watched: Watched, guide: dict) -> list[Passage]:
    segs = watched.segments
    passages: list[Passage] = []
    for i in range(0, len(segs)):
        window = segs[i : i + 3]
        text = " ".join(s.text for s in window)
        passages.append(Passage("said", window[0].start, window[-1].end, text, lines=[s.index for s in window]))
    for st in guide.get("steps", []):
        text = " ".join(filter(None, [st["title"], st["say"], st["look_for"], st.get("visual_note") or ""]))
        passages.append(
            Passage("step", st["start_s"], st["end_s"], text, step_number=st["step_number"], lines=st.get("evidence", []))
        )
    for p in passages:
        p.toks = tokens(p.text)
    return passages


def search(passages: list[Passage], question: str, k: int = 4) -> list[tuple[float, Passage]]:
    q = tokens(question)
    if not q or not passages:
        return []
    n = len(passages)
    avgdl = sum(len(p.toks) for p in passages) / n or 1.0
    df: Counter[str] = Counter()
    for p in passages:
        for t in set(p.toks):
            df[t] += 1
    k1, b = 1.4, 0.75
    scored: list[tuple[float, Passage]] = []
    for p in passages:
        tf = Counter(p.toks)
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(p.toks) / avgdl))
        if s > 0:
            # steps were written from the frames with mis-heard words corrected, so they rank slightly higher
            scored.append((s * (1.25 if p.kind == "step" else 1.0), p))
    scored.sort(key=lambda x: -x[0])
    # drop near-duplicate transcript windows that overlap a better one
    picked: list[tuple[float, Passage]] = []
    for s, p in scored:
        if any(p.kind == "said" and q.kind == "said" and abs(p.start - q.start) < 6 for _, q in picked):
            continue
        picked.append((s, p))
        if len(picked) >= k:
            break
    return picked
