"""Watch a video with crv (claude-real-video) and load what it saw.

crv turns a URL or a local file into scene-change keyframes, per-frame source
timestamps and a timestamped transcript. This module runs it and reads the
results back as plain Python data.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Frame:
    index: int  # 1-based, as in crv's frame_NNN.jpg
    path: Path
    t: float  # seconds in the source video


@dataclass
class Segment:
    index: int  # 0-based
    start: float
    end: float
    text: str


@dataclass
class Watched:
    folder: Path
    frames: list[Frame]
    segments: list[Segment]
    duration_s: float
    video_path: Path | None


def source_key(source: str) -> str:
    """Stable key for a source: a file hash for local files, the URL otherwise."""
    p = Path(source).expanduser()
    if p.exists():
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return "file:" + h.hexdigest()[:24]
    return "url:" + source.strip()


def folder_for(key: str) -> Path:
    return config.VIDEOS_DIR / hashlib.sha256(key.encode()).hexdigest()[:16]


def _crv_cmd() -> list[str]:
    exe = shutil.which("crv", path=str(Path(sys.executable).parent)) or shutil.which("crv")
    return [exe] if exe else [sys.executable, "-m", "claude_real_video"]


def run_crv(source: str, out: Path) -> None:
    """Run crv on the source. Raises RuntimeError with crv's own message on failure."""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*_crv_cmd(), source, "-o", str(out), "--overwrite"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not (out / "frames.json").exists():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError("crv could not watch this video: " + " | ".join(tail))


def load(folder: Path) -> Watched:
    frames_meta = json.loads((folder / "frames.json").read_text())["frames"]
    frames = []
    for i, f in enumerate(frames_meta, start=1):
        path = folder / "frames" / f["file"]
        if path.exists():
            t = f.get("timestamp_sec", f.get("pts", 0.0))  # free crv: timestamp_sec, crv Pro: pts
            frames.append(Frame(index=i, path=path, t=float(t)))
    segments: list[Segment] = []
    tj = folder / "transcript.json"
    if tj.exists():
        raw = json.loads(tj.read_text())
        segs = raw["segments"] if isinstance(raw, dict) else raw
        for i, s in enumerate(segs):
            text = str(s.get("text", "")).strip()
            if text:
                segments.append(Segment(index=len(segments), start=float(s["start"]), end=float(s["end"]), text=text))
    video = next((p for p in (folder / "source.mp4", *folder.glob("source.*")) if p.exists()), None)
    duration = max([f.t for f in frames] + [s.end for s in segments] + [0.0])
    if video:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                capture_output=True, text=True, timeout=30,
            )
            duration = float(out.stdout.strip() or duration)
        except (ValueError, subprocess.SubprocessError):
            pass
    return Watched(folder=folder, frames=frames, segments=segments, duration_s=duration, video_path=video)


def frame_at(watched: Watched, t: float) -> Frame:
    """The keyframe closest to second t."""
    return min(watched.frames, key=lambda f: abs(f.t - t))


def exact_frame(watched: Watched, t: float) -> Path:
    """Grab the exact frame at second t from the source video (falls back to the nearest keyframe)."""
    if watched.video_path is None:
        return frame_at(watched, t).path
    cache = watched.folder / "moments"
    cache.mkdir(exist_ok=True)
    out = cache / f"t{t:08.2f}.jpg"
    if not out.exists():
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(watched.video_path),
             "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(out)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not out.exists():
            return frame_at(watched, t).path
    return out


def clock(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"
