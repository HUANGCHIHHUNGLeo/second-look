# Devpost submission draft: Second Look

## Project name
Second Look

## Elevator pitch (tagline)
Alexa+ follows a cooking video with you, hands-free, because it actually watched it: answers with the moment it was said, shows the frame, remembers your step.

## Track and mini challenges
- Primary track: **Alexa+** (self-hosted MCP server, spec 2025-11-25, Streamable HTTP; demoed through a simulated Alexa+ screen that is a real MCP client, as the FAQ describes)
- Mini challenges: **AWS Builder** (Strands Agents SDK) · **Open Source**

## About the project

### Inspiration
Learning a dish from a video is common, and frustrating in the kitchen. Your hands are covered in dough, the video keeps going, a quantity is said once and gone, and you never know whether yours looks right. Recipe tools can turn a video's captions into text. But the most useful part of a cooking video is what you can *see*: how thick, how shiny, how the thumb drags. We wanted Alexa+ to be able to look at the video for you.

### What it does
Second Look gives Alexa+ a video it has actually watched:
- **Import a how-to video.** crv (our open-source video reader) extracts a keyframe at every scene change and a timestamped transcript. A multimodal model writes a step-by-step guide from frames and words together. Every step points to a real frame, a time range and the transcript lines behind it, plus a "visible but never said" note. Before the guide is stored, every number in every step (amounts, durations, temperatures) is checked against the narration, with its unit and the ingredient word next to it; numbers that aren't said are flagged on screen, and a timer is only offered for a duration that is said. It is a lexical check, not a proof of meaning, but it catches invented and swapped amounts.
- **Hands-free.** Like an Echo Show, the screen keeps listening and acts only on sentences that start with "Alexa". It closes the microphone while Alexa talks and while a clip plays (the view reports when playback starts and stops), and re-arms by itself.
- **Cook along by voice.** "Next." "Go back." "Repeat that." One step at a time, never skipping ahead on its own.
- **Ask the video.** "How much water did she use?" The answer comes from the line where she says it ("about three quarters of a cup of cold water to start; she says it at 1:24"), with evidence cards you can tap to replay that moment. "How much sugar?" gets "the video never mentions sugar", not the nearest other number.
- **"Show me how she does it."** For a technique described in words ("pressing the thumb into the dough"), it finds the moment she describes it, shows the frame a beat later and replays the clip, and describes details the narrator never says.
- **Timers from the video.** The 30-minute rest becomes a real timer, stored as a deadline.
- **Resume later.** State lives on the server, not in the page. Close the browser, restart the server, come back later: "I'm back, where was I?" picks up at your step, and the timer kept its deadline ("your dough rest timer has 29 minutes left").

