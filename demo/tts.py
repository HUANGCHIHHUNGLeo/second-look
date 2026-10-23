"""OpenAI text-to-speech for the demo soundtrack (narrator, the person cooking, Alexa)."""
import hashlib, json, os, subprocess, sys
from pathlib import Path
from openai import OpenAI

OUT = Path(os.environ.get("DEMO_AUDIO", "/tmp/sl-demo-audio"))
OUT.mkdir(parents=True, exist_ok=True)
VOICES = {"narrator": ("onyx", "Calm, warm documentary narrator. Unhurried."),
          "user": ("echo", "A home cook talking to a smart speaker in the kitchen, casual and a bit busy."),
          "alexa": ("nova", "A friendly, clear voice assistant. Natural, helpful, not robotic.")}
_client = None

def tts(role: str, text: str) -> tuple[Path, float]:
    global _client
    voice, style = VOICES[role]
    key = hashlib.sha1(f"{role}|{text}".encode()).hexdigest()[:16]
    path = OUT / f"{role}-{key}.mp3"
    words = len(text.split())
    for attempt in range(4):
        if not path.exists():
            _client = _client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            with _client.audio.speech.with_streaming_response.create(model="gpt-4o-mini-tts", voice=voice, input=text, instructions=style, response_format="mp3") as r:
                r.stream_to_file(path)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip())
        # the TTS model sometimes stops early; nobody speaks faster than ~3.6 words a second
        if words < 8 or words / dur <= 3.6:
            return path, dur
        path.rename(path.with_suffix(f".short{attempt}.mp3"))
    raise RuntimeError(f"TTS keeps cutting off: {text[:60]}")

if __name__ == "__main__":
    p, d = tts(sys.argv[1], sys.argv[2]); print(p, d)
