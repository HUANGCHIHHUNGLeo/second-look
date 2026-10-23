"""Turn what crv saw (keyframes + timestamped transcript) into a step-by-step guide.

A multimodal model reads the transcript lines AND the keyframes, so each step can
say what the finished stage should look like, not only what was said. Every step
keeps pointers back to its evidence: transcript line numbers and a frame number.
The output is validated against the real video (times, frame numbers, line
numbers) before it is stored, so the guide can only cite things that exist.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any

from pydantic import BaseModel, Field

from . import config
from .extract import Watched, clock, frame_at


class IngredientOut(BaseModel):
    item: str
    amount: str | None = None
    evidence: list[int] = Field(default_factory=list)


class StepOut(BaseModel):
    title: str
    say: str
    look_for: str
    start_s: float
    end_s: float
    frame: int
    wait_s: int | None = None
    visual_note: str | None = None
    evidence: list[int] = Field(default_factory=list)


class GuideOut(BaseModel):
    title: str
    summary: str
    ingredients: list[IngredientOut]
    equipment: list[str]
    steps: list[StepOut]


PROMPT = """You are turning a cooking video into a hands-free, step-by-step guide for a voice assistant with a screen.
You get (1) the video's transcript as numbered lines with timestamps and (2) the video's keyframes, each labelled with its frame number and timestamp.

Rules:
- Write 6 to 14 steps in the order they happen in the video. One step = one thing the cook does before moving on.
- `say`: what the assistant reads aloud. Imperative, under 35 words, plain spoken English. Include the quantities, sizes and durations the creator gives for this step.
- `look_for`: what the food should look like when this step is done, under 25 words. Describe only what is actually visible in the frame you choose.
- `frame`: the frame number that best shows the result or the key technique of this step. It must fall inside the step's time range.
- `start_s` / `end_s`: where this step happens in the video, in seconds.
- `wait_s`: seconds of timed or hands-off work the creator states for this step (e.g. "knead about five minutes" = 300, "rest half an hour" = 1800). null if no duration is stated.
- `visual_note`: one useful detail you can SEE in the frames for this step that the narration does not say (hand position, texture, size, colour). null if there is none. Never guess.
- `evidence`: the transcript line numbers that support the quantities, durations and claims in `say`.
- The transcript is automatic speech recognition and contains mis-heard words (e.g. "need" for "knead"). Use the frames and cooking knowledge to write the correct words, but keep `evidence` pointing at the original lines.
- `ingredients`: every ingredient with the amount the creator states (null if none is stated) and its evidence lines. `equipment`: tools that appear or are named.
- Never invent a quantity, time or temperature that is neither said nor visible.
"""


def _thin(frames: list, limit: int) -> list:
    if len(frames) <= limit:
        return frames
    step = len(frames) / limit
    return [frames[int(i * step)] for i in range(limit)]


def _jpeg_bytes(path, width: int = 448) -> bytes:
    from PIL import Image

    im = Image.open(path).convert("RGB")
    if im.width > width:
        im = im.resize((width, int(im.height * width / im.width)))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def compile_guide(watched: Watched) -> dict[str, Any]:
    if not config.GEMINI_API_KEY:
        raise RuntimeError("Set GEMINI_API_KEY to build guides.")
    if not watched.segments:
        raise RuntimeError("crv found no speech in this video, so there is nothing to build a guide from.")
    from google import genai
    from google.genai import types

    lines = "\n".join(
        f"[{s.index}] {clock(s.start)}-{clock(s.end)} {s.text}" for s in watched.segments
    )
    parts: list[Any] = [PROMPT, f"Video length: {clock(watched.duration_s)}\n\nTRANSCRIPT:\n{lines}\n\nKEYFRAMES:"]
    for f in _thin(watched.frames, config.MAX_GUIDE_FRAMES):
        parts.append(f"Frame {f.index} at {clock(f.t)} ({f.t:.1f}s)")
        parts.append(types.Part.from_bytes(data=_jpeg_bytes(f.path), mime_type="image/jpeg"))

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(response_mime_type="application/json", response_schema=GuideOut, temperature=0.2)
    last: Exception | None = None
    # Busy models answer 503/429; try again, then fall back to the next model in the list.
    for model in config.GUIDE_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=parts, config=cfg)
                out = GuideOut.model_validate_json(resp.text)
                return validate(out, watched, model=getattr(resp, "model_version", None) or model)
            except Exception as e:  # noqa: BLE001 - surfaced below if every model fails
                last = e
                if not any(code in str(e) for code in ("503", "429", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
                    break
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"The guide model is unavailable right now: {last}")


def validate(out: GuideOut, watched: Watched, model: str | None = None) -> dict[str, Any]:
    """Pin the model's answer to the real video: valid frame numbers, times and line numbers only."""
    n_lines = len(watched.segments)
    by_index = {f.index: f for f in watched.frames}
    dur = watched.duration_s or max(f.t for f in watched.frames)
    steps = []
    for s in sorted(out.steps, key=lambda s: s.start_s):
        start = min(max(0.0, s.start_s), dur)
        end = min(max(start, s.end_s), dur)
        fr = by_index.get(s.frame)
        if fr is None or not (start - 3 <= fr.t <= end + 3):
            # keep the frame inside the step: nearest keyframe to the end of the step
            fr = frame_at(watched, max(start, end - 2))
        ev = sorted({i for i in s.evidence if 0 <= i < n_lines})
        steps.append(
            {
                "title": s.title.strip(),
                "say": s.say.strip(),
                "look_for": s.look_for.strip(),
                "start_s": round(start, 1),
                "end_s": round(end, 1),
                "frame": fr.index,
                "frame_t": round(fr.t, 1),
                "wait_s": s.wait_s if (s.wait_s and s.wait_s > 0) else None,
                "visual_note": (s.visual_note or "").strip() or None,
                "evidence": ev,
            }
        )
    for i, st in enumerate(steps, start=1):
        st["step_number"] = i
    ingredients = [
        {"item": g.item.strip(), "amount": (g.amount or None), "evidence": sorted({i for i in g.evidence if 0 <= i < n_lines})}
        for g in out.ingredients
    ]
    return {
        "title": out.title.strip(),
        "summary": out.summary.strip(),
        "ingredients": ingredients,
        "equipment": [e.strip() for e in out.equipment if e.strip()],
        "steps": steps,
        "video_duration_s": round(dur, 1),
        "frames": len(watched.frames),
        "transcript_lines": n_lines,
        "model": model,
    }


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
