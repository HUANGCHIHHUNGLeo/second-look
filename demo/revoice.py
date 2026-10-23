"""Re-voices a recorded take with Google Gemini text-to-speech, without recording it again.

record.py paces a take with quick placeholder voices. This script asks Gemini for each segment's
lines as one two-speaker conversation (the cook and Alexa) and for the narration as one read,
cuts the audio back into lines using Whisper word timings, and points the take's timeline at the
new clips. compose.py then lays them out as before (run it with DEMO_TTS_LOCKED=1 so nothing
falls back to the placeholder voices).

    DEMO_OUT=/tmp/sl-demo18 DEMO_AUDIO_G=/tmp/sl-demo-audio-g python demo/revoice.py
    (needs GEMINI_API_KEY and OPENAI_API_KEY; voices: VOICE_USER, VOICE_ALEXA, VOICE_NARRATOR)
"""

from __future__ import annotations

import array
import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OUT = Path(os.environ.get("DEMO_OUT", "/tmp/sl-demo"))
AUDIO = Path(os.environ.get("DEMO_AUDIO_G", "/tmp/sl-demo-audio-g"))
MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
VOICES = {"user": os.environ.get("VOICE_USER", "Puck"), "alexa": os.environ.get("VOICE_ALEXA", "Sulafat"),
          "narrator": os.environ.get("VOICE_NARRATOR", "Charon")}
SPEAKER = {"user": "Cook", "alexa": "Alexa"}
RATE = 24000


