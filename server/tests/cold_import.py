"""Cold path: import a video the server has never seen, by URL, and time each stage."""
import asyncio, sys, time
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://127.0.0.1:8765/mcp"
SRC = sys.argv[1]

async def main():
    async with streamable_http_client(URL) as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            t0 = time.time()
            res = await s.call_tool("import_video", {"source": SRC})
            job = res.structured_content["job_id"]
            last = None
            while True:
                res = await s.call_tool("get_import", {"job_id": job})
                sc = res.structured_content
                if sc["stage"] != last:
                    print(f"{time.time()-t0:6.1f}s  {sc['status']}: {sc['stage']}", flush=True); last = sc["stage"]
                if sc["status"] != "processing":
                    break
                await asyncio.sleep(2)
            print(res.content[0].text)
            if sc["status"] == "ready":
                g = sc["guide"]
                for st in g["steps"]:
                    print(" ", st["step_number"], st["title"], st["wait_s"])

asyncio.run(main())
