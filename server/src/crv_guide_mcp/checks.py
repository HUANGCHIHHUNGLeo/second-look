"""Check a written guide against what the video actually says.

validate() in guide.py makes sure every reference points at something real (a frame
that exists, a line number that exists). This module checks the content, lexically:
every number in a step's instruction (amounts, durations, temperatures) has to appear in
the narration the step cites or the lines spoken during it, with the same unit, and for
amounts of something ("two cups of sugar") next to the same ingredient word. Numbers that
don't are listed in the step's `unverified` field and shown on screen; a timer duration
that isn't in the narration is dropped instead of offered.

It is a check of numbers, units and nearby words, not of meaning: it catches invented or
swapped amounts, it does not prove an instruction is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .evidence import STOP, _stem
from .extract import Watched

WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}
FRACTIONS = {"half": 0.5, "quarter": 0.25, "quarters": 0.25, "third": 1 / 3, "thirds": 1 / 3}
SECONDS = {"second": 1, "seconds": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60, "hour": 3600, "hours": 3600}
UNIT_NAME = {
    "cup": "cup", "cups": "cup", "teaspoon": "tsp", "teaspoons": "tsp", "tsp": "tsp", "tablespoon": "tbsp",
    "tablespoons": "tbsp", "tbsp": "tbsp", "degree": "deg", "degrees": "deg", "gram": "g", "grams": "g", "g": "g",
    "kg": "kg", "ounce": "oz", "ounces": "oz", "oz": "oz", "pound": "lb", "pounds": "lb", "lb": "lb", "lbs": "lb",
    "inch": "in", "inches": "in", "cm": "cm", "ml": "ml", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "piece": "piece", "pieces": "piece", "clove": "clove", "cloves": "clove", "egg": "egg", "eggs": "egg",
    "pinch": "pinch", "pinches": "pinch", "handful": "handful", "handfuls": "handful",
    **{k: "time" for k in SECONDS},
}
UNITS = "|".join(sorted(map(re.escape, UNIT_NAME), key=len, reverse=True))
NUM = r"\d+(?:\.\d+)?(?:/\d+)?|" + "|".join(sorted(map(re.escape, [*WORDS, *FRACTIONS]), key=len, reverse=True))
# "three quarters of a cup", "half an hour", "seven to eight minutes", "1/2 cup", "30 minutes", "350 degrees"
MENTION = re.compile(
    rf"\b(?P<num>(?:{NUM})(?:[\s-]+(?:and\s+a\s+)?(?:{NUM}))?)(?:\s+(?:to|or)\s+(?P<num2>{NUM}))?"
    rf"(?:\s+(?:an?|of\s+an?|of\s+a)?\s*(?P<unit>{UNITS}))?\b",
    re.I,
)
# words after an amount that are not the thing being measured
NOT_NOUNS = STOP | {"each", "more", "less", "extra", "equal", "small", "large", "big", "until", "using", "into", "onto", "per"}


@dataclass
class Amount:
    text: str
    numbers: set[float]
    unit: str = ""                                    # normalised: "cup", "tsp", "time", ... or "" when no unit
    seconds: set[float] = field(default_factory=set)  # for durations
    what: set[str] = field(default_factory=set)       # stemmed words naming what is measured ("of cold water")


def _value(num: str) -> float | None:
    parts = re.split(r"[\s-]+", num.lower().replace("and a ", ""))
    total, last = 0.0, None
    for p in parts:
        if re.fullmatch(r"\d+/\d+", p):
            a, b = p.split("/")
            v = int(a) / int(b) if int(b) else None
        elif re.fullmatch(r"\d+(?:\.\d+)?", p):
            v = float(p)
        elif p in WORDS:
            v = float(WORDS[p])
        elif p in FRACTIONS:
            if p.endswith("s") and last:          # "three quarters" = 3 x 1/4
                total -= last
                v = last * FRACTIONS[p]
            else:                                  # "half", "one and a half"
                v = FRACTIONS[p]
        else:
            continue
        if v is None:
            return None
        total += v
        last = v
    return total if parts else None


def mentions(text: str) -> list[Amount]:
    """Amounts in a sentence, each with what it can match."""
    out = []
    for m in MENTION.finditer(text):
        nums = [n for n in (m.group("num"), m.group("num2")) if n]
        raw_unit = (m.group("unit") or "").lower()
        unit = UNIT_NAME.get(raw_unit, "")
        a = Amount(text=m.group(0).strip(), numbers=set(), unit=unit)
        for n in nums:
            v = _value(n)
            if v is None:
                continue
            a.numbers.add(round(v, 3))
            if raw_unit in SECONDS:
                a.seconds.add(round(v * SECONDS[raw_unit], 3))
        if not a.numbers:
            continue
        if unit != "time":
            words = [w for w in re.findall(r"[a-z]+", text[m.end():].lower())[:4] if w not in NOT_NOUNS and len(w) > 2]
            a.what = {_stem(w) for w in words[:2]}  # the first words after the amount: "of cold water"
        out.append(a)
    return out


def _line_words(watched: Watched, i: int) -> set[str]:
    out: set[str] = set()
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(watched.segments):
            out |= {_stem(w) for w in re.findall(r"[a-z]+", watched.segments[j].text.lower())}
    return out


def _supported(a: Amount, said: list[tuple[int, Amount]], watched: Watched) -> bool:
    for i, e in said:
        same_time = bool(a.seconds and a.seconds & e.seconds)
        if not (a.numbers & e.numbers or same_time):
            continue
        if a.unit and e.unit and a.unit != e.unit and not same_time:
            continue  # "two cups" is not "two teaspoons"
        if a.what and not (a.what & _line_words(watched, i)):
            continue  # "two cups of sugar" is not "two cups of flour"
        return True
    return False


def _said(watched: Watched, line_ids: list[int]) -> list[tuple[int, Amount]]:
    return [(i, e) for i in line_ids if 0 <= i < len(watched.segments) for e in mentions(watched.segments[i].text)]


def _step_line_ids(watched: Watched, step: dict[str, Any]) -> list[int]:
    cited = [i for i in step.get("evidence", []) if 0 <= i < len(watched.segments)]
    spoken = [s.index for s in watched.segments if s.end >= step["start_s"] - 5 and s.start <= step["end_s"] + 5]
    return sorted(set(cited) | set(spoken))


def check_guide(data: dict[str, Any], watched: Watched) -> dict[str, Any]:
    """Annotate a guide in place: `unverified` amounts per step and ingredient; drop timers the narration doesn't support."""
    checked = flagged = 0
    for st in data.get("steps", []):
        said = _said(watched, _step_line_ids(watched, st))
        missing = []
        for a in mentions(st["say"]):
            checked += 1
            if not _supported(a, said, watched):
                missing.append(a.text)
        st["unverified"] = missing
        flagged += len(missing)
        st.pop("timer_dropped_s", None)
        if st.get("wait_s"):
            checked += 1
            if not any(round(float(st["wait_s"]), 3) in e.seconds for _, e in said):
                st["timer_dropped_s"] = st["wait_s"]  # never offer a timer for a duration nobody said
                st["wait_s"] = None
                flagged += 1
    everywhere = _said(watched, list(range(len(watched.segments))))
    for ing in data.get("ingredients", []):
        amounts = mentions(ing.get("amount") or "")
        item = {_stem(w) for w in re.findall(r"[a-z]+", (ing.get("item") or "").lower()) if w not in NOT_NOUNS and len(w) > 2}
        for a in amounts:
            a.what = item
        ing["unverified"] = bool(amounts) and not all(_supported(a, everywhere, watched) for a in amounts)
        checked += len(amounts)
        flagged += int(ing["unverified"])
    data["checks"] = {"numbers_checked": checked, "unverified": flagged}
    return data
