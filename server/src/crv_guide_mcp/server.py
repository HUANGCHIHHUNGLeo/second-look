"""crv-guide MCP server (Streamable HTTP, MCP spec 2025-11-25).

Gives an assistant such as Alexa+ the ability to follow a how-to video with a
person: import a video (crv watches it), walk through it one step at a time,
answer questions from what was said AND shown, show the exact moment a step
looks like, and keep timers. Progress lives in SQLite, so it survives closed
apps, new connections and server restarts.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import checks, config, evidence, extract, guide as guide_mod, store

INSTRUCTIONS = """Use these tools to cook (or build, or fix) along with a video, hands-free.
1. import_video, then poll get_import until it is ready. 2. start_session (or get_session to resume).
Move only when the person asks (move_step). Questions never move the step.
For questions about the video call ask_source and answer ONLY from the returned passages and images,
mentioning the video time (for example "at 2:14"). If ask_source finds nothing relevant, say the video does not say.
When the person asks what something should look like, call show_moment and describe the returned frame.
When a step has a duration (wait_s) offer a timer; set it with set_timer."""

# MCP Apps (io.modelcontextprotocol/ui): the session tools point at one self-contained HTML view.
# Alexa+ renders an add-on's UI this way (a ui:// resource in a sandboxed iframe, talking to the
# host over postMessage); any other MCP Apps host can show it too, and text-only clients ignore it.
UI_URI = "ui://second-look/cook.html"
UI_META = {"ui": {"resourceUri": UI_URI}}
apps = Apps()
apps.add_html_resource(
    UI_URI,
    (Path(__file__).parent / "ui" / "cook.html").read_text(encoding="utf-8"),
    name="second-look-view",
    title="Second Look",
    description="The cook-along screen: current step, its frame from the video, evidence cards and the moment to replay.",
    csp=ResourceCsp(resource_domains=[config.PUBLIC_BASE_URL, "https://unpkg.com"]),
    prefers_border=False,
)

mcp = MCPServer(
    name="crv-guide",
    title="crv-guide: follow any how-to video",
    version="0.2.0",
    instructions=INSTRUCTIONS,
    website_url="https://github.com/HUANGCHIHHUNGLeo/second-look",
    extensions=[apps],
)

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
_watched_cache: dict[str, extract.Watched] = {}
_passages_cache: dict[str, list[evidence.Passage]] = {}


# ---------------------------------------------------------------- helpers

def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _result(data: dict[str, Any], text: str, images: list[Path] | None = None, is_error: bool = False) -> CallToolResult:
    content: list[Any] = [TextContent(type="text", text=text)]
    for img in images or []:
        try:
            content.append(ImageContent(type="image", data=_b64(img), mime_type="image/jpeg"))
        except OSError:
            pass
    return CallToolResult(content=content, structured_content=data, is_error=is_error)


def _b64(path: Path) -> str:
    import base64

    return base64.b64encode(Path(path).read_bytes()).decode()


def _error(message: str) -> CallToolResult:
    return _result({"error": message}, message, is_error=True)


def _watched(g: dict[str, Any]) -> extract.Watched:
    gid = g["guide_id"]
    if gid not in _watched_cache:
        _watched_cache[gid] = extract.load(Path(g["folder"]))
    return _watched_cache[gid]


def _passages(g: dict[str, Any]) -> list[evidence.Passage]:
    gid = g["guide_id"]
    if gid not in _passages_cache:
        _passages_cache[gid] = evidence.build_passages(_watched(g), g)
    return _passages_cache[gid]


def _media(gid: str, kind: str, arg: str = "") -> str:
    return f"{config.PUBLIC_BASE_URL}/media/{gid}/{kind}{('/' + arg) if arg else ''}"


def _step_card(g: dict[str, Any], idx: int) -> dict[str, Any]:
    st = g["steps"][idx]
    return {
        "step_number": st["step_number"],
        "total_steps": len(g["steps"]),
        "title": st["title"],
        "say": st["say"],
        "look_for": st["look_for"],
        "visual_note": st.get("visual_note"),
        "unverified": st.get("unverified") or [],
        "wait_s": st.get("wait_s"),
        "video_time": f"{extract.clock(st['start_s'])}-{extract.clock(st['end_s'])}",
        "frame_time": extract.clock(st["frame_t"]),
        "frame_url": _media(g["guide_id"], "frame", str(st["frame"])),
        "clip": {"start_s": st["start_s"], "end_s": st["end_s"], "video_url": _media(g["guide_id"], "video")},
    }


def _snapshot(sess: dict[str, Any], g: dict[str, Any]) -> dict[str, Any]:
    idx = min(sess["step_index"], len(g["steps"]) - 1)
    nxt = g["steps"][idx + 1]["title"] if idx + 1 < len(g["steps"]) else None
    return {
        "session_id": sess["id"],
        "guide_id": g["guide_id"],
        "guide_title": g["title"],
        "status": sess["status"],
        "revision": sess["revision"],
        "step": _step_card(g, idx),
        "next_step_title": nxt,
        "completed_steps": [i + 1 for i in sess["completed"]],
        "outline": [f"{x['step_number']}. {x['title']}" for x in g["steps"]],
        "timers": store.timers_for(sess["id"]),
        "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sess["updated_at"])),
    }


def _snapshot_text(s: dict[str, Any]) -> str:
    st = s["step"]
    lines = [
        f"{s['guide_title']} - step {st['step_number']} of {st['total_steps']}: {st['title']}.",
        f"Instruction: {st['say']}",
        f"When done it should look like: {st['look_for']}",
    ]
    if st.get("visual_note"):
        lines.append(f"Visible in the video but not said: {st['visual_note']}")
    if st.get("unverified"):
        lines.append(f"Not in the narration (inferred from the picture or by the guide writer; say so if asked): {', '.join(st['unverified'])}")
    if st.get("wait_s"):
        lines.append(f"This step has a stated duration of {st['wait_s']} seconds (offer a timer).")
    lines.append(f"Shown in the video at {st['video_time']}. Attached image: the video frame at {st['frame_time']}.")
    for t in s["timers"]:
        lines.append(f"Timer '{t['label']}': {t['status']}, {t['remaining_s']} s left.")
    if s["next_step_title"]:
        lines.append(f"Next step: {s['next_step_title']}.")
    else:
        lines.append("This is the last step.")
    lines.append("All steps (use move_step goto with the number to jump): " + "; ".join(s["outline"]))
    return "\n".join(lines)


def _minutes(seconds: float) -> str:
    m = int(seconds // 60)  # a countdown says "29 minutes left" until 29 full minutes remain
    return "less than a minute" if seconds < 60 else f"{m} minute{'s' if m != 1 else ''}"


def _speak(snap: dict[str, Any], resume: bool) -> str:
    """A short voice-ready line, the way a skill hands Alexa its output speech; the screen shows the rest."""
    st = snap["step"]
    if snap["status"] == "finished":
        return "That was the last step. Enjoy!"
    if resume:
        out = f"You're on step {st['step_number']}, {st['title'].lower()}."
        for t in snap["timers"]:
            label = t["label"].lower()
            out += f" Your {label} timer is done." if t["remaining_s"] <= 0 else f" Your {label} timer has {_minutes(t['remaining_s'])} left."
        return out
    out = f"Step {st['step_number']}. {st['say']}"
    if st.get("wait_s") and not snap["timers"]:
        out += f" Want a {_minutes(st['wait_s']).replace(' minutes', '-minute')} timer?"
    return out


def _session_result(sess: dict[str, Any], g: dict[str, Any], prefix: str = "", resume: bool = False) -> CallToolResult:
    snap = _snapshot(sess, g)
    snap["speak"] = _speak(snap, resume)
    frame = _watched(g)
    st = g["steps"][min(sess["step_index"], len(g["steps"]) - 1)]
    img = next((f.path for f in frame.frames if f.index == st["frame"]), None)
    text = (prefix + "\n" if prefix else "") + f"Suggested spoken reply: {snap['speak']}\n" + _snapshot_text(snap)
    return _result(snap, text, [img] if img else [])


def _load(session_id: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    sess = store.get_session(session_id) if session_id else store.latest_active_session()
    if not sess:
        return None, None, "No cooking session found. Use start_session with a guide_id (see list_guides)."
    g = store.get_guide(sess["guide_id"])
    if not g:
        return None, None, "The guide for this session no longer exists."
    return sess, g, None


# ---------------------------------------------------------------- import pipeline

def _resolve_source(source: str) -> tuple[str, Path | None]:
    """Returns (source_key, prepared_folder_or_None)."""
    if source.startswith("sample:"):
        name = source.split(":", 1)[1]
        folder = FIXTURES_DIR / name
        if not (folder / "frames.json").exists():
            raise RuntimeError(f"Unknown sample '{name}'. Available: {[p.name for p in FIXTURES_DIR.iterdir() if p.is_dir()]}")
        return f"sample:{name}", folder
    p = Path(source).expanduser()
    if p.is_dir() and (p / "frames.json").exists():
        return "folder:" + str(p.resolve()), p
    return extract.source_key(source), None


def _run_import(job_id: str, source: str) -> None:
    try:
        key, folder = _resolve_source(source)
        existing = store.guide_by_source_key(key)
        if existing:
            store.update_job(job_id, status="ready", stage="ready", guide_id=existing["id"])
            return
        if folder is None:
            folder = extract.folder_for(key)
            store.update_job(job_id, stage="watching the video with crv")
            extract.run_crv(source, folder)
        watched = extract.load(folder)
        precompiled = folder / "guide.json"
        if precompiled.exists():
            data = checks.check_guide(json.loads(precompiled.read_text()), watched)
        else:
            store.update_job(job_id, stage="writing the step-by-step guide")
            data = guide_mod.compile_guide(watched)
            (folder / "guide.json").write_text(guide_mod.dumps(data))
        gid = store.save_guide(source, key, str(folder), data)
        store.update_job(job_id, status="ready", stage="ready", guide_id=gid)
    except Exception as e:  # noqa: BLE001 - reported to the caller through get_import
        store.update_job(job_id, status="failed", stage="failed", error=str(e)[:600])


def _guide_summary(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "guide_id": g["guide_id"],
        "title": g["title"],
        "summary": g.get("summary"),
        "video_duration": extract.clock(g.get("video_duration_s") or 0),
        "steps": [{"step_number": s["step_number"], "title": s["title"], "wait_s": s.get("wait_s")} for s in g["steps"]],
        "ingredients": g.get("ingredients", []),
        "equipment": g.get("equipment", []),
        "built_from": {"keyframes": g.get("frames"), "transcript_lines": g.get("transcript_lines"), "model": g.get("model")},
    }


# ---------------------------------------------------------------- tools

@mcp.tool(
    title="Import a how-to video",
    annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=True),
)
async def import_video(source: str) -> CallToolResult:
    """Watch a how-to video and build a step-by-step guide from it. `source` is a video URL (YouTube and other sites crv supports), a local file path, or a bundled sample such as "sample:pasta". Returns a job_id right away; poll get_import until status is ready. Re-importing the same video reuses the existing guide."""
    job_id = store.create_job(source)
    asyncio.get_running_loop().run_in_executor(None, _run_import, job_id, source)
    await asyncio.sleep(0.3)
    job = store.get_job(job_id) or {}
    data = {"job_id": job_id, "status": job.get("status"), "stage": job.get("stage"), "guide_id": job.get("guide_id")}
    return _result(data, f"Import started (job {job_id}). Check progress with get_import.")


@mcp.tool(title="Check a video import", annotations=ToolAnnotations(readOnlyHint=True))
async def get_import(job_id: str) -> CallToolResult:
    """Progress of an import_video job: processing (with the current stage), ready (with guide_id and the guide outline) or failed (with the reason)."""
    job = store.get_job(job_id)
    if not job:
        return _error(f"No import job {job_id}.")
    data: dict[str, Any] = {"job_id": job_id, "status": job["status"], "stage": job["stage"], "error": job["error"]}
    text = f"Import {job['status']}: {job['stage']}."
    if job["status"] == "ready":
        g = store.get_guide(job["guide_id"])
        data["guide"] = _guide_summary(g)
        text = f"Ready: '{g['title']}', {len(g['steps'])} steps. Start with start_session(guide_id='{g['guide_id']}')."
    elif job["status"] == "failed":
        text = f"Import failed: {job['error']}"
    return _result(data, text)


@mcp.tool(title="List imported guides", annotations=ToolAnnotations(readOnlyHint=True))
async def list_guides() -> CallToolResult:
    """Guides already built from videos, newest first."""
    guides = store.list_guides()
    text = "\n".join(f"- {g['title']} ({g['steps']} steps) guide_id={g['guide_id']}" for g in guides) or "No guides yet."
    return _result({"guides": guides}, text)


@mcp.tool(title="Start cooking along", annotations=ToolAnnotations(readOnlyHint=False), meta=UI_META)
async def start_session(guide_id: str) -> CallToolResult:
    """Start a new hands-free session for a guide at step 1. Returns the session snapshot and the video frame for the step."""
    g = store.get_guide(guide_id)
    if not g:
        return _error(f"No guide {guide_id}. Use list_guides.")
    sid = store.create_session(guide_id)
    sess = store.get_session(sid)
    return _session_result(sess, g, f"Started session {sid}.")


@mcp.tool(title="Where was I?", annotations=ToolAnnotations(readOnlyHint=True), meta=UI_META)
async def get_session(session_id: str | None = None) -> CallToolResult:
    """Current state of a session: step, what it should look like, timers with time left. Without session_id it resumes the most recent unfinished session (use this when someone comes back and asks where they were)."""
    sess, g, err = _load(session_id)
    if err:
        return _error(err)
    ago = int(time.time() - sess["updated_at"])
    return _session_result(sess, g, f"Resuming session {sess['id']} (last activity {ago // 60} min ago).", resume=True)


@mcp.tool(title="Move to another step", annotations=ToolAnnotations(readOnlyHint=False), meta=UI_META)
async def move_step(
    session_id: str,
    action: Literal["next", "back", "repeat", "goto"],
    step_number: int | None = None,
    expected_revision: int | None = None,
) -> CallToolResult:
    """Change the current step. next marks the current step done; back and goto move without completing; repeat returns the same step again. Pass expected_revision from the last snapshot to avoid double moves when two screens are open."""
    sess, g, err = _load(session_id)
    if err:
        return _error(err)
    n = len(g["steps"])
    idx = sess["step_index"]
    done = list(sess["completed"])
    status = sess["status"]
    if action == "next":
        done.append(idx)
        if idx + 1 >= n:
            status = "finished"
        else:
            idx += 1
    elif action == "back":
        idx = max(0, idx - 1)
    elif action == "goto":
        if not step_number or not 1 <= step_number <= n:
            return _error(f"step_number must be between 1 and {n}.")
        idx = step_number - 1
    if action != "repeat":
        ok = store.update_session(sess["id"], step_index=idx, completed=done, status=status, expected_revision=expected_revision)
        if not ok:
            fresh = store.get_session(sess["id"])
            return _session_result(fresh, g, "Someone else moved first; here is the current step.")
    sess = store.get_session(sess["id"])
    prefix = "All steps are done." if sess["status"] == "finished" else ""
    return _session_result(sess, g, prefix)


@mcp.tool(title="Ask the video", annotations=ToolAnnotations(readOnlyHint=True), meta=UI_META)
async def ask_source(question: str, session_id: str | None = None) -> CallToolResult:
    """Search what the video says and shows for an answer (quantities, times, technique, what something should look like). Returns the best matching transcript passages and steps with video times, plus the matching video frames as images. Answer only from these; if nothing relevant comes back, say the video does not cover it. Never moves the step."""
    sess, g, err = _load(session_id)
    if err:
        return _error(err)
    hits = evidence.search(_passages(g), question, k=4)
    watched = _watched(g)
    results, images, said_times = [], [], set()
    for score, p in hits:
        ln = evidence.best_line(watched.segments, p.lines, question)
        if p.kind == "said" and ln is not None:
            if round(ln.start, 1) in said_times:
                continue  # another window already quotes this line
            said_times.add(round(ln.start, 1))
        item: dict[str, Any] = {
            "kind": "what was said" if p.kind == "said" else f"step {p.step_number} (said + visible in the frame)",
            "video_time": f"{extract.clock(p.start)}-{extract.clock(p.end)}",
            "start_s": round(p.start, 1),
            "text": p.text,
            "score": round(score, 2),
        }
        if ln is not None:
            item["said_at"] = extract.clock(ln.start)
            item["at_s"] = round(ln.start, 1)
            item["quote"] = ln.text
            item["clip"] = {"start_s": round(max(0.0, ln.start - 1.0), 1), "end_s": round(min(watched.duration_s, ln.end + 4.0), 1)}
        else:
            item["clip"] = {"start_s": round(p.start, 1), "end_s": round(min(p.end, p.start + 12.0), 1)}
        if p.kind == "step":
            item["step_number"] = p.step_number
            st = g["steps"][p.step_number - 1]
            fr = next(f for f in watched.frames if f.index == st["frame"])
        else:
            fr = extract.frame_at(watched, (ln.start + 1.0) if ln is not None else (p.start + p.end) / 2)
        item["frame_time"] = extract.clock(fr.t)
        item["frame_url"] = _media(g["guide_id"], "frame", str(fr.index))
        results.append(item)
        if len(images) < 2 and fr.path not in images:
            images.append(fr.path)
    supported = bool(results) and results[0]["score"] >= 1.0
    data = {"question": question, "supported": supported, "evidence": results, "current_step": sess["step_index"] + 1}
    missing = evidence.unmentioned(question, _passages(g))
    if missing:
        data["not_in_video"] = missing
    if not results:
        text = (f"The video never mentions {', '.join(missing)}." if missing else "The video does not seem to cover this (no matching passage).") + " Say so; do not guess."
    else:
        rows = []
        for r in results:
            where = f"step {r['step_number']}, {r['video_time']}" if "step_number" in r else r["video_time"]
            quote = f' says at {r["said_at"]}: "{r["quote"]}"' if "said_at" in r else ""
            rows.append(f"- [{where}]{quote} | {r['text']}")
        if missing:
            rows.append(f"Not mentioned anywhere in the video: {', '.join(missing)}. Say that plainly instead of answering from a different ingredient or step.")
        text = (
            "Evidence from the video. Answer only from this, and name the single time of the line that answers it "
            "(e.g. 'she says it at 1:24'), not a range:\n" + "\n".join(rows) + "\nAttached: the video frames for the top passages."
        )
    return _result(data, text, images)


@mcp.tool(title="Show the moment in the video", annotations=ToolAnnotations(readOnlyHint=True), meta=UI_META)
async def show_moment(
    session_id: str | None = None,
    step_number: int | None = None,
    at_seconds: float | None = None,
    query: str | None = None,
) -> CallToolResult:
    """Show what the video looks like: at a technique described in words (query, e.g. "pressing the thumb into the dough"; preferred for "show me how she does X"), at an exact second the person named (at_seconds), or at a step (default: the current step's key frame). Returns the frame as an image plus a clip (start/end seconds) a screen can replay."""
    sess, g, err = _load(session_id)
    if err:
        return _error(err)
    watched = _watched(g)
    if query and at_seconds is None:
        hits = evidence.search(_passages(g), query, k=3)
        topic = frozenset(evidence.tokens(g["title"]))  # "orecchiette" names the dish, "thumb" names the move
        line = evidence.best_line(watched.segments, sorted({i for _, p in hits for i in p.lines}), query, ignore=topic)
        if line is not None:
            at_seconds = line.start + 2.0  # creators usually show the move a beat after they name it
        elif hits:
            st = g["steps"][hits[0][1].step_number - 1]
            at_seconds = st["frame_t"]
    elif at_seconds is not None and any(abs(seg.start - at_seconds) < 0.35 for seg in watched.segments):
        at_seconds += 2.0  # a time taken from a quoted line: show the move a beat after the words, replay from the words
    if at_seconds is not None:
        t = max(0.0, min(float(at_seconds), watched.duration_s))
        img = extract.exact_frame(watched, t)
        clip = {"start_s": max(0.0, t - 2), "end_s": min(watched.duration_s, t + 8)}
        label = f"the video at {extract.clock(t)}"
    else:
        idx = (step_number - 1) if step_number else sess["step_index"]
        idx = max(0, min(idx, len(g["steps"]) - 1))
        st = g["steps"][idx]
        t = st["frame_t"]
        img = extract.exact_frame(watched, t)
        # around the step's key frame, not the whole step: a kitchen replay should be short
        clip = {"start_s": max(st["start_s"], t - 4), "end_s": min(st["end_s"], t + 8)}
        label = f"step {st['step_number']} ({st['title']}) at {extract.clock(t)}: {st['look_for']}"
    clip["video_url"] = _media(g["guide_id"], "video")
    data = {"at_s": round(t, 1), "video_time": extract.clock(t), "image_url": _media(g["guide_id"], "moment", f"{t:.2f}"), "clip": clip, "description": label}
    return _result(data, f"Attached: {label}. Replay clip {extract.clock(clip['start_s'])}-{extract.clock(clip['end_s'])}.", [img])


@mcp.tool(title="Set a timer", annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True), meta=UI_META)
async def set_timer(seconds: int, label: str, session_id: str | None = None, request_id: str | None = None) -> CallToolResult:
    """Start a timer tied to the session (for example the 30-minute rest). The deadline is stored, so time left is still right after the app or server restarts. Reusing a request_id returns the same timer instead of a duplicate."""
    sess, g, err = _load(session_id)
    if err:
        return _error(err)
    if seconds <= 0 or seconds > 24 * 3600:
        return _error("seconds must be between 1 and 86400.")
    t = store.add_timer(sess["id"], int(seconds), label.strip()[:60] or "Timer", request_id)
    timers = store.timers_for(sess["id"])
    me = next(x for x in timers if x["timer_id"] == t["id"])
    speak = f"Timer set: {me['label'].lower()}, {_minutes(seconds)}."
    return _result({"timer": me, "timers": timers, "speak": speak}, f"Suggested spoken reply: {speak}\nTimer '{me['label']}' set for {seconds // 60} min {seconds % 60} s; due {me['due_at_utc']}.")


