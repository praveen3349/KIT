"""FastAPI app: pages, login, streaming chat/voice, history and health."""
import asyncio
import json
import logging
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, config, db, voice
from .auth import Owner
from .college import AuthFailed, college, error_message
from .knowledge import knowledge
from .tools import registry

log = logging.getLogger("kit.main")
STATIC = Path(__file__).resolve().parent.parent / "static"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; media-src 'self' blob: data:; "
                                "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
}


class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window, self.hits = limit, window, defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, dq = time.monotonic(), self.hits[key]
        while dq and now - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(now)
        return True


chat_limiter = RateLimiter(30, 60)
login_limiter = RateLimiter(8, 60)
_public_cache: dict[str, tuple[float, object]] = {}


async def _background() -> None:
    await registry.ensure()
    while True:
        try:
            await knowledge.sync()
            await db.purge_expired()
        except Exception:
            log.exception("background refresh failed")
        await asyncio.sleep(600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    task = asyncio.create_task(_background())
    yield
    task.cancel()


app = FastAPI(title="KIT Voice Assistant", lifespan=lifespan)


@app.middleware("http")
async def security(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    new_vid = getattr(request.state, "new_visitor", None)
    if new_vid:
        response.set_cookie("kit_visitor", new_vid, max_age=60 * 60 * 24 * 365, httponly=True,
                            samesite="lax", secure=_is_https(request), path="/")
    return response


@app.exception_handler(Exception)
async def friendly_errors(request: Request, exc: Exception):
    log.exception("Unhandled error")
    return JSONResponse({"detail": "Something went wrong on the server. Please try again."}, status_code=500)


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


async def current_owner(request: Request) -> Owner:
    owner, new_vid = await auth.owner_from_cookies(
        request.cookies.get("kit_session"), request.cookies.get("kit_visitor"))
    if new_vid:
        request.state.new_visitor = new_vid
    return owner


def _session_info(owner: Owner) -> dict:
    return {"role": owner.role, "name": owner.name, "login_id": owner.login_id,
            "college": config.COLLEGE_NAME, "short": config.COLLEGE_SHORT}


# ----------------------------- pages -----------------------------
@app.get("/")
async def page_home():
    return FileResponse(STATIC / "index.html")


@app.get("/login")
async def page_login():
    return FileResponse(STATIC / "login.html")


@app.get("/history")
async def page_history():
    return FileResponse(STATIC / "history.html")


# ----------------------------- session -----------------------------
class LoginIn(BaseModel):
    login_id: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


@app.get("/api/session")
async def session(request: Request):
    return _session_info(await current_owner(request))


@app.post("/api/login")
async def login(body: LoginIn, request: Request):
    if not login_limiter.allow(_ip(request)):
        raise HTTPException(429, "Too many login attempts. Wait a minute and try again.")
    try:
        sid, owner = await auth.login(body.login_id.strip(), body.password)
    except AuthFailed:
        raise HTTPException(401, "Incorrect login ID or password.")
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))
    except ValueError:
        raise HTTPException(502, "The college system sent an unexpected login reply.")
    response = JSONResponse(_session_info(owner))
    response.set_cookie("kit_session", sid, max_age=int(config.SESSION_HOURS * 3600), httponly=True,
                        samesite="lax", secure=_is_https(request), path="/")
    return response


@app.post("/api/logout")
async def logout(request: Request):
    sid = request.cookies.get("kit_session")
    if sid:
        await db.delete_session(sid)
    response = JSONResponse({"ok": True})
    response.delete_cookie("kit_session", path="/")
    return response


# ----------------------------- public college info (visitor calls) -----------------------------
async def _public_get(path: str):
    cached = _public_cache.get(path)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    res = await college.request("GET", path)
    if res["status"] != 200:
        raise HTTPException(502, error_message(res["data"]))
    _public_cache[path] = (time.time(), res["data"])
    return res["data"]


@app.get("/api/public/college")
async def public_college():
    return await _public_get("/v1/college")


@app.get("/api/public/courses")
async def public_courses():
    return await _public_get("/v1/courses")


# ----------------------------- streaming turns -----------------------------
async def _ndjson(events):
    async for ev in events:
        yield (json.dumps(ev, ensure_ascii=False) + "\n").encode()


