# Product feedback (from building Second Look, Oct 23 2026)

Everything below is what we actually ran into while building this project, with versions.

## Alexa+ (Agent Skills / MCP integration)
- **Used for:** the target platform. We built a self-hosted MCP server (spec 2025-11-25, Streamable HTTP) meant to be an Alexa+ integration.
- **What worked well:** choosing MCP as the integration standard. It let us build and test the whole skill with ordinary MCP tooling and two different clients. The add-on guide's Visual Foundations (768 px base canvas, type, spacing, radius and card colour tokens) and Layout and Rendering pages (UI as an MCP Apps HTML bundle in a sandboxed iframe) were concrete enough to build the add-on view directly against them.
- **What needs work:** the Add-on developer tools (Category SDK, MCP Toolkit, CLI, Web Simulator) are select-partners only, and some setup-guide pages don't say so (the hackathon FAQ clarified it). We could not see how Alexa+ renders image content from a tool result, or how it treats `structuredContent`, so we designed the screen from public Echo Show conventions.
- **Onboarding:** reading the Alexa+ pages took time because we were looking for an access path that doesn't exist yet for us.
- **What needs work (2):** we didn't find an example payload for the Alexa-specific host context fields (`deviceClass`, `isMobile`, `maxWidth`) that sit next to the standard MCP Apps ones, so our simulated host had to guess their exact shape; the Layout and Rendering page asks you to check buttons against a minimum touch target but doesn't state the size.
- **Feature requests:** (critical) a public reference client or simulator for MCP-based Alexa+ integrations, even without device access; (important) documented guidance on images and structured content in tool results on Echo Show; (important) published expectations for session and state handling across conversations.
- **Would we build with it again:** yes. We'd ship this to Echo Show users the day the tools open up.

## MCP Python SDK (`mcp` 2.1.1, server and client)
- **Used for:** the Streamable HTTP server (`MCPServer`, `streamable_http_app`, `custom_route`, `TransportSecuritySettings`), returning `CallToolResult` with text + images + `structured_content`; a Python test client.
- **What worked well:** the initialize handshake defaults to 2025-11-25; image + structured results in one tool response worked first time; DNS-rebinding protection is one settings object.
- **What needs work:** 2.x renames broke code we copied from older examples: `FastMCP` is now `MCPServer` (good error message), and the client helper `streamablehttp_client` is now `streamable_http_client` (plain ImportError, no deprecation alias).
- **Onboarding:** about 20 minutes from install to a tool call over Streamable HTTP. The built-in MCP Apps extension (`MCPServer(extensions=[Apps()])`, `add_html_resource`) made serving the `ui://` view a few lines.
- **Would we build with it again:** yes.

## MCP Apps (`io.modelcontextprotocol/ui`, spec 2026-01-26)
- **Used for:** the add-on screen: the server registers `ui://second-look/cook.html` (`text/html;profile=mcp-app`, CSP in `_meta.ui`), the session tools point at it with `_meta.ui.resourceUri`, and our simulated host mounts it in a sandboxed iframe and talks to it over postMessage (`ui/initialize`, tool-input, tool-result, `tools/call`).
- **What worked well:** the screen travels with the server instead of living in each client, and the same tool results drive both the voice reply and the view.
- **What needs work:** we could only test the view in our own host; a reference host or test harness would help catch differences between hosts. Autoplay inside an opaque-origin iframe needs the host's `allow="autoplay"`, which is easy to miss.
- **Onboarding:** the spec plus Amazon's Layout and Rendering page were enough to build the view within the hackathon evening; the SDK's Apps extension made the server side a few lines.
- **Would we build with it again:** yes; it is how an Alexa+ add-on ships its screen.

## Strands Agents SDK (`strands-agents` 1.62.0) — AWS Builder
- **Used for:** the conversational agent: `MCPClient(url=...)` over Streamable HTTP, `Agent` with an `AfterToolCallEvent` hook, `SlidingWindowConversationManager`, OpenAI and Gemini model providers.
- **What worked well:** `MCPClient(url=...)` plus `list_tools_sync()` gave a working tool-calling agent against our server in minutes; the hook system made it easy to mirror every tool result onto the screen.
- **What needs work:**
  - Strands pins `mcp<2.2`, while the current MCP SDK is 2.3, so one environment can't use the newest SDK.
  - Strands' MCP client negotiated the newest protocol (2026-07-28) with our server, while Alexa+ integrations target 2025-11-25. We didn't find an option to pin the protocol version on `MCPClient`. Our server supports both, so it worked, but a pin would help test Alexa+ compatibility.
  - With the OpenAI provider, every image in a tool result logs "Moving image from tool message to a new user message for OpenAI compatibility" on every turn, once per image in history; the log noise grows with the conversation.
