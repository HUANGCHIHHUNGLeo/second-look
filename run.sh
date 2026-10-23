#!/bin/sh
# Starts the crv-guide MCP server (port 8765) and the simulated Alexa+ screen (port 8770).
# Usage: ./run.sh            (reads keys from .env if present)
set -e
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
PY=${PYTHON:-.venv/bin/python}
$PY -m crv_guide_mcp --port 8765 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT INT TERM
sleep 2
echo "MCP server:  http://127.0.0.1:8765/mcp"
echo "Open:        http://127.0.0.1:8770"
$PY -m second_look.main
