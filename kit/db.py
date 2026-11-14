"""SQLite storage: login sessions, conversations and messages (the History page)."""
import asyncio
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing

from . import config

_lock = threading.Lock()
DEFAULT_TITLE = "New conversation"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY, token TEXT NOT NULL, login_id TEXT NOT NULL,
    role TEXT NOT NULL, name TEXT, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, mode TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'New conversation', created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS conv_owner ON conversations(owner, updated DESC);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS msg_conv ON messages(conv_id, id);
"""


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA_DIR / "kit.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _run(sql: str, args: tuple = (), fetch: str | None = None):
    with _lock, closing(_connect()) as conn:
        cur = conn.execute(sql, args)
        out = None
        if fetch == "all":
            out = [dict(r) for r in cur.fetchall()]
        elif fetch == "one":
            row = cur.fetchone()
            out = dict(row) if row else None
        conn.commit()
        return out


def _init() -> None:
    with _lock, closing(_connect()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        conn.commit()


async def init() -> None:
    await asyncio.to_thread(_init)


# ---- sessions (the college token stays on the server, never in the browser) ----
async def create_session(token: str, login_id: str, role: str, name: str | None) -> str:
    sid = secrets.token_urlsafe(32)
    expires = time.time() + config.SESSION_HOURS * 3600
    await asyncio.to_thread(
        _run, "INSERT INTO sessions(id,token,login_id,role,name,expires) VALUES(?,?,?,?,?,?)",
        (sid, token, login_id, role, name, expires))
    return sid


async def get_session(sid: str) -> dict | None:
    row = await asyncio.to_thread(_run, "SELECT * FROM sessions WHERE id=?", (sid,), "one")
    if row and row["expires"] < time.time():
        await delete_session(sid)
        return None
    return row


async def delete_session(sid: str) -> None:
    await asyncio.to_thread(_run, "DELETE FROM sessions WHERE id=?", (sid,))


async def purge_expired() -> None:
    await asyncio.to_thread(_run, "DELETE FROM sessions WHERE expires<?", (time.time(),))


# ---- conversations ----
async def create_conversation(owner: str, mode: str) -> str:
    cid, ts = uuid.uuid4().hex, time.time()
    await asyncio.to_thread(
        _run, "INSERT INTO conversations(id,owner,mode,title,created,updated) VALUES(?,?,?,?,?,?)",
        (cid, owner, mode, DEFAULT_TITLE, ts, ts))
    return cid


async def get_conversation(owner: str, cid: str) -> dict | None:
    return await asyncio.to_thread(
        _run, "SELECT * FROM conversations WHERE id=? AND owner=?", (cid, owner), "one")


async def list_conversations(owner: str) -> list[dict]:
    return await asyncio.to_thread(
        _run,
        "SELECT c.id,c.mode,c.title,c.created,c.updated,"
        "(SELECT COUNT(*) FROM messages m WHERE m.conv_id=c.id) AS messages "
        "FROM conversations c WHERE c.owner=? AND EXISTS(SELECT 1 FROM messages m WHERE m.conv_id=c.id) "
        "ORDER BY c.updated DESC LIMIT 100", (owner,), "all")


async def delete_conversation(owner: str, cid: str) -> None:
    await asyncio.to_thread(_run, "DELETE FROM conversations WHERE id=? AND owner=?", (cid, owner))


async def add_message(cid: str, role: str, content: str, meta: dict | None = None) -> None:
    ts = time.time()
    await asyncio.to_thread(
        _run, "INSERT INTO messages(conv_id,role,content,meta,created) VALUES(?,?,?,?,?)",
        (cid, role, content, json.dumps(meta) if meta else None, ts))
    await asyncio.to_thread(_run, "UPDATE conversations SET updated=? WHERE id=?", (ts, cid))
    if role == "user":
        await asyncio.to_thread(
            _run, "UPDATE conversations SET title=? WHERE id=? AND title=?",
            (content[:60], cid, DEFAULT_TITLE))


async def get_messages(cid: str, limit: int | None = None) -> list[dict]:
    if limit:
        rows = await asyncio.to_thread(
            _run, "SELECT role,content,meta,created FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?",
            (cid, limit), "all")
        rows.reverse()
    else:
        rows = await asyncio.to_thread(
            _run, "SELECT role,content,meta,created FROM messages WHERE conv_id=? ORDER BY id",
            (cid,), "all")
    for r in rows:
        r["meta"] = json.loads(r["meta"]) if r["meta"] else None
    return rows
