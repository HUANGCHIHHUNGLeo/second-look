"""Check a written guide against what the video actually says.

validate() in guide.py makes sure every reference points at something real (a frame
that exists, a line number that exists). This module checks the content: every number
in a step's instruction (amounts, durations, temperatures) has to appear in the
narration that step cites or in the lines spoken during the step. Numbers that don't
are listed in the step's `unverified` field and shown on screen; a timer duration that
isn't in the narration is dropped instead of offered.
"""

from __future__ import annotations

import re
from typing import Any

from .extract import Watched

WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100,
}
FRACTIONS = {"half": 0.5, "quarter": 0.25, "quarters": 0.25, "third": 1 / 3, "thirds": 1 / 3}
SECONDS = {"second": 1, "seconds": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60, "hour": 3600, "hours": 3600}
UNITS = (
    r"cups?|teaspoons?|tablespoons?|tsp|tbsp|minutes?|mins?|hours?|seconds?|degrees?|grams?|g|kg|ounces?|oz|"
    r"pounds?|lbs?|inch(?:es)?|cm|ml|liters?|litres?|pieces?|cloves?|eggs?|pinch(?:es)?|handfuls?"
)
NUM = r"\d+(?:\.\d+)?(?:/\d+)?|" + "|".join(sorted(map(re.escape, [*WORDS, *FRACTIONS]), key=len, reverse=True))
# "three quarters of a cup", "half an hour", "seven to eight minutes", "1/2 cup", "30 minutes", "350 degrees"
MENTION = re.compile(
    rf"\b(?P<num>(?:{NUM})(?:[\s-]+(?:and\s+a\s+)?(?:{NUM}))?)(?:\s+(?:to|or)\s+(?P<num2>{NUM}))?"
    rf"(?:\s+(?:an?|of\s+an?|of\s+a)?\s*(?P<unit>{UNITS}))?\b",
    re.I,
)


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
            # "three quarters" = 3 x 1/4, "one and a half" = 1 + 1/2
            v = (last or 1) * FRACTIONS[p] if p.endswith("s") and last else FRACTIONS[p]
            if p.endswith("s") and last:
                total -= last
        else:
            continue
        if v is None:
            return None
        total += v
        last = v
    return total if parts else None


def mentions(text: str) -> list[tuple[str, set[tuple[str, float]]]]:
    """Numbers in a sentence, each with the values it can match: the number itself and,
    for durations, the length in seconds ("half an hour" matches "30 minutes")."""
    out = []
    for m in MENTION.finditer(text):
        nums = [n for n in (m.group("num"), m.group("num2")) if n]
        unit = (m.group("unit") or "").lower()
        values: set[tuple[str, float]] = set()
        for n in nums:
            v = _value(n)
            if v is None:
                continue
            values.add(("n", round(v, 3)))
            if unit in SECONDS:
                values.add(("s", round(v * SECONDS[unit], 3)))
        if values:
            out.append((m.group(0).strip(), values))
    return out


def _known(texts: list[str]) -> set[tuple[str, float]]:
    vals: set[tuple[str, float]] = set()
    for t in texts:
        for _, v in mentions(t):
            vals |= v
    return vals


def _step_lines(watched: Watched, step: dict[str, Any]) -> list[str]:
    cited = [watched.segments[i].text for i in step.get("evidence", []) if 0 <= i < len(watched.segments)]
    spoken = [s.text for s in watched.segments if s.end >= step["start_s"] - 5 and s.start <= step["end_s"] + 5]
    return cited + spoken


def check_guide(data: dict[str, Any], watched: Watched) -> dict[str, Any]:
    """Annotate a guide in place: `unverified` numbers per step and ingredient; drop timers the narration doesn't support."""
    checked = flagged = 0
    for st in data.get("steps", []):
        known = _known(_step_lines(watched, st))
        missing = []
        for text, values in mentions(st["say"]):
            checked += 1
            if not values & known:
                missing.append(text)
        if st.get("wait_s"):
            checked += 1
            if ("s", round(float(st["wait_s"]), 3)) not in known:
                missing.append(f"timer {int(st['wait_s']) // 60} min")
                st["wait_s"] = None
        st["unverified"] = missing
        flagged += len(missing)
    everything = _known([s.text for s in watched.segments])
    for ing in data.get("ingredients", []):
        amount = ing.get("amount") or ""
        vals = [v for _, v in mentions(amount)]
        ing["unverified"] = bool(vals) and not any(v & everything for v in vals)
        checked += len(vals)
        flagged += int(ing["unverified"])
    data["checks"] = {"numbers_checked": checked, "unverified": flagged}
    return data
