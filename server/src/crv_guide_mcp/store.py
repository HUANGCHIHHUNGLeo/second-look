"""SQLite persistence: guides, import jobs, cooking sessions and timers.

All state lives here so a session survives a closed browser, a new MCP connection
or a restarted server. Timer deadlines are stored as absolute UTC epoch seconds.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_lock = threading.RLock()

SCHEMA = """
create table if not exists guides (
  id text primary key,
  source text not null,
  source_key text not null unique,
  folder text not null,
  title text not null,
  data text not null,
  created_at real not null
);
create table if not exists jobs (
  id text primary key,
  source text not null,
  status text not null check (status in ('processing','ready','failed')),
  stage text,
  guide_id text,
  error text,
  created_at real not null,
  updated_at real not null
);
create table if not exists sessions (
  id text primary key,
  guide_id text not null references guides(id),
  step_index integer not null default 0,
  completed text not null default '[]',
  status text not null default 'active' check (status in ('active','finished')),
  revision integer not null default 0,
  created_at real not null,
  updated_at real not null
);
create table if not exists timers (
  id text primary key,
  session_id text not null references sessions(id),
  label text not null,
  seconds integer not null,
  due_at real not null,
  request_id text unique,
  cancelled integer not null default 0,
  created_at real not null
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.ensure_dirs()
    with _lock:
        con = sqlite3.connect(config.DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            con.executescript(SCHEMA)
            yield con
            con.commit()
        finally:
            con.close()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------- guides ----------

def guide_by_source_key(key: str) -> sqlite3.Row | None:
    with connect() as con:
        return con.execute("select * from guides where source_key=?", (key,)).fetchone()


def save_guide(source: str, source_key: str, folder: str, data: dict[str, Any]) -> str:
    gid = new_id("guide")
    with connect() as con:
        con.execute(
            "insert into guides (id, source, source_key, folder, title, data, created_at) values (?,?,?,?,?,?,?)"
            " on conflict(source_key) do update set folder=excluded.folder, title=excluded.title, data=excluded.data",
            (gid, source, source_key, folder, data.get("title", "Untitled"), json.dumps(data), time.time()),
        )
        row = con.execute("select id from guides where source_key=?", (source_key,)).fetchone()
    return row["id"]


def get_guide(guide_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("select * from guides where id=?", (guide_id,)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    data.update({"guide_id": row["id"], "source": row["source"], "folder": row["folder"]})
    return data


def list_guides() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("select id, title, data, created_at from guides order by created_at desc").fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"])
        out.append(
            {
                "guide_id": r["id"],
                "title": r["title"],
                "steps": len(d.get("steps", [])),
                "video_duration_s": d.get("video_duration_s"),
            }
        )
    return out


# ---------- import jobs ----------

def create_job(source: str) -> str:
    jid = new_id("job")
    now = time.time()
    with connect() as con:
        con.execute(
            "insert into jobs (id, source, status, stage, created_at, updated_at) values (?,?,?,?,?,?)",
            (jid, source, "processing", "queued", now, now),
        )
    return jid


def update_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"update jobs set {cols} where id=?", (*fields.values(), job_id))


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("select * from jobs where id=?", (job_id,)).fetchone()
    return dict(row) if row else None


# ---------- sessions ----------

def create_session(guide_id: str) -> str:
    sid = new_id("cook")
    now = time.time()
    with connect() as con:
        con.execute(
            "insert into sessions (id, guide_id, created_at, updated_at) values (?,?,?,?)",
            (sid, guide_id, now, now),
        )
    return sid


def get_session(session_id: str) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("select * from sessions where id=?", (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["completed"] = json.loads(d["completed"])
    return d


def latest_active_session() -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute(
            "select id from sessions where status='active' order by updated_at desc limit 1"
        ).fetchone()
    return get_session(row["id"]) if row else None


def update_session(session_id: str, *, step_index: int, completed: list[int], status: str, expected_revision: int | None) -> bool:
    """Write progress. Returns False when expected_revision no longer matches (another client moved first)."""
    with connect() as con:
        row = con.execute("select revision from sessions where id=?", (session_id,)).fetchone()
        if not row:
            return False
        if expected_revision is not None and row["revision"] != expected_revision:
            return False
        con.execute(
            "update sessions set step_index=?, completed=?, status=?, revision=revision+1, updated_at=? where id=?",
            (step_index, json.dumps(sorted(set(completed))), status, time.time(), session_id),
        )
    return True


# ---------- timers ----------

def add_timer(session_id: str, seconds: int, label: str, request_id: str | None) -> dict[str, Any]:
    with connect() as con:
        if request_id:
            row = con.execute("select * from timers where request_id=?", (request_id,)).fetchone()
            if row:
                return dict(row)
        tid = new_id("timer")
        now = time.time()
        con.execute(
            "insert into timers (id, session_id, label, seconds, due_at, request_id, created_at) values (?,?,?,?,?,?,?)",
            (tid, session_id, label, seconds, now + seconds, request_id, now),
        )
        return dict(con.execute("select * from timers where id=?", (tid,)).fetchone())


def cancel_timer(session_id: str, timer_id: str) -> bool:
    with connect() as con:
        cur = con.execute(
            "update timers set cancelled=1 where id=? and session_id=? and cancelled=0", (timer_id, session_id)
        )
        return cur.rowcount > 0


def timers_for(session_id: str) -> list[dict[str, Any]]:
    now = time.time()
    with connect() as con:
        rows = con.execute(
            "select * from timers where session_id=? and cancelled=0 order by due_at", (session_id,)
        ).fetchall()
    out = []
    for r in rows:
        remaining = r["due_at"] - now
        out.append(
            {
                "timer_id": r["id"],
                "label": r["label"],
                "seconds": r["seconds"],
                "due_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r["due_at"])),
                "remaining_s": max(0, int(round(remaining))),
                "status": "running" if remaining > 0 else "done",
            }
        )
    return out
