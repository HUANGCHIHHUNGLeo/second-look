# Devpost submission draft: Second Look

## Project name
Second Look

## Elevator pitch (tagline)
Alexa+ follows a cooking video with you, hands-free, because it actually watched it: answers with timestamps, shows the exact frame, remembers your step.

## Track and mini challenges
- Primary track: **Alexa+** (self-hosted MCP server, spec 2025-11-25, Streamable HTTP; demoed through a simulated Alexa+ screen that is a real MCP client, as the FAQ describes)
- Mini challenges: **AWS Builder** (Strands Agents SDK) · **Open Source**

## About the project

### Inspiration
Cooking from a video is one of the most common things people do with a screen in the kitchen, and one of the most frustrating. Your hands are covered in dough, the video keeps going, a quantity is said once and gone, and you never know whether yours looks right. Recipe apps turn the captions into text. But the most useful part of a cooking video is what you can *see*: how thick, how shiny, how the thumb drags. We wanted Alexa+ to be able to look at the video for you.

### What it does
Second Look gives Alexa+ a video it has actually watched:
- **Import any how-to video.** crv (our open-source video reader) extracts a keyframe at every scene change and a timestamped transcript. A multimodal model writes a step-by-step guide from frames and words together. Every step is pinned to a real frame, a time range and the transcript lines behind its quantities, plus a "visible but never said" note. The guide is validated against the video before it is stored, so it can only cite things that are actually in the video.
- **Cook along by voice.** "Next." "Go back." "Repeat that." One step at a time, never skipping ahead on its own.
- **Ask the video.** "How much water did she use?" The answer comes from what was said, pinned to the exact line ("about three quarters of a cup of cold water to start; she says it at 1:24"), and the evidence cards are on screen. Tap one to replay exactly that moment.
- **"Show me what it should look like."** The exact frame from the video for that step, described, including details the narrator never says.
- **Timers from the video.** The 30-minute rest becomes a real timer, stored as a deadline.
- **Resume anywhere.** Close the browser, restart the server, come back tomorrow: "I'm back, where was I?" picks up at your step with the timer still counting.

### How we built it
- **crv-guide MCP server** (Python, official MCP SDK 2.1, Streamable HTTP, protocol 2025-11-25): 10 tools (`import_video`, `get_import`, `list_guides`, `start_session`, `get_session`, `move_step`, `ask_source`, `show_moment`, `set_timer`, `cancel_timer`). Tool results return text, structured content and images (the video frames). SQLite keeps guides, sessions, step progress (with a revision check so two screens can't double-move) and absolute timer deadlines. The endpoint has DNS-rebinding protection and a CORS allow-list.
- **Guide compiler**: crv's keyframes + transcript go to Gemini (OpenAI vision as fallback) with a strict JSON schema; the result is validated against the real video (frame numbers, time ranges, transcript line numbers).
- **Evidence search**: BM25 over timestamped transcript windows and the frame-grounded steps, then each hit is pinned to the single transcript line that answers the question, so answers cite a moment (1:24), not a range; the assistant answers only from what comes back, and says so when the video doesn't cover something.
- **Voice-ready replies**: session tools return a short `speak` line (like a skill's output speech) next to the full card, so Alexa says one or two sentences while the screen carries the detail.
- **Simulated Alexa+ screen** (Echo Show style web app): Web Speech API voice in/out, step cards with the video frame, evidence cards, clip replay, timer chips, and a live MCP trace. The page itself is an MCP client (initialize with protocol 2025-11-25, tools/call), handling the on-screen buttons and state.
- **Conversation**: an AWS **Strands Agents** agent with `MCPClient` over Streamable HTTP and an `AfterToolCallEvent` hook that sends every tool result to the screen as a card (model provider: OpenAI gpt-4.1-mini or Gemini through Strands).

### Challenges we ran into
- Alexa+ developer tools (MCP Toolkit, Web Simulator) are select-partners only, so we built a faithful simulation and kept the MCP server standard and spec-compliant (2025-11-25) so it is ready for the real thing.
- Automatic transcripts mishear cooking words ("need" for "knead", "Orcetti" for "orecchiette"). Writing steps from frames and words together fixes the wording while keeping the evidence pointing at the original lines.
- Grounding: models love to invent quantities. Every step is validated against the video, and answers go through an evidence search first.
- Model availability on deadline night (Gemini returned 503 "high demand" for several models), so the guide compiler retries and falls back across models and providers.

### Accomplishments that we're proud of
- "What should it look like?" answered with the actual frame from the video, including details the narration never says.
- True persistence: the demo closes the browser and restarts the server, and Alexa still knows your step and your timer.
- Two different MCP clients (the Strands agent and the screen) sharing one server's state, visible live in the MCP trace.

### What we learned
MCP is a good fit for "skills" that need memory and media: structured content plus images in one tool result lets the voice side talk and the screen side show, from the same call.

### What's next
Support any how-to category (repairs, crafts, workouts), let the user point the camera at their own result and compare it to the video's frame, and run on a real Alexa+ device when the developer tools open up.

## Built with
python, mcp, model-context-protocol, streamable-http, strands-agents, aws, gemini, openai, crv, claude-real-video, ffmpeg, sqlite, starlette, web-speech-api, javascript, alexa

## Try it out
- Code: https://github.com/HUANGCHIHHUNGLeo/second-look
- Demo video: (YouTube link)

## If your project existed before the hackathon
Second Look and its MCP server were built during the hackathon. It uses crv (claude-real-video), an open-source video reader by the same author that existed before: crv extracts keyframes and a transcript and offered a local stdio MCP server. Everything else is new: the Streamable HTTP MCP server (spec 2025-11-25), the guide compiler and its validation, persistent sessions, step navigation, evidence search, exact-moment frames, timers, the simulated Alexa+ screen and the Strands agent. crv is used unchanged as a pinned library (0.10.7).

## AWS Builder: which services and how
Strands Agents SDK (strands-agents 1.62.0) runs the conversation in `second_look/agent.py`: `strands.tools.mcp.MCPClient(url=...)` connects to the MCP server over Streamable HTTP and loads its tools; `strands.Agent` runs the tool loop with a `SlidingWindowConversationManager`; an `AfterToolCallEvent` hook forwards tool results to the screen. Models are called through Strands' OpenAI and Gemini providers; the agent loop runs locally and no AWS-hosted service is used.

## Open Source
(Filled in at submission: contribution URL, repo URL, GitHub username, description.)

## Product feedback (required)
See docs/product-feedback.md.