- **Onboarding:** minutes from `MCPClient(url=...)` to an agent calling our tools; the hooks documentation was the page we needed most.
- **Feature requests:** (important) a `protocol_version` option on `MCPClient`; (nice-to-have) quieter image-relocation logging.
- **Would we build with it again:** yes; it was the fastest path to a tool-calling agent against an MCP server.

## Gemini API (guide compiler, optional agent model, demo voices)
- **Used for:** writing the step-by-step guide from ~90 keyframes + a transcript with a JSON response schema; the demo video's voices (two-speaker for the cook and Alexa; `gemini-3.8-flash-lite-tts` after the free daily quota of `gemini-3.8-flash-tts` ran out).
- **What worked well:** structured output with a Pydantic schema; good grounding when given frames and timestamped lines together. Text-to-speech sounds far more natural than what we used before, and one two-speaker request voices a whole exchange.
- **What needs work:** on the deadline evening `gemini-flash-latest` and `gemini-3.7-flash` returned 503 "high demand" for long stretches; `gemini-pro-latest` returned 429 quota. We added retries and fallbacks across models and to OpenAI vision. `gemini-3.8-flash-tts` reads a style instruction placed in the text aloud, rejects `systemInstruction` ("Developer instruction is not enabled for this model"), wants `speechMetadata.speaker` on every part for two speakers, and returns no word timings, so we cut lines apart with Whisper. On the free tier each TTS model allows 10 requests a day, and we hit that limit mid-production.
- **Onboarding:** quick; structured output worked on the first try, and the TTS voices could be auditioned with a single request.
- **Would we build with it again:** yes, for the guide compiler and for voices; with a paid tier for anything beyond a demo.

## OpenAI API
- **Used for:** the agent's model through Strands (`gpt-4.1-mini`), the guide compiler fallback (`gpt-4.1` vision), and placeholder voices that pace the demo recording (`gpt-4o-mini-tts`).
- **What worked well:** reliable tool calling and low latency (about 3–5 s per spoken turn including MCP calls).
- **What needs work:** `gpt-4o-mini-tts` occasionally returned audio that stops mid-answer (a 32-word answer came back as 6.9 s and lost its last sentence, the timestamp). The demo script now checks words per second and re-synthesizes. `gpt-4.1-mini` also tends to read a whole step card aloud even when told to keep it to one sentence, so the MCP server now returns a short `speak` line and simple intents skip the model.
- **Onboarding:** quick; the existing SDK worked as a Strands provider with one setting.
- **Would we build with it again:** yes for the agent model and Whisper word timings; for voices we preferred Gemini.

## crv / claude-real-video 0.10.7 (our own open-source tool)
- **Used for:** watching the video: scene-change keyframes, per-frame timestamps, Whisper transcript.
- **What worked well:** one call turns a link into frames plus a timestamped transcript, which is exactly the evidence the guide and the answers point at.
- **Onboarding:** we wrote it; the only snag on a fresh install was the PyAV issue below.
- **Would we build with it again:** yes; it is the reason Second Look can cite a moment instead of a range.
- **Friction we found in our own tool:** on a fresh install with PyAV 19, faster-whisper's audio loading fails (`open() got an unexpected keyword argument 'metadata_errors'`), so transcription silently fell back to "none". Pinning `av<16` fixes it; we pinned it in Second Look and sent the fix upstream (see Open Source).

## Web Speech API (Chrome)
- **Used for:** voice input (SpeechRecognition, including the hands-free wake word) and Alexa's voice (speechSynthesis) on the simulated screen.
- **What worked well:** zero setup in Chrome; continuous recognition with interim results made the wake word possible.
- **What needs work:** recognition and synthesis fight each other unless you stop listening while speaking; we handle that explicitly. In practice recognition only works in Chrome.
- **Onboarding:** minutes for push-to-talk; the hands-free loop (pausing while speaking and while a clip plays) took most of the effort.
- **Would we build with it again:** for a simulation, yes; on a device, Alexa's own speech stack replaces it.

