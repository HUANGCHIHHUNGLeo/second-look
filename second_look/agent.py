"""The conversational brain of the simulated Alexa+ device.

A Strands Agent (AWS's open-source agent SDK) holds the conversation and calls
the crv-guide MCP server over Streamable HTTP for everything it knows about the
video. Every tool result is also forwarded to the screen as a UI event, the way
an Echo Show renders cards next to what Alexa says.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any

from strands import Agent
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.mcp import MCPClient

MCP_URL = os.environ.get("CRV_GUIDE_MCP_URL", "http://127.0.0.1:8765/mcp")

SYSTEM_PROMPT = """You are Alexa+, running on a kitchen screen, using the "Second Look" skill to help someone cook along with a video.
The person's hands are busy, so talk like a calm friend standing next to them:
- Speak one or two short sentences, under 35 words in total. No lists, no markdown, no emoji. Quantities as words a person would say ("three quarters of a cup"); times in the video as m:ss ("at 1:24").
- Don't close with "let me know when you're ready" or a question; offer the next step or a timer only when it is useful.
- Good answers sound like: "About a teaspoon of salt in the water. She says it at 2:06." or "Step 5. Cut the log into small pieces and roll each into a ball."
- Everything you know about the video comes from the crv-guide tools. Never invent a quantity, time, temperature, ingredient or technique, and never read a step instruction that did not come from a tool result in this turn.
- Each message starts with what the screen shows right now (session_id, current step, outline). Use that session_id in tool calls.
- Questions about the video: call ask_source, answer only from what it returns, and ALWAYS end with the single time of the line that answers it ("she says it at 2:06"), never a time range. If it returns nothing relevant, say the video doesn't say, and offer what it does show.
- "Show me how she does X" (a technique at a moment): call show_moment directly with query set to a short description of the technique (e.g. "Show me how she rolls the log" -> show_moment(query="rolling the dough into a log")); don't call ask_source first. Then say in one sentence what the frame shows and when it is in the video.
- Tool results for moving steps, resuming and timers include a "Suggested spoken reply". When that tool call was the answer to what the person asked, say exactly that reply and nothing else.
- "What should it look like", "show me", "is this right", "what does ... look like": call show_moment (default: the current step; pass at_seconds when an earlier answer named a time) and describe what is visible in the returned frame in one sentence, including details the narration never said (hand position, thickness, texture).
- Move steps only when asked, and ALWAYS by calling move_step first: "next" = action next; "go back" = back; "repeat" = repeat; "skip to the sauce" / "go to shaping" = goto with the step number from the outline. Then say "Step N." and read the Instruction from the tool result, and stop: no description of the picture, no "next is ...".
- When a step has a stated duration, offer a timer once; set it with set_timer using that duration and a short label.
- "Where was I", "I'm back", or a new conversation: call get_session with no session_id and say, in one sentence, the step number and title and any timer's time left. Don't read the instruction again unless asked.
- If no guide is loaded yet, call list_guides; if one exists, start_session with it.
- You can see the images tools return; only describe them when the person asks how something looks or whether theirs is right.
- When asked to set a timer for a step ("the rest", "the boil"), use that step's stated duration from the session (wait_s); if you're not on that step, use the step from the outline."""


class UiEvents(HookProvider):
    """Collects tool calls and their results for the screen."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _after_tool(self, event: AfterToolCallEvent) -> None:
        name = event.tool_use.get("name")
        structured = None
        texts = []
        images = 0
        for block in (event.result or {}).get("content", []) or []:
            if "json" in block:
                structured = block["json"]
            elif "text" in block:
                texts.append(block["text"])
                if structured is None:
                    try:
                        maybe = json.loads(block["text"])
                        if isinstance(maybe, dict):
                            structured = maybe
                    except ValueError:
                        pass
            elif "image" in block:
                images += 1
        sc = (event.result or {}).get("structuredContent")
        if isinstance(sc, dict):
            structured = sc
        self.events.append(
            {
                "type": "tool",
                "tool": name,
                "input": event.tool_use.get("input"),
                "status": (event.result or {}).get("status"),
                "data": structured,
                "text": "\n".join(texts)[:1500],
                "images": images,
            }
        )


def make_model():
    provider = os.environ.get("SECOND_LOOK_PROVIDER", "gemini").lower()
    if provider == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={"api_key": os.environ["OPENAI_API_KEY"]},
            model_id=os.environ.get("SECOND_LOOK_MODEL", "gpt-4.1-mini"),
            params={"temperature": 0.2},
        )
    from strands.models.gemini import GeminiModel

    return GeminiModel(
        client_args={"api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")},
        model_id=os.environ.get("SECOND_LOOK_MODEL", "gemini-flash-latest"),
        params={"temperature": 0.3},
    )


