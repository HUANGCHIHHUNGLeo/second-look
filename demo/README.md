# How the demo video is made

- `record.py` drives the real app in Chromium (Playwright, 1920x1080). It types each line into the app as if it were spoken, waits for the real reply from the Strands agent and the MCP server, and restarts both servers between the two halves.
- While recording, the lines are voiced with quick OpenAI `gpt-4o-mini-tts` placeholders, only to pace the take.
- `revoice.py` then voices every line with Google Gemini text-to-speech (`gemini-3.8-flash-tts`): each segment's lines as one two-speaker conversation (the cook and Alexa), the narration as one read, cut back into lines with Whisper word timings. Alexa says exactly the text the agent returned.
- `compose.py` cuts the time spent waiting for the model, plays the dialogue faster (`DEMO_SPEED`), places every voice on its real timestamp, and adds the title cards and narration (`cards/`, rendered by `render_cards.py`).

```sh
export OPENAI_API_KEY=... GEMINI_API_KEY=...   # voices and word timings; the app itself uses .env
DEMO_OUT=/tmp/sl-demo .venv/bin/python demo/record.py
DEMO_OUT=/tmp/sl-demo DEMO_AUDIO_G=/tmp/sl-demo-g .venv/bin/python demo/revoice.py
DEMO_OUT=/tmp/sl-demo DEMO_AUDIO=/tmp/sl-demo-g DEMO_TTS_LOCKED=1 .venv/bin/python demo/compose.py   # -> /tmp/sl-demo/second-look-demo.mp4
```

Needs ffmpeg, Playwright's Chromium (`.venv/bin/playwright install chromium`) and the sample video (`server/scripts/fetch_sample.sh`).
