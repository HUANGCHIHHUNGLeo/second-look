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
    okay ok kind like really little bit thing things now well much many use used using did make""".split()
)

# A few cooking words automatic transcripts commonly mis-hear.
ALIASES = {"orecchiette": ["orcetti", "orchetti"], "silpat": ["siltat"]}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            w = w[: -len(suf)]
            if suf in ("ing", "ed") and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
                w = w[:-1]  # dragging -> drag, stopped -> stop
            return w
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


QUANTITY = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|half|quarters?|cups?|teaspoons?|tablespoons?|"
    r"minutes?|hours?|seconds?|degrees?|grams?|ounces?|inch(es)?|pinch|handful)\b",
    re.I,
)
ASKS_QUANTITY = re.compile(r"\bhow (much|many|long|big|thick|thin|hot)\b|temperature|amount|quantity|how much time", re.I)
# words that say what kind of answer is wanted, not what it is about ("how long does the DOUGH rest")
GENERIC = {_stem(w) for w in """add adds put pour need needs take get cook mix look see show tell say know want start time
    video step right good long big thick thin hot temperature amount quantity size""".split()}


def subject(question: str) -> list[str]:
    """What a question is about: "How much sugar does she add?" -> ["sugar"] (stemmed, like the passages)."""
    return [t for t in tokens(question) if t not in GENERIC]


def unmentioned(question: str, passages: list[Passage]) -> list[str]:
    """Words of the question's subject that appear nowhere in the video, as the person said them."""
    seen = set().union(*(p.toks for p in passages)) if passages else set()
    out = []
    for w in re.findall(r"[a-z0-9]+", question.lower()):
        t = _stem(w)
        if w not in STOP and t not in GENERIC and t not in seen and w not in out:
            out.append(w)
    return out


def best_line(segments: list, line_ids: list[int], question: str, ignore: frozenset[str] = frozenset()):
    """The one transcript line among `line_ids` that answers the question, so an answer can say
    "she says it at 1:24" instead of quoting a whole step's time range. None if no line matches.
    `ignore`: words that name the whole video (its title), which point at no particular moment."""
    q = set(tokens(question)) - ignore
    wants_amount = bool(ASKS_QUANTITY.search(question))
    n = len(segments) or 1
    df: Counter[str] = Counter(t for seg in segments for t in set(tokens(seg.text)))
    def overlap(i: int) -> float:
        if not 0 <= i < len(segments):
            return 0.0
        return sum(math.log(1 + n / df[t]) for t in q & set(tokens(segments[i].text)))

    best, best_s = None, 0.0
    for i in line_ids:
        if not 0 <= i < len(segments):
            continue
        s = overlap(i)
        if wants_amount and (s or overlap(i - 1) or overlap(i + 1)):
            # "how much water": the amount is often in the line right after the one naming the thing
            s += 3.0 * min(2, len(QUANTITY.findall(segments[i].text)))
        if s > best_s:
            best, best_s = segments[i], s
    return best


def search(passages: list[Passage], question: str, k: int = 4) -> list[tuple[float, Passage]]:
    q = tokens(question)
    wants_amount = bool(ASKS_QUANTITY.search(question))
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
            boost = 1.25 if p.kind == "step" else 1.0
            if wants_amount:
                # "how much / how long" questions: prefer passages that actually state an amount
                boost *= 1 + 0.6 * min(3, len(QUANTITY.findall(p.text)))
            scored.append((s * boost, p))
    if wants_amount and (about := set(subject(question))):
        # an amount for something the video never mentions is not an answer ("how much sugar?" in a recipe with none)
        scored = [(sc, p) for sc, p in scored if about & set(p.toks)]
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
