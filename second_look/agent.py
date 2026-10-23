"""The conversational brain of the simulated Alexa+ device.

A Strands Agent (AWS's open-source agent SDK) holds the conversation and calls
the crv-guide MCP server over Streamable HTTP for everything it knows about the
video. Every tool result is also forwarded to the screen as a UI event, the way
an Echo Show renders cards next to what Alexa says.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from strands import Agent
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.tools.mcp import MCPClient

MCP_URL = os.environ.get("CRV_GUIDE_MCP_URL", "http://127.0.0.1:8765/mcp")

SYSTEM_PROMPT = """You are Alexa+, running on a kitchen screen, using the "Second Look" skill to help someone cook along with a video.
The person's hands are busy, so talk like a calm friend standing next to them:
- Speak at most two short sentences. No lists, no markdown, no emoji. Numbers as words a person would say ("three quarters of a cup").
- Everything you know about the video comes from the crv-guide tools. Never invent a quantity, time, temperature or technique.
- Questions about the video: call ask_source, answer only from what it returns, and ALWAYS end with when in the video it happens, read naturally ("that's at two oh six in the video"). If it returns nothing relevant, say the video doesn't say, and offer what it does show.
- "What should it look like", "show me", "is this right", "what does ... look like": call show_moment (default: the current step; pass at_seconds when an earlier answer named a time) and describe what is visible in the returned frame in one sentence, including details the narration never said (hand position, thickness, texture).
- Move steps only when asked ("next", "go back", "repeat", "skip to the sauce"): call move_step and read the new step's instruction.
- When a step has a stated duration, offer a timer once; set it with set_timer using that duration and a short label.
- "Where was I", "I'm back", or a new conversation: call get_session with no session_id and tell them the step they're on and any timer status.
- If no guide is loaded yet, call list_guides; if one exists, start_session with it.
- You can see the images tools return; use them when the person asks how something looks."""


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
            params={"temperature": 0.3},
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

    def turn(self, convo_id: str, text: str) -> dict[str, Any]:
        agent, ui = self._agent(convo_id)
        ui.events = []
        result = agent(text)
        reply = str(result).strip()
        return {"reply": reply, "events": list(ui.events)}

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Direct tool call (no model) for screen refreshes."""
        r = self.mcp.call_tool_sync(tool_use_id=f"ui-{tool}", name=tool, arguments=args)
        out = r.get("structuredContent") if isinstance(r, dict) else None
        return out or {}
