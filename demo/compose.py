"""Edits the recorded takes into the demo video: cuts the model's thinking pauses, lays the
voices and the original clip audio on their real timestamps, adds title cards and narration.

    python demo/compose.py  ->  $DEMO_OUT/second-look-demo.mp4
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tts import tts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("DEMO_OUT", "/tmp/sl-demo2"))
WORK = OUT / "work"
WORK.mkdir(parents=True, exist_ok=True)
CARDS = Path(__file__).parent / "cards"
SOURCE = ROOT / "server/fixtures/pasta/source.mp4"
FPS = 30
CAPTION = "Alexa+ experience simulation  ·  requests typed for this recording, voices synthesized  ·  waits for the model are cut"

NARR = {
    "intro": "Cooking from a video is hard with dough on your hands. The video keeps going, a quantity is said once, and you never know if yours looks right.",
    "title": "Second Look lets Alexa plus follow the video with you, because it actually watched it.",
    "restart": "Now close the browser, and restart the server.",
    "fresh": "And a video it has never seen.",
    "newvideo": "Paste a link. crv watches it, and a model writes the steps. Tonight that took {took}, sped up here.",
    "trace": "Under the hood: the screen is an MCP Apps host, and the Strands agent and the screen share one MCP server.",
    "end": "Second Look. Cook along with a video, hands-free. Open source.",
}


def caption_png(name: str, text: str, where: str) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    out = WORK / f"cap-{name}.png"
    im = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except OSError:
        font = ImageFont.load_default()
    w = d.textlength(text, font=font)
    if where == "banner":
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30) if Path("/System/Library/Fonts/Helvetica.ttc").exists() else font
        w = d.textlength(text, font=font)
        x, y = (1920 - w) / 2, 1080 - 150
        d.rounded_rectangle([x - 20, y - 14, x + w + 20, y + 44], radius=12, fill=(8, 12, 18, 215))
        d.text((x, y), text, font=font, fill=(255, 255, 255, 240))
        im.save(out)
        return out
    if where == "intro":
        x, y = 40, 1080 - 64
        d.rounded_rectangle([x - 12, y - 10, x + w + 12, y + 34], radius=8, fill=(0, 0, 0, 120))
        d.text((x, y), text, font=font, fill=(255, 255, 255, 225))
    else:
        x, y = (1920 - w) / 2, 1080 - 40
        d.text((x, y), text, font=font, fill=(85, 96, 109, 255))
    im.save(out)
    return out


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(" ".join(cmd)[:600])
        print(r.stderr[-2500:])
        raise SystemExit(1)


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip())


def mix_audio(items: list[tuple[float, Path, float]], total: float, out: Path) -> Path:
    """items: (start_seconds, audio_file, volume). Returns a wav of length `total`."""
    if not items:
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo", "-t", f"{total:.3f}", str(out)])
        return out
    cmd = ["ffmpeg", "-y"]
    for _, f, _ in items:
        cmd += ["-i", str(f)]
    parts = []
    for i, (start, _, vol) in enumerate(items):
        ms = max(0, int(start * 1000))
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol},adelay={ms}|{ms}[a{i}]")
    parts.append("".join(f"[a{i}]" for i in range(len(items))) + f"amix=inputs={len(items)}:normalize=0:dropout_transition=0,apad,atrim=0:{total:.3f}[out]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    run(cmd)
    return out


def narr(key: str, **fmt) -> tuple[Path, float]:
    a, _ = tts("narrator", NARR[key].format(**fmt))
    return fast_audio(str(a), 1.06)


def card(name: str, narr_key: str | None, min_len: float = 4.0, pad: float = 0.8) -> Path:
    items = []
    length = min_len
    if narr_key:
        a, d = narr(narr_key)
        items.append((0.35, a, 1.0))
        length = max(min_len, d + 0.35 + pad)
    audio = mix_audio(items, length, WORK / f"{name}.wav")
    out = WORK / f"{name}.mp4"
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(CARDS / f"{name}.png"), "-i", str(audio),
         "-t", f"{length:.3f}", "-vf", "scale=1920:1080,format=yuv420p,fade=t=in:st=0:d=0.3", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)])
    return out


def source_clip(start: float, length: float, out: Path, source: Path | None = None) -> Path:
    run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(source or SOURCE), "-t", f"{length:.2f}", "-vn", "-ac", "2", "-ar", "48000", str(out)])
    return out


def new_video_source() -> Path:
    """The video imported live in seg3 (its clip audio must come from it, not from the sample)."""
    data_new = Path(os.environ.get("DEMO_DATA_NEW", os.environ.get("DEMO_DATA", "/tmp/sl-demo-data") + "-new"))
    found = sorted(data_new.glob("videos/*/source.*"))
    if not found:
        raise SystemExit(f"no imported video under {data_new}/videos")
    return found[0]


def intro() -> Path:
    a, d = narr("intro")
    length = d + 0.9
    bed = source_clip(117.0, length, WORK / "intro-bed.wav")
    audio = mix_audio([(0.0, bed, 0.22), (0.4, a, 1.0)], length, WORK / "intro.wav")
    out = WORK / "intro.mp4"
    cap = caption_png("intro", "Cooking with Laura: Homemade Pasta · Northampton Community Television · CC BY 3.0", "intro")
    fc = ("[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fade=t=in:st=0:d=0.4[b];"
          "[b][2:v]overlay=0:0,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-ss", "117", "-i", str(SOURCE), "-i", str(audio), "-loop", "1", "-i", str(cap), "-t", f"{length:.3f}",
         "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-c:a", "aac", "-b:a", "192k", str(out)])
    return out


SPEED = 1.2  # dialogue is played 20% faster; pauses for the model are cut anyway


def fast_audio(path: str, speed: float | None = None) -> tuple[Path, float]:
    speed = speed or SPEED
    src = Path(path)
    out = WORK / f"{src.stem}-x{speed:.2f}.wav"
    if not out.exists():
        run(["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={speed}", "-ar", "48000", "-ac", "2", str(out)])
    return out, dur(out)


def keep_intervals(events: list[dict], seg_len: float, lead: float) -> list[list[float]]:
    iv: list[list[float]] = [[lead, events[0]["at"] if events else seg_len]]
    for e in events:
        at = e.get("at_override", e["at"])
        if e["kind"] == "user":
            iv.append([at - 0.15, at + e["dur"] / SPEED + 0.4])
        elif e["kind"] == "alexa":
            iv.append([at - 0.45, at + e["dur"] / SPEED + 0.3])
        elif e["kind"] == "clip":
            iv.append([at - 0.4, at + e["dur"] + 0.2])
        elif e["kind"] == "tail":
            iv.append([at - 0.3, min(seg_len, at + e.get("dur", 9.5))])
        elif e["kind"] == "handsfree":
            iv.append([at - 0.3, at + 1.0])
        elif e["kind"] == "import_start":
            iv.append([at - 1.2, at + e.get("dur", 4.0)])
        elif e["kind"] == "stage":
            iv.append([at - 0.2, at + 2.6])
        elif e["kind"] == "import_done":
            iv.append([at - 1.0, at + 0.4])
    iv = sorted([[max(0.0, a), min(seg_len, b)] for a, b in iv if b > a])
    merged: list[list[float]] = []
    for a, b in iv:
        if merged and a <= merged[-1][1] + 0.25:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def remap(t: float, iv: list[list[float]]) -> float:
    acc = 0.0
    for a, b in iv:
        if t < a:
            return acc
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def segment(name: str, events: list[dict], lead: float, extra_audio: list[tuple[float, Path, float]] = (),
            banners: list[tuple[float, float, str]] = (), source: Path | None = None) -> Path:
    src = OUT / f"{name}.webm"
    seg_len = dur(src)
    iv = keep_intervals(events, seg_len, lead)
    total = sum(b - a for a, b in iv)
    # video: trim each kept interval, concat, fit into 1920x1080
    parts, labels = [], []
    for i, (a, b) in enumerate(iv):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,fps={FPS}[v{i}]")
        labels.append(f"[v{i}]")
    cap = caption_png("rec", CAPTION, "rec")
    parts.append("".join(labels) + f"concat=n={len(iv)}:v=1:a=0,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0xe9e6e1[base];"
                 "[base][2:v]overlay=0:0" + ("[c0]" if banners else ",format=yuv420p[v]"))
    banner_inputs: list[str] = []
    for k, (a, b, text) in enumerate(banners):
        png = caption_png(f"{name}-banner{k}", text, "banner")
        banner_inputs += ["-loop", "1", "-i", str(png)]
        a2, b2 = remap(a, iv), remap(b, iv)
        last = k == len(banners) - 1
        parts.append(f"[c{k}][{3 + k}:v]overlay=0:0:enable='between(t,{a2:.2f},{b2:.2f})'" + (",format=yuv420p[v]" if last else f"[c{k + 1}]"))
    # audio: every line at its new time
    items: list[tuple[float, Path, float]] = []
    for e in events:
        at = e.get("at_override", e["at"])
        if e["kind"] in ("user", "alexa"):
            fa, _ = fast_audio(e["audio"])
            items.append((remap(at, iv), fa, 1.0))
        elif e["kind"] == "clip":
            clip = source_clip(e["start_s"], e["dur"], WORK / f"{name}-clip-{int(e['start_s'])}.wav", source)
            items.append((remap(at, iv), clip, 0.9))
    items += [(remap(t, iv), f, v) for t, f, v in extra_audio]
    audio = mix_audio(items, total, WORK / f"{name}.wav")
    out = WORK / f"{name}.mp4"
    run(["ffmpeg", "-y", "-i", str(src), "-i", str(audio), "-loop", "1", "-i", str(cap), *banner_inputs, "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", str(out)])
    print(name, "kept", round(total, 1), "s of", round(seg_len, 1))
    return out


def main() -> None:
    tl = json.loads((OUT / "timeline.json").read_text())
    kinds = ("user", "alexa", "clip", "handsfree", "import_start", "stage", "import_done", "tail")
    s1 = [e for e in tl if e["seg"] == "seg1" and e["kind"] in kinds]
    # hands-free is switched on once in the video; after the reload it is switched on again off camera
    s2 = [e for e in tl if e["seg"] == "seg2" and e["kind"] in kinds and e["kind"] != "handsfree"]
    s3 = [e for e in tl if e["seg"] == "seg3" and e["kind"] in kinds and e["kind"] != "handsfree"]
    # the new video: say how long the real import took, and give the narration room over the sped-up part
    t_start = next(e["at"] for e in s3 if e["kind"] == "import_start")
    t_done = next(e["at"] for e in s3 if e["kind"] == "import_done")
    took_s = int(round(t_done - t_start))
    took = f"{took_s // 60} minutes {took_s % 60} seconds" if took_s >= 60 else f"{took_s} seconds"
    na, nd = narr("newvideo", took=took)
    for e in s3:
        if e["kind"] == "import_start":
            e["dur"] = max(4.0, nd - 4.0)
    tail = next(e for e in s3 if e["kind"] == "tail")
    ta, td = narr("trace")
    tail["dur"] = td + 0.6
    banner = f"First time watching this video: {took_s // 60} min {took_s % 60:02d} s in real time, sped up here"
    pieces = [
        intro(),
        card("title", "title", 3.5, 0.4),
        segment("seg1", s1, lead=1.4),
        card("restart", "restart", 3.2, 0.4),
        segment("seg2", s2, lead=0.6),
        card("fresh", "fresh", 2.4, 0.3),
        segment("seg3", s3, lead=0.6, extra_audio=[(t_start + 0.2, na, 1.0), (tail["at"] + 0.3, ta, 1.0)],
                banners=[(t_start - 0.5, t_done + 0.3, banner)], source=new_video_source()),
        card("end", "end", 4.2, 1.0),
    ]
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in pieces))
    final = OUT / "second-look-demo.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-r", str(FPS), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", str(final)])
    print("FINAL", final, round(dur(final), 1), "s", "| import took", took)


if __name__ == "__main__":
    main()
