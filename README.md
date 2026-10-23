# Second Look

**Alexa+ follows a cooking video with you, hands-free, because it actually watched the video.**

Cooking from a video is awkward when your hands are covered in flour: the video keeps going, you can't scrub back, a quantity is said once and gone, and you never know if yours looks right. Recipe apps turn the *captions* into a text recipe. Second Look gives Alexa+ the video itself:

- **"How much water?"** It answers from what was said *and when*: "about three quarters of a cup to start, then a little at a time; that's at 1:24 in the video", with the evidence on screen.
- **"What should the dough look like now?"** It shows the exact frame from the video for that step and describes details the narrator never says (hand position, thickness, texture).
- **"Next." "Go back." "Repeat that."** One step at a time, read aloud, never skipping ahead on its own.
- **"Set a timer for the rest."** The 30-minute rest from the video becomes a real timer.
- **"I'm back, where was I?"** Close the app, restart everything, come back tomorrow: your step and your timers are still there.

Track: **Alexa+** (self-hosted MCP server, spec 2025-11-25, Streamable HTTP) · Mini challenges: **AWS Builder** (Strands Agents SDK), **Open Source**.

> Demo video: *(link added at submission)*

![Second Look screen](docs/screen.png)

---

## How it works

```
                 ┌──────────────────────────── Second Look (simulated Alexa+ Echo Show) ─────────────┐
 you ── voice ──▶│ browser: Web Speech API ──▶ /api/turn ──▶ Strands Agent (AWS Strands Agents SDK)  │
                 │   the screen itself is also an MCP client          │ MCPClient (Streamable HTTP) │
                 └───────────────────┬────────────────────────────────┼─────────────────────────────┘
                                     │ MCP: initialize / tools/list / tools/call (protocol 2025-11-25)
                                     ▼                                ▼
                 ┌──────────────────────── crv-guide MCP server (this repo, server/) ──────────────────┐
                 │ import_video ─▶ crv watches the video (scene-change keyframes + timestamped transcript)│
                 │              ─▶ a multimodal model writes the guide from frames AND words,           │
                 │                 every step pinned to real frame numbers, times and transcript lines   │
                 │ start_session · get_session · move_step · ask_source · show_moment · set_timer …      │
                 │ SQLite: guides, sessions, step progress, timer deadlines (survives restarts)          │
                 └────────────────────────────────────────────────────────────────────────────────────────┘
```

Two different MCP clients talk to the same server and share its state: the **Strands agent** (conversation) and the **screen** (renders cards, handles the Back / Repeat / Next buttons). The **MCP trace** button on the screen shows every JSON-RPC message live: `initialize` requesting protocol `2025-11-25`, the negotiated version, `tools/call` with its arguments.

Alexa+ developer tools (MCP Toolkit, Web Simulator) are select-partners only, so, as the hackathon FAQ describes, the device is simulated by a web page that acts as a real MCP client. The server itself is a standard MCP server any MCP client can use.

### MCP tools (server/src/crv_guide_mcp/server.py)

| Tool | What it does |
|---|---|
| `import_video(source)` | Watch a video (URL, local file, or `sample:pasta`) and build a guide. Returns a job id at once. |
| `get_import(job_id)` | Processing stage, or the finished guide outline (steps, ingredients, durations). |
| `list_guides()` | Guides already built. |
| `start_session(guide_id)` | Start cooking at step 1. Returns the step card + the step's video frame (image). |
| `get_session(session_id?)` | "Where was I?" Resumes the latest unfinished session, with live timer time-left. |
| `move_step(session_id, next/back/repeat/goto)` | Moves only when asked; `expected_revision` guards against double moves from two screens. |
| `ask_source(question)` | BM25 search over what was *said* (timestamped transcript windows) and what each step *shows*; returns passages + matching frames as images. The assistant answers only from these. |
| `show_moment(step_number? \| at_seconds?)` | The exact frame (grabbed from the source video at that second) + a replayable clip range. |
| `set_timer(seconds, label, request_id?)` / `cancel_timer` | Timers stored as absolute deadlines; idempotent with `request_id`. |

Every step in a guide keeps its evidence: the frame number it was written from, its time range, the transcript lines behind each quantity, and a `visual_note` for something visible in the frames that the narration never says. The guide is validated against the real video before it is stored (frame numbers must exist and fall inside the step, times are clamped, line numbers must exist), so it can only cite things that are in the video.