## Supporting tools
| Tool | Used for | What worked well | What needs work | Onboarding | Again? |
|---|---|---|---|---|---|
| ffmpeg | Exact frames at a second, clip audio, the demo video | One binary does all of it; `-ss` before `-i` makes frame grabs instant | Filter-graph errors are hard to read | Long familiar | Yes |
| SQLite | Guides, sessions with a revision check, timer deadlines | Zero setup; a restart keeps everything | One writer at a time; fine for one kitchen, not for a shared server | Minutes | Yes, for a single host |
| Starlette / uvicorn | The MCP endpoint, media routes, the host page | The MCP SDK mounts into it directly; CORS and routes are a few lines | Shutdown during a streaming request can wait for the client | Minutes | Yes |
| Playwright | The hands-free and New video browser checks, the demo recording | Frame locators reach into the sandboxed view; video recording is built in | Recording waits must be tuned by hand | Quick; the sandboxed iframe took the most care | Yes |

## Friction log

| # | Task | Steps | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| 1 | Connect a real Alexa+ surface to our MCP server | Read Alexa+ Add-on docs, looked for MCP Toolkit / Web Simulator access | A path to test on Alexa+ | Tools are select-partners only (confirmed by the hackathon FAQ) | High | Built a web simulation that is a real MCP client | Publish a reference client/simulator for MCP integrations |
| 2 | Test Alexa+'s protocol version with the agent | Connected Strands `MCPClient` to our server, watched the MCP trace | Negotiate 2025-11-25 like Alexa+ | Negotiated 2026-07-28; no pin option found | Medium | Server accepts both; browser client pins 2025-11-25 | Add `protocol_version` to `MCPClient` |
| 3 | Run newest MCP SDK next to Strands | `pip install strands-agents mcp` | Latest of both | Strands requires `mcp<2.2` (2.3 is current) | Medium | Pinned `mcp>=2.1,<2.2` | Widen the pin |
| 4 | Port an MCP client example to SDK 2.x | Imported `streamablehttp_client` | Works or a deprecation warning | ImportError | Low | Renamed to `streamable_http_client` | Keep a deprecated alias |
| 5 | Build a guide during peak hours | Called Gemini flash models with ~90 images | A response | 503 high demand, repeatedly | Medium | Retry + model and provider fallback | Clearer capacity signals / retry-after |
| 6 | Give the demo's Alexa voice a warmer tone with Gemini TTS | Put a style instruction before the text ("say warmly and briskly: ..."), then tried `systemInstruction` | The instruction shapes the delivery | `gemini-3.8-flash-tts` read the instruction aloud; `systemInstruction` returned 400 "Developer instruction is not enabled for this model" | Low | Chose expressive prebuilt voices and sent only the words | Support style prompts (or a separate field) on the newest TTS models, as the earlier ones do |
| 7 | Voice a two-person exchange in one request | Sent "Cook: ... / Alexa: ..." text with `multiSpeakerVoiceConfig` | Two speakers, as with earlier TTS models | 400 "Multi-speaker generation requests must specify speech_metadata.speaker for each text part" | Low | One text part per line, each with `speechMetadata.speaker` | Keep accepting the earlier format, or show the new one in the TTS guide |
| 8 | Re-voice the final demo on the free tier | Generated samples, then the takes | Enough requests for one evening | 429 with "limit: 10" free-tier requests per model per day, in the middle of production | Medium | Switched to `gemini-3.8-flash-lite-tts`, batched each scene into one request and cut lines with Whisper word timings | Show the remaining daily quota, and return word timings so one request can be split into lines |
| 9 | Play a replay clip inside the MCP Apps view | View in a `sandbox="allow-scripts"` iframe (opaque origin) calls `video.play()` | The clip plays after the person asked for it | `play()` rejected until the host iframe had `allow="autoplay"` | Medium | Host sets `allow="autoplay"`; the view falls back to visible controls | Mention autoplay delegation in the MCP Apps and Alexa+ add-on rendering docs |
