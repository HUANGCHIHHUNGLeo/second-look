# How the demo video is made

- `record.py` drives the real app in Chromium (Playwright, 1920x1080). It types each line into the app as if it were spoken, waits for the real reply from the Strands agent and the MCP server, and restarts both servers between the two halves.
- The cook's lines and Alexa's replies are voiced with OpenAI `gpt-4o-mini-tts`. Alexa says exactly the text the agent returned.
- `compose.py` cuts the time spent waiting for the model, plays the dialogue 15% faster, places every voice on its real timestamp, and adds the title cards and narration (`cards/`, rendered by `render_cards.py`).

```sh
export OPENAI_API_KEY=...            # voices; the app itself uses .env
DEMO_OUT=/tmp/sl-demo .venv/bin/python demo/record.py
DEMO_OUT=/tmp/sl-demo .venv/bin/python demo/compose.py   # -> /tmp/sl-demo/second-look-demo.mp4
```

Needs ffmpeg, Playwright's Chromium (`.venv/bin/playwright install chromium`) and the sample video (`server/scripts/fetch_sample.sh`).
