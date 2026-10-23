"""Second Look web app: serves the simulated Alexa+ screen and runs the Strands agent.

    python -m app.main  (then open http://127.0.0.1:8770)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .agent import MCP_URL, Brain

logging.getLogger("strands.models.openai").setLevel(logging.ERROR)

WEB = Path(__file__).resolve().parent.parent / "web"
_brain: Brain | None = None


def brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain()
    return _brain


async def index(request: Request):
    return FileResponse(WEB / "index.html")


async def config(request: Request):
    base = MCP_URL.rsplit("/mcp", 1)[0]
    return JSONResponse({"mcp_url": MCP_URL, "mcp_base": base, "provider": os.environ.get("SECOND_LOOK_PROVIDER", "gemini")})


async def turn(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    convo = (body.get("convo_id") or "default")[:64]
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)
    try:
        out = await run_in_threadpool(brain().turn, convo, text)
    except Exception as e:  # noqa: BLE001 - shown on screen instead of a blank failure
        return JSONResponse({"reply": "Sorry, I couldn't reach my brain just now. Try again in a moment.", "events": [], "error": str(e)[:300]})
    return JSONResponse(out)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/config", config),
        Route("/api/turn", turn, methods=["POST"]),
        Mount("/web", StaticFiles(directory=WEB), name="web"),
    ]
)


def main() -> None:
    port = int(os.environ.get("SECOND_LOOK_PORT", "8770"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