@mcp.tool(title="Cancel a timer", annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True), meta=UI_META)
async def cancel_timer(timer_id: str, session_id: str | None = None) -> CallToolResult:
    """Cancel a running timer."""
    sess, g, err = _load(session_id)
    if err:
        return _error(err)
    ok = store.cancel_timer(sess["id"], timer_id)
    return _result({"cancelled": ok, "timers": store.timers_for(sess["id"])}, "Cancelled." if ok else "No such running timer.")


# ---------------------------------------------------------------- media + trace routes

@mcp.custom_route("/media/{guide_id}/frame/{n}", methods=["GET"])
async def media_frame(request: Request) -> Response:
    g = store.get_guide(request.path_params["guide_id"])
    if not g:
        return Response(status_code=404)
    try:
        n = int(request.path_params["n"].split(".")[0])
    except ValueError:
        return Response(status_code=404)
    fr = next((f for f in _watched(g).frames if f.index == n), None)
    return FileResponse(fr.path, media_type="image/jpeg") if fr else Response(status_code=404)


@mcp.custom_route("/media/{guide_id}/moment/{t}", methods=["GET"])
async def media_moment(request: Request) -> Response:
    g = store.get_guide(request.path_params["guide_id"])
    if not g:
        return Response(status_code=404)
    try:
        t = float(request.path_params["t"])
    except ValueError:
        return Response(status_code=404)
    return FileResponse(extract.exact_frame(_watched(g), t), media_type="image/jpeg")