---

## Run it

Requirements: Python 3.11–3.13, `ffmpeg` on PATH, and one model key (OpenAI or Gemini). Tested on macOS (Apple Silicon).

```sh
git clone https://github.com/HUANGCHIHHUNGLeo/second-look && cd second-look
python3 -m venv .venv && .venv/bin/pip install -e .        # or: uv venv && uv pip install -e .
server/scripts/fetch_sample.sh                             # sample video for clip replay (CC BY, ~150 MB)
cp .env.example .env                                       # add OPENAI_API_KEY (or GEMINI_API_KEY + SECOND_LOOK_PROVIDER=gemini)
./run.sh                                                   # MCP server :8765 + screen :8770
```

Open http://127.0.0.1:8770 in Chrome, click **Load the sample video**, then tap the mic (or press Space) and talk, or type. The sample's guide ships pre-built in `server/fixtures/pasta/`, so it loads instantly; paste any other video URL to watch a new one (needs `GEMINI_API_KEY` for the guide step; a 2–3 minute video takes about a minute to watch, plus the model's time).

Use the server from any MCP client: `.venv/bin/crv-guide-mcp --port 8765`, then connect to `http://127.0.0.1:8765/mcp` (Streamable HTTP). Smoke test with a real MCP client: `.venv/bin/python server/tests/smoke_client.py`.

---

## What was built during the hackathon

**Before** (existing open-source project by the same author): [claude-real-video (crv)](https://github.com/HUANGCHIHHUNGLeo/claude-real-video) could extract keyframes and a transcript from a video, with a local stdio MCP server for chat clients.

**Built for this hackathon (all of this repo):** the crv-guide Streamable HTTP MCP server (spec 2025-11-25) with persistent cooking sessions, step navigation, timers and evidence search; the frames-plus-transcript guide compiler with validation against the source video; exact-moment frame extraction and clip replay; the simulated Alexa+ screen with voice, cards and a live MCP trace; the Strands agent. crv is used unchanged as a library (pinned 0.10.7).

## AWS Builder mini challenge

The conversation runs on the **Strands Agents SDK** (AWS's open-source agent framework), in `second_look/agent.py`:

- `strands.tools.mcp.MCPClient(url=...)` connects to the crv-guide server over Streamable HTTP and loads its tools at startup (`list_tools_sync`).
- `strands.Agent(model=..., tools=..., hooks=[UiEvents()], conversation_manager=SlidingWindowConversationManager(16))` runs the tool loop. An `AfterToolCallEvent` hook forwards every tool result (step cards, evidence, frames, timers) to the screen.
- Model providers through Strands: `strands.models.openai.OpenAIModel` (default `gpt-4.1-mini`) or `strands.models.gemini.GeminiModel`. Inference runs on the provider's API; the agent loop runs locally. No AWS-hosted services are used.

## Security and privacy

- No keys in the repo; everything comes from `.env` (git-ignored). The screen never sees a key: the agent runs server-side.
- The MCP endpoint has DNS-rebinding protection (allowed Host/Origin lists) and a CORS allow-list limited to the screen's origin.
- Data stays local (SQLite + files under `~/.crv-guide`) except what is sent to the chosen model provider (frames and transcript for the guide; conversation and tool results for the agent).

## Honest limits

- Not tested on a real Alexa+ device or console: those tools are select-partners only. The simulated screen speaks MCP exactly as described above.
- Timers ring while the screen is open; after a restart they show "done" or the correct time left (deadlines are stored), but there are no background notifications.
- Voice uses the browser's Web Speech API (Chrome). Typing works everywhere.
- Answers are only as good as what the video shows and says; when the search finds nothing the assistant says the video doesn't cover it.

## Credits

- Sample video: "Cooking with Laura: Homemade Pasta" by Northampton Community Television, [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/), via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Cooking_with_Laura_Homemade_Pasta.webm). The bundled keyframes and transcript in `server/fixtures/pasta/` are derived from it under the same license.
- Built with [crv / claude-real-video](https://github.com/HUANGCHIHHUNGLeo/claude-real-video), the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Strands Agents](https://strandsagents.com), Gemini and OpenAI models.

License: MIT (code). Sample media: CC BY 3.0 as above.
