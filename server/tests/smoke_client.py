"""End-to-end smoke test against a running server: real MCP client over Streamable HTTP."""
import asyncio, json, sys, time
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"

def show(r):
    texts = [c.text for c in r.content if getattr(c, "type", "") == "text"]
    imgs = [c for c in r.content if getattr(c, "type", "") == "image"]
    return texts[0][:600] + (f"\n  [+{len(imgs)} image(s), {sum(len(i.data) for i in imgs)//1024} KB b64]" if imgs else "")

async def main():
    async with streamable_http_client(URL) as (read, write, *_):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print("negotiated protocol:", init.protocol_version, "| server:", init.server_info.name)
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            r = await s.call_tool("import_video", {"source": "sample:pasta"})
            job = r.structured_content["job_id"]; print(show(r))
            for _ in range(60):
                r = await s.call_tool("get_import", {"job_id": job})
                if r.structured_content["status"] != "processing": break
                await asyncio.sleep(1)
            print(show(r))
            gid = r.structured_content["guide"]["guide_id"]
            r = await s.call_tool("start_session", {"guide_id": gid}); print(show(r))
            sid = r.structured_content["session_id"]
            for a in ["next", "next"]:
                r = await s.call_tool("move_step", {"session_id": sid, "action": a})
            print(show(r))
            r = await s.call_tool("ask_source", {"session_id": sid, "question": "how much water do I add?"}); print(show(r))
            r = await s.call_tool("ask_source", {"session_id": sid, "question": "how long do I knead the dough?"}); print(show(r))
            r = await s.call_tool("show_moment", {"session_id": sid}); print(show(r))
            r = await s.call_tool("set_timer", {"session_id": sid, "seconds": 20, "label": "demo rest", "request_id": "t1"}); print(show(r))
            r = await s.call_tool("get_session", {}); print(show(r))

asyncio.run(main())