@mcp.custom_route("/media/{guide_id}/video", methods=["GET"])
async def media_video(request: Request) -> Response:
    g = store.get_guide(request.path_params["guide_id"])
    w = _watched(g) if g else None
    if not w or not w.video_path:
        return Response(status_code=404)
    media = "video/webm" if w.video_path.suffix == ".webm" else "video/mp4"
    return FileResponse(w.video_path, media_type=media)


TRACE: deque[dict[str, Any]] = deque(maxlen=300)


@mcp.custom_route("/trace", methods=["GET"])
async def trace(request: Request) -> Response:
    since = float(request.query_params.get("since", "0") or 0)
    return JSONResponse([t for t in TRACE if t["ts"] > since])


class TraceMiddleware:
    """Records every JSON-RPC message on /mcp (method, tool, protocol version, session) for the protocol inspector."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
        entries = []
        if scope["method"] == "POST":
            try:
                payload = json.loads(body or b"null")
                for m in payload if isinstance(payload, list) else [payload]:
                    if not isinstance(m, dict):
                        continue
                    params = m.get("params") or {}
                    e = {
                        "ts": time.time(),
                        "direction": "client → server",
                        "method": m.get("method") or "(response)",
                        "id": m.get("id"),
                        "tool": params.get("name") if m.get("method") == "tools/call" else None,
                        "arguments": params.get("arguments") if m.get("method") == "tools/call" else None,
                        "requested_protocol_version": params.get("protocolVersion") if m.get("method") == "initialize" else None,
                        "client": (params.get("clientInfo") or {}).get("name") if m.get("method") == "initialize" else None,
                        "header_protocol_version": headers.get("mcp-protocol-version"),
                        "session": (headers.get("mcp-session-id") or "")[:12],
                        "origin": headers.get("origin"),
                    }
                    entries.append(e)
            except ValueError:
                pass
        elif scope["method"] in ("GET", "DELETE"):
            entries.append({"ts": time.time(), "direction": "client → server", "method": f"HTTP {scope['method']} /mcp", "session": (headers.get("mcp-session-id") or "")[:12]})
        TRACE.extend(entries)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        captured = bytearray()
        status = {"code": 0}
        is_init = any(e.get("method") == "initialize" for e in entries)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                for k, v in message.get("headers", []):
                    if k.decode().lower() == "mcp-session-id":
                        for e in entries:
                            e["session"] = v.decode()[:12]
            elif message["type"] == "http.response.body" and is_init and len(captured) < 8000:
                captured.extend(message.get("body", b""))
            await send(message)

        await self.app(scope, replay, send_wrapper)
        for e in entries:
            e["http_status"] = status["code"]
        if is_init and captured:
            import re

            text = captured.decode(errors="ignore")
            m = re.search(r'"protocolVersion"\s*:\s*"([^"]+)"', text)
            n = re.search(r'"serverInfo"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', text)
            TRACE.append({
                "ts": time.time(), "direction": "server → client", "method": "initialize result",
                "negotiated_protocol_version": m.group(1) if m else None, "server": n.group(1) if n else None,
                "session": entries[0].get("session") if entries else None,
            })


def build_app():
    config.ensure_dirs()
    store.fail_stale_jobs()
    hostport = config.PUBLIC_BASE_URL.split("://", 1)[-1]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[hostport, "127.0.0.1:*", "localhost:*"],
        allowed_origins=config.ALLOWED_ORIGINS,
    )
    app = mcp.streamable_http_app(transport_security=security)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "mcp-session-id"],
    )
    app.add_middleware(TraceMiddleware)
    return app