def gemini(parts: list[tuple[str, str]], single_voice: str | None = None) -> bytes:
    """Mono 16-bit PCM at 24 kHz for the given (role, text) parts."""
    if single_voice:
        contents = [{"parts": [{"text": "\n\n".join(t for _, t in parts)}]}]
        speech = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": single_voice}}}
    else:
        contents = [{"parts": [{"text": t, "speechMetadata": {"speaker": SPEAKER[r]}} for r, t in parts]}]
        speech = {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": [
            {"speaker": SPEAKER[r], "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICES[r]}}} for r in ("user", "alexa")]}}
    body = {"contents": contents, "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": speech}}
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Gemini TTS {e.code}: {e.read().decode()[:400]}")
    p = r["candidates"][0]["content"]["parts"][0]["inlineData"]
    data, mt = base64.b64decode(p["data"]), p["mimeType"]
    if "wav" in mt.lower():
        with wave.open(_tmp_wav(data)) as w:
            assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, mt
            return w.readframes(w.getnframes())
    a = array.array("h", data)
    if "L16" in mt.upper():
        a.byteswap()  # audio/L16 is big-endian
    return a.tobytes()


def _tmp_wav(data: bytes) -> str:
    path = AUDIO / "_last.wav"
    path.write_bytes(data)
    return str(path)


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def whisper_words(path: Path) -> list[dict]:
    out = subprocess.run(["curl", "-s", "https://api.openai.com/v1/audio/transcriptions",
                          "-H", f"Authorization: Bearer {os.environ['OPENAI_API_KEY']}",
                          "-F", f"file=@{path}", "-F", "model=whisper-1", "-F", "response_format=verbose_json",
                          "-F", "timestamp_granularities[]=word"], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["words"]


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def align(lines: list[str], words: list[dict]) -> list[tuple[int, int]]:
    """For each expected line, the first and last Whisper word that belongs to it."""
    exp, exp_line = [], []
    for k, line in enumerate(lines):
        for t in tokens(line):
            exp.append(t)
            exp_line.append(k)
    rec, rec_word = [], []
    for j, w in enumerate(words):
        for t in tokens(w["word"]):
            rec.append(t)
            rec_word.append(j)
    hits: dict[int, list[int]] = {}
    for blk in difflib.SequenceMatcher(None, exp, rec, autojunk=False).get_matching_blocks():
        for d in range(blk.size):
            hits.setdefault(exp_line[blk.a + d], []).append(rec_word[blk.b + d])
    spans = []
    for k, line in enumerate(lines):
        got = hits.get(k)
        if not got:
            raise SystemExit(f"line {k} not found in the audio: {line[:70]}")
        coverage = len(got) / max(1, len(tokens(line)))
        if coverage < 0.6:
            raise SystemExit(f"line {k} only {coverage:.0%} recognised: {line[:70]}")
        spans.append((min(got), max(got)))
    for a, b in zip(spans, spans[1:]):
        if b[0] <= a[1]:
            raise SystemExit(f"lines overlap in the audio: {a} {b}")
    return spans


def quietest(pcm: array.array, t0: float, t1: float) -> float:
    """Time of the quietest 20 ms in [t0, t1] (where to cut between two lines)."""
    win = int(0.02 * RATE)
    a, b = max(0, int(t0 * RATE)), min(len(pcm) - win, int(t1 * RATE))
    if b <= a:
        return (t0 + t1) / 2
    best, best_t = None, (t0 + t1) / 2
    for s in range(a, b, win // 2):
        e = sum(abs(x) for x in pcm[s:s + win])
        if best is None or e < best:
            best, best_t = e, (s + win / 2) / RATE
    return best_t


def trim(pcm: array.array, floor: int = 300, pad: float = 0.04) -> array.array:
    """Drop leading and trailing near-silence, keep a short pad."""
    idx = [i for i in range(0, len(pcm), 120) if abs(pcm[i]) > floor]
    if not idx:
        return pcm
    p = int(pad * RATE)
    return pcm[max(0, idx[0] - p): min(len(pcm), idx[-1] + p)]


def split(pcm_bytes: bytes, lines: list[str], name: str) -> list[array.array]:
    raw = AUDIO / f"{name}.wav"
    write_wav(raw, pcm_bytes)
    words = whisper_words(raw)
    spans = align(lines, words)
    pcm = array.array("h", pcm_bytes)
    cuts = [0.0]
    for (_, end_a), (start_b, _) in zip(spans, spans[1:]):
        cuts.append(quietest(pcm, words[end_a]["end"] - 0.05, words[start_b]["start"] + 0.05))
    cuts.append(len(pcm) / RATE)
    return [trim(pcm[int(a * RATE):int(b * RATE)]) for a, b in zip(cuts, cuts[1:])]


def dur(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True).stdout.strip())


def main() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    tl_path = OUT / "timeline.json"
    keep = OUT / "timeline.placeholder.json"
    if not keep.exists():
        shutil.copy(tl_path, keep)  # the take as recorded, with the placeholder voices
    timeline = json.loads(keep.read_text())

    # 1. the conversation, one request per segment
    speed = float(os.environ.get("DEMO_SPEED", "1.2"))
    rows = []
    for seg in os.environ.get("REVOICE_SEGS", "seg1,seg2,seg3").split(","):
        events = [e for e in timeline if e["seg"] == seg]
        said = [e for e in events if e["kind"] in ("user", "alexa")]
        clips = split(gemini([(e["kind"], e["text"]) for e in said]), [e["text"] for e in said], f"{seg}-dialogue")
        for e, clip in zip(said, clips):
            path = AUDIO / f"{seg}-{e['kind']}-{hashlib.sha1(e['text'].encode()).hexdigest()[:10]}.wav"
            write_wav(path, clip.tobytes())
            later = [x["at"] for x in events if x["at"] > e["at"]]
            slot = (later[0] - e["at"]) if later else None
            e["audio_placeholder"], e["dur_placeholder"] = e["audio"], e["dur"]
            e["audio"], e["dur"] = str(path), round(dur(path), 3)
            fits = slot is None or e["dur"] / speed <= slot - 0.1
            rows.append((seg, e["kind"], e["dur_placeholder"], e["dur"], slot, fits, e["text"][:60]))
    tl_path.write_text(json.dumps(timeline, indent=1))

    # 2. the narration, one request; saved where compose.py's tts() looks for it
    from compose import NARR  # noqa: E402  (same texts compose.py will ask for)
    t_start = next(e["at"] for e in timeline if e["seg"] == "seg3" and e["kind"] == "import_start")
    t_done = next(e["at"] for e in timeline if e["seg"] == "seg3" and e["kind"] == "import_done")
    took_s = int(round(t_done - t_start))
    took = f"{took_s // 60} minutes {took_s % 60} seconds" if took_s >= 60 else f"{took_s} seconds"
    texts = [NARR[k].format(took=took) for k in NARR]
    if all((AUDIO / f"narrator-{hashlib.sha1(f'narrator|{t}'.encode()).hexdigest()[:16]}.mp3").exists() for t in texts):
        texts = []  # the narration for these exact texts was made earlier
    clips = split(gemini([("narrator", t) for t in texts], single_voice=VOICES["narrator"]), texts, "narration") if texts else []
    for text, clip in zip(texts, clips):
        key = hashlib.sha1(f"narrator|{text}".encode()).hexdigest()[:16]
        wav = AUDIO / f"narrator-{key}.wav"
        write_wav(wav, clip.tobytes())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(AUDIO / f"narrator-{key}.mp3")], check=True)
        rows.append(("narr", "narrator", None, round(dur(AUDIO / f"narrator-{key}.mp3"), 3), None, True, text[:60]))

    for seg, kind, old, new, slot, fits, text in rows:
        flag = "" if fits else "  <-- longer than its slot"
        print(f"{seg:5} {kind:8} {old if old is not None else '':>6} -> {new:6.2f}  slot {slot if slot is None else round(slot, 2)!s:>6}  {text}{flag}")


if __name__ == "__main__":
    main()