def _stream(events) -> StreamingResponse:
    return StreamingResponse(_ndjson(events), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


async def _conversation(owner: Owner, conversation_id: str | None) -> str:
    if conversation_id:
        conv = await db.get_conversation(owner.key, conversation_id)
        if conv and conv["mode"] == owner.mode:
            return conv["id"]
    return await db.create_conversation(owner.key, owner.mode)


async def turn_events(owner: Owner, conversation_id: str | None, text: str, speak: bool,
                      transcript: str | None = None, voice_name: str | None = None):
    try:
        conv_id = await _conversation(owner, conversation_id)
        yield {"type": "meta", "conversation_id": conv_id}
        if transcript:
            yield {"type": "transcript", "text": transcript}
        final = None
        async for ev in agent.run_turn(owner, conv_id, text):
            if ev["type"] == "reply":
                final = ev
            yield ev
        if speak and final:
            async for ev in voice.speech_events(final["text"], voice_name):
                yield ev
    except Exception:
        log.exception("turn failed")
        yield {"type": "error", "message": "Something went wrong. Please try again."}
    yield {"type": "done"}


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=config.MAX_MESSAGE_CHARS)
    conversation_id: str | None = Field(default=None, max_length=64)
    speak: bool = False
    voice: str | None = Field(default=None, max_length=20)


@app.post("/api/chat")
async def chat(body: ChatIn, request: Request):
    owner = await current_owner(request)
    if not chat_limiter.allow(owner.key):
        raise HTTPException(429, "You are sending messages too quickly. Please wait a moment.")
    return _stream(turn_events(owner, body.conversation_id, body.message.strip(), body.speak,
                               voice_name=body.voice))


@app.post("/api/voice")
async def voice_turn(request: Request, audio: UploadFile = File(...),
                     conversation_id: str | None = Form(None), speak: bool = Form(True),
                     voice_name: str | None = Form(None)):
    owner = await current_owner(request)
    if not chat_limiter.allow(owner.key):
        raise HTTPException(429, "You are sending messages too quickly. Please wait a moment.")
    data = await audio.read(config.MAX_AUDIO_BYTES + 1)
    if len(data) > config.MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Please keep it under a minute.")
    # Short spoken questions can be smaller than 1.5 KB with modern Opus codecs.
    # Let Whisper decide whether the recording contains speech.
    if len(data) < 600:
        raise HTTPException(422, "I could not hear anything. Please try again.")
    ctype = audio.content_type or "audio/webm"
    ext = "mp4" if "mp4" in ctype else "ogg" if "ogg" in ctype else "wav" if "wav" in ctype else "webm"

    async def events():
        try:
            text = await voice.transcribe(data, f"speech.{ext}", ctype)
        except voice.VoiceError as exc:
            yield {"type": "error", "message": str(exc)}
            yield {"type": "done"}
            return
        if not text:
            yield {"type": "empty"}
            yield {"type": "done"}
            return
        async for ev in turn_events(owner, conversation_id, text[:config.MAX_MESSAGE_CHARS], speak,
                                    transcript=text, voice_name=voice_name):
            yield ev

    return _stream(events())


# ----------------------------- history -----------------------------
_ID = re.compile(r"^[a-f0-9]{32}$")


@app.get("/api/conversations")
async def conversations(request: Request):
    owner = await current_owner(request)
    return await db.list_conversations(owner.key)


@app.get("/api/conversations/{cid}")
async def conversation(cid: str, request: Request):
    owner = await current_owner(request)
    conv = await db.get_conversation(owner.key, cid) if _ID.match(cid) else None
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    return {**conv, "messages": await db.get_messages(cid)}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str, request: Request):
    owner = await current_owner(request)
    if _ID.match(cid):
        await db.delete_conversation(owner.key, cid)
    return {"ok": True}


# ----------------------------- health -----------------------------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "college_key_set": bool(config.COLLEGE_API_KEY),
        "groq_key_set": bool(config.GROQ_API_KEY),
        "tools": len(registry.tools), "tools_source": registry.source,
        "documents_indexed": knowledge.titles,
        "chat_models": config.CHAT_MODELS, "tts": config.TTS_PROVIDER,
    }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
