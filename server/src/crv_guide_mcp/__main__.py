"""Run the server:  crv-guide-mcp [--host 127.0.0.1] [--port 8765]"""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(prog="crv-guide-mcp", description="Streamable HTTP MCP server for following how-to videos.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    import uvicorn

    from .server import build_app

    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