### How we built it
- **An Alexa+-style add-on view over MCP Apps**: Amazon's Alexa+ add-on guide describes an add-on screen as a self-contained HTML bundle that Alexa shows in a sandboxed iframe and drives over the MCP Apps postMessage bridge. Our server registers `ui://second-look/cook.html` (`text/html;profile=mcp-app`, CSP in `_meta.ui`) with the MCP Python SDK's Apps extension, and the session tools point at it with `_meta.ui.resourceUri`. The view is authored on Amazon's 768 px base canvas with the guide's type, spacing, radius and dark card tokens.
- **crv-guide MCP server** (Python, official MCP SDK 2.1, Streamable HTTP, protocol 2025-11-25): 10 tools (`import_video`, `get_import`, `list_guides`, `start_session`, `get_session`, `move_step`, `ask_source`, `show_moment`, `set_timer`, `cancel_timer`). Tool results return text, structured content and images (the video frames). SQLite keeps guides, sessions, step progress (with a revision check so two screens can't double-move) and absolute timer deadlines. The endpoint has DNS-rebinding protection and a CORS allow-list.
- **Guide compiler**: crv's keyframes + transcript go to Gemini with a JSON response schema (OpenAI vision as fallback, in JSON mode, validated with Pydantic). The result is checked against the real video: frame numbers, time ranges and transcript line numbers must exist, and every number in an instruction must appear in the narration it cites.
- **Evidence search**: BM25 over timestamped transcript windows and the frame-grounded steps; each hit is pinned to the single transcript line that answers the question, so answers cite a moment (1:24), not a range. Amount questions only match passages that mention what was asked about, and the tool reports words the video never mentions.
- **Voice-ready replies**: session tools return a short `speak` line (like a skill's output speech) next to the full card, so Alexa says one or two sentences while the screen carries the detail. Simple intents (next, back, repeat, "where was I") go straight to the MCP tools without a model call.
- **Simulated Alexa+ host** (Echo Show 15 class screen): an MCP client and an MCP Apps host. It reads the view resource, mounts it in a `sandbox="allow-scripts"` iframe, answers `ui/initialize` with a host context, forwards each tool result from the agent's turn (`ui/notifications/tool-result`) and proxies the view's own `tools/call`. Around it: Web Speech API voice in/out with a wake-word hands-free mode, Alexa's timers, and a live trace of both MCP and MCP Apps traffic.
- **Conversation**: an AWS **Strands Agents** agent with `MCPClient` over Streamable HTTP and an `AfterToolCallEvent` hook that sends every tool result to the screen as a card (model provider: OpenAI gpt-4.1-mini or Gemini through Strands).
- **Tests**: unit tests for the guide checks, evidence search and sessions, an MCP smoke client, and a browser test that drives hands-free mode with a scripted speech recognizer.

### Challenges we ran into
- Alexa+ developer tools (MCP Toolkit, Web Simulator) are select-partners only, so we built a simulation that is a real MCP client and kept the MCP server standard (spec 2025-11-25).
- Automatic transcripts mishear cooking words ("need" for "knead", "Orcetti" for "orecchiette"). Writing steps from frames and words together fixes the wording while keeping the evidence pointing at the original lines.
- Grounding: models invent quantities, and a keyword search will happily return the water amount when you ask about sugar. Numbers are now checked against the narration with their units and ingredient words, and an amount question about something the video never mentions returns no evidence.
- Hands-free in a kitchen means the page must not hear its own voice or the cook in the video, so listening pauses during speech and playback and re-arms afterwards.
- Model availability on deadline night (Gemini returned 503 "high demand" for several models), so the guide compiler retries and falls back across models and providers.

### Accomplishments that we're proud of
- "Show me how she makes the shape with her thumb" answered with the frame and clip from the video, including details the narration never says.
- True persistence: the demo closes the browser and restarts the server, and Alexa still knows your step and your timer.
- A video it had never seen, imported live in the demo, answered from the line where the cook says it.
- The add-on UI ships from the MCP server as an MCP Apps view, the way Alexa+ renders add-ons, and two different MCP clients (the Strands agent and the host) share one server's state, visible live in the MCP trace.

### What we learned
MCP is a good fit for "skills" that need memory and media: structured content plus images in one tool result lets the voice side talk and the screen side show, from the same call. MCP Apps closes the loop: the same server ships the screen itself, so the add-on's UI travels with the tools instead of living in each client. And grounding has to be checked in code; asking a model to "only use the video" is not enough.

### What's next
Test hands-free mode with real cooks in real kitchens, support more how-to categories (repairs, crafts, workouts), let the user point the camera at their own result and compare it to the video's frame, and run on a real Alexa+ device when the developer tools open up.

## Built with
python, mcp, model-context-protocol, streamable-http, strands-agents, aws, gemini, openai, crv, claude-real-video, ffmpeg, sqlite, starlette, web-speech-api, javascript, alexa

## Try it out
- Code: https://github.com/HUANGCHIHHUNGLeo/second-look
- Demo video: https://youtu.be/tgE5cRr50LM

## If your project existed before the hackathon
Second Look and its MCP server were built during the hackathon. It uses crv (claude-real-video), an open-source video reader by the same author that existed before: crv extracts keyframes and a transcript and offered a local stdio MCP server. Everything else is new: the Streamable HTTP MCP server (spec 2025-11-25), the guide compiler and its validation, persistent sessions, step navigation, evidence search, exact-moment frames, timers, the simulated Alexa+ screen and the Strands agent. crv is used unchanged as a pinned library (0.10.7).

## AWS Builder: which services and how
Strands Agents SDK (strands-agents 1.62.0) runs the conversation in `second_look/agent.py`: `strands.tools.mcp.MCPClient(url=...)` connects to the MCP server over Streamable HTTP and loads its tools; `strands.Agent` runs the tool loop with a `SlidingWindowConversationManager`; an `AfterToolCallEvent` hook forwards tool results to the screen. Models are called through Strands' OpenAI and Gemini providers; the agent loop runs locally and no AWS-hosted service is used.

## Open Source
- Contribution URL: https://github.com/HUANGCHIHHUNGLeo/claude-real-video/pull/31
- Project repository URL: https://github.com/HUANGCHIHHUNGLeo/claude-real-video
- GitHub username: HUANGCHIHHUNGLeo
- What, how, why: While building Second Look we found that a fresh install of crv (claude-real-video, the open-source video reader Second Look uses to watch videos) could no longer transcribe: pip now resolves PyAV 19, and faster-whisper still passes `metadata_errors` to `av.open()`, which PyAV 16 removed. Both transcription and the voice-activity pre-check failed, so crv quietly produced no transcript, and anything built on it (like a cooking guide) had nothing to work from. The pull request pins `av<16` in the `fast` and `mlx` extras until faster-whisper is updated; we reproduced the failure with av 19.0.2 and confirmed the same 11-minute video transcribes with av 15.1.0. Second Look also ships the code it adds: the crv-guide MCP server is reusable by any MCP client (MIT).

## Product feedback (required)
See docs/product-feedback.md.