class Brain:
    """One MCP connection shared by all turns; one Strands agent per browser conversation."""

    def __init__(self) -> None:
        self.mcp = MCPClient(url=MCP_URL, application_name="second-look-agent", application_version="0.1.0")
        self.mcp.start()
        self.tools = self.mcp.list_tools_sync()
        self._agents: dict[str, tuple[Agent, UiEvents]] = {}
        self._lock = threading.Lock()

    def _agent(self, convo_id: str) -> tuple[Agent, UiEvents]:
        with self._lock:
            if convo_id not in self._agents:
                ui = UiEvents()
                agent = Agent(
                    model=make_model(),
                    tools=self.tools,
                    system_prompt=SYSTEM_PROMPT,
                    hooks=[ui],
                    callback_handler=None,
                    # keep the recent turns only: every tool result can carry a video frame
                    conversation_manager=SlidingWindowConversationManager(window_size=16),
                )
                self._agents[convo_id] = (agent, ui)
            return self._agents[convo_id]

    def screen_context(self) -> str:
        """What the screen shows right now, so the model always knows the session and the step numbers."""
        try:
            snap = self.call("get_session", {})
        except Exception:  # noqa: BLE001
            snap = {}
        if not snap.get("session_id"):
            return "(Screen: no cooking session yet. Use list_guides / start_session.)"
        st = snap["step"]
        timers = "; ".join(f"{t['label']} {t['status']} {t['remaining_s']}s left" for t in snap.get("timers", [])) or "none"
        return (
            f"(Screen right now, from the crv-guide server: session_id={snap['session_id']}, "
            f"'{snap['guide_title']}', on step {st['step_number']} of {st['total_steps']}: {st['title']} "
            f"(stated duration {st.get('wait_s') or 'none'} s). Timers: {timers}. "
            f"Outline: {' | '.join(snap.get('outline', []))})"
        )

    NAV = {
        "next": re.compile(r"^(ok(ay)?[, ]*)?(next|next step|go on|continue|what'?s next|done[, ]*next)[.!?]*$", re.I),
        "back": re.compile(r"^(go back|back|previous( step)?|last step)[.!?]*$", re.I),
        "repeat": re.compile(r"^(repeat( that| the step)?|say (that|it) again|again)[.!?]*$", re.I),
        "resume": re.compile(r"^((ok(ay)?|hi|hey)[, ]*)?(i'?m back[.!,]*\s*)?(where was i|where am i|where were we)[.!?]*$|^i'?m back[.!?]*$", re.I),
    }

    def fast_path(self, text: str) -> dict[str, Any] | None:
        """Plain step navigation is routed straight to the MCP server, the way a voice platform maps simple intents."""
        t = text.strip()
        action = next((a for a, rx in self.NAV.items() if rx.match(t)), None)
        if not action:
            return None
        snap = self.call("get_session", {})
        if not snap.get("session_id"):
            return None
        if action == "resume":
            return {"reply": snap.get("speak", ""), "events": [{"type": "tool", "tool": "get_session", "input": {}, "status": "success", "data": snap, "text": "", "images": 1}], "route": "intent"}
        args = {"session_id": snap["session_id"], "action": action, "expected_revision": snap["revision"]}
        r = self.mcp.call_tool_sync(tool_use_id="nav", name="move_step", arguments=args)
        data = r.get("structuredContent") or {}
        st = data.get("step") or {}
        if not st:
            return None
        reply = data.get("speak") or f"Step {st['step_number']}. {st['say']}"
        return {"reply": reply, "events": [{"type": "tool", "tool": "move_step", "input": args, "status": "success", "data": data, "text": "", "images": 1}], "route": "intent"}

    def turn(self, convo_id: str, text: str) -> dict[str, Any]:
        fast = self.fast_path(text)
        if fast:
            return fast
        agent, ui = self._agent(convo_id)
        ui.events = []
        result = agent(f"{self.screen_context()}\nThe person says: {text}")
        reply = str(result).strip()
        return {"reply": reply, "events": list(ui.events)}

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Direct tool call (no model) for screen refreshes."""
        r = self.mcp.call_tool_sync(tool_use_id=f"ui-{tool}", name=tool, arguments=args)
        out = r.get("structuredContent") if isinstance(r, dict) else None
        return out or {}
