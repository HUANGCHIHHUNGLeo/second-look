# Product feedback (from building Second Look, Oct 23 2026)

Everything below is what we actually ran into while building this project, with versions.

## Alexa+ (Agent Skills / MCP integration)
- **Used for:** the target platform. We built a self-hosted MCP server (spec 2025-11-25, Streamable HTTP) meant to be an Alexa+ integration.
- **What worked well:** choosing MCP as the integration standard. It let us build and test the whole skill with ordinary MCP tooling and two different clients.
- **What needs work:** the Add-on developer tools (Category SDK, MCP Toolkit, CLI, Web Simulator) are select-partners only, and some setup-guide pages don't say so (the hackathon FAQ clarified it). We could not see how Alexa+ renders image content from a tool result, or how it treats `structuredContent`, so we designed the screen from public Echo Show conventions.
- **Onboarding:** reading the Alexa+ pages took time because we were looking for an access path that doesn't exist yet for us.
- **Feature requests:** (critical) a public reference client or simulator for MCP-based Alexa+ integrations, even without device access; (important) documented guidance on images and structured content in tool results on Echo Show; (important) published expectations for session and state handling across conversations.
- **Would we build with it again:** yes. We'd ship this to Echo Show users the day the tools open up.

## MCP Python SDK (`mcp` 2.1.1, server and client)
- **Used for:** the Streamable HTTP server (`MCPServer`, `streamable_http_app`, `custom_route`, `TransportSecuritySettings`), returning `CallToolResult` with text + images + `structured_content`; a Python test client.
- **What worked well:** the initialize handshake defaults to 2025-11-25; image + structured results in one tool response worked first time; DNS-rebinding protection is one settings object.
- **What needs work:** 2.x renames broke code we copied from older examples: `FastMCP` is now `MCPServer` (good error message), and the client helper `streamablehttp_client` is now `streamable_http_client` (plain ImportError, no deprecation alias).
- **Onboarding:** about 20 minutes from install to a tool call over Streamable HTTP.
- **Would we build with it again:** yes.

## Strands Agents SDK (`strands-agents` 1.62.0) — AWS Builder
- **Used for:** the conversational agent: `MCPClient(url=...)` over Streamable HTTP, `Agent` with an `AfterToolCallEvent` hook, `SlidingWindowConversationManager`, OpenAI and Gemini model providers.
- **What worked well:** `MCPClient(url=...)` plus `list_tools_sync()` gave a working tool-calling agent against our server in minutes; the hook system made it easy to mirror every tool result onto the screen.
- **What needs work:**
  - Strands pins `mcp<2.2`, while the current MCP SDK is 2.3, so one environment can't use the newest SDK.
  - Strands' MCP client negotiated the newest protocol (2026-07-28) with our server, while Alexa+ integrations target 2025-11-25. We didn't find an option to pin the protocol version on `MCPClient`. Our server supports both, so it worked, but a pin would help test Alexa+ compatibility.
  - With the OpenAI provider, every image in a tool result logs "Moving image from tool message to a new user message for OpenAI compatibility" on every turn, once per image in history; the log noise grows with the conversation.
- **Feature requests:** (important) a `protocol_version` option on `MCPClient`; (nice-to-have) quieter image-relocation logging.
- **Would we build with it again:** yes; it was the fastest path to a tool-calling agent against an MCP server.

## Gemini API (guide compiler and optional agent model)
- **Used for:** writing the step-by-step guide from ~90 keyframes + a transcript with a JSON response schema.
- **What worked well:** structured output with a Pydantic schema; good grounding when given frames and timestamped lines together.
- **What needs work:** on the deadline evening `gemini-flash-latest` and `gemini-3.7-flash` returned 503 "high demand" for long stretches; `gemini-pro-latest` returned 429 quota. We added retries and fallbacks across models and to OpenAI vision.

## OpenAI API
- **Used for:** the agent's model through Strands (`gpt-4.1-mini`), the guide compiler fallback (`gpt-4.1` vision), and the demo video's voices (`gpt-4o-mini-tts`).
- **What worked well:** reliable tool calling and low latency (about 3–5 s per spoken turn including MCP calls).
- **What needs work:** `gpt-4o-mini-tts` occasionally returned audio that stops mid-answer (a 32-word answer came back as 6.9 s and lost its last sentence, the timestamp). The demo script now checks words per second and re-synthesizes. `gpt-4.1-mini` also tends to read a whole step card aloud even when told to keep it to one sentence, so the MCP server now returns a short `speak` line and simple intents skip the model.

## crv / claude-real-video 0.10.7 (our own open-source tool)
- **Used for:** watching the video: scene-change keyframes, per-frame timestamps, Whisper transcript.
- **Friction we found in our own tool:** on a fresh install with PyAV 19, faster-whisper's audio loading fails (`open() got an unexpected keyword argument 'metadata_errors'`), so transcription silently fell back to "none". Pinning `av<16` fixes it; we pinned it in Second Look and sent the fix upstream (see Open Source).

## Web Speech API (Chrome)
- **Used for:** voice input (SpeechRecognition) and Alexa's voice (speechSynthesis) on the simulated screen.
- **What needs work:** recognition and synthesis fight each other unless you stop listening while speaking; we handle that explicitly.

## Friction log

| # | Task | Steps | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| 1 | Connect a real Alexa+ surface to our MCP server | Read Alexa+ Add-on docs, looked for MCP Toolkit / Web Simulator access | A path to test on Alexa+ | Tools are select-partners only (confirmed by the hackathon FAQ) | High | Built a web simulation that is a real MCP client | Publish a reference client/simulator for MCP integrations |
| 2 | Test Alexa+'s protocol version with the agent | Connected Strands `MCPClient` to our server, watched the MCP trace | Negotiate 2025-11-25 like Alexa+ | Negotiated 2026-07-28; no pin option found | Medium | Server accepts both; browser client pins 2025-11-25 | Add `protocol_version` to `MCPClient` |
| 3 | Run newest MCP SDK next to Strands | `pip install strands-agents mcp` | Latest of both | Strands requires `mcp<2.2` (2.3 is current) | Medium | Pinned `mcp>=2.1,<2.2` | Widen the pin |
| 4 | Port an MCP client example to SDK 2.x | Imported `streamablehttp_client` | Works or a deprecation warning | ImportError | Low | Renamed to `streamable_http_client` | Keep a deprecated alias |
| 5 | Build a guide during peak hours | Called Gemini flash models with ~90 images | A response | 503 high demand, repeatedly | Medium | Retry + model and provider fallback | Clearer capacity signals / retry-after |
