"""Speech services. STT: Whisper on Groq. TTS: Orpheus on Groq, with browser voice as a fallback."""
import asyncio
import base64
import logging
import re

import httpx

from . import config

log = logging.getLogger("kit.voice")
BASE = "https://api.groq.com/openai/v1"
HALLUCINATIONS = {"you", "thanks for watching", "thank you for watching", "bye", "."}
STT_PROMPT = ("Knotopian Institute of Technology, KIT, attendance, on-duty, leave request, "
              "placement drive, admission, timetable, hostel, semester.")
_http: httpx.AsyncClient | None = None
VOICE_CHOICES = {"autumn", "diana", "hannah", "austin", "daniel", "troy"}


class VoiceError(Exception):
    pass


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=8.0))
    return _http


def _auth() -> dict:
    return {"Authorization": f"Bearer {config.GROQ_API_KEY}"}


async def transcribe(data: bytes, filename: str, content_type: str | None) -> str:
    """Speech to text. Falls back to a second Whisper model if the first one fails."""
    if not config.GROQ_API_KEY:
        raise VoiceError("Speech recognition is not configured. Add GROQ_API_KEY to the server environment.")
    last_error = ""
    for model in config.STT_MODELS:
        form = {"model": model, "response_format": "json", "temperature": "0", "prompt": STT_PROMPT}
        if config.STT_LANGUAGE:
            form["language"] = config.STT_LANGUAGE
        try:
            r = await _client().post(f"{BASE}/audio/transcriptions", headers=_auth(), data=form,
                                     files={"file": (filename, data, content_type or "application/octet-stream")})
        except httpx.TransportError as exc:
            log.warning("STT %s network error: %s", model, exc)
            continue
        if r.status_code == 401:
            raise VoiceError("The Groq API key is missing or invalid.")
        if r.status_code >= 400:
            log.warning("STT %s failed (%s): %s", model, r.status_code, r.text[:200])
            last_error = f"status {r.status_code}"
            continue
        try:
            payload = r.json()
        except ValueError:
            log.warning("STT %s returned invalid JSON", model)
            last_error = "invalid response"
            continue
        text = str(payload.get("text", "")).strip() if isinstance(payload, dict) else ""
        return "" if text.lower().strip(" .!") in HALLUCINATIONS else text
    if last_error == "status 400":
        raise VoiceError("The audio format was not accepted. Please try again in Chrome or Edge, or type your question.")
    raise VoiceError("Speech recognition is unavailable right now. You can type your question instead.")


def tts_clean(text: str) -> str:
    """Make written text easy to speak."""
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = text.replace("%", " percent").replace("&", " and ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_text(text: str, limit: int = 190) -> list[str]:
    """Orpheus takes about 200 characters per request, so split at sentence ends."""
    chunks, cur = [], ""
    for p in re.split(r"(?<=[.!?])\s+", text.strip()):
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:cut])
            p = p[cut:].strip()
        if len(cur) + len(p) + 1 <= limit:
            cur = (cur + " " + p).strip()
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


async def _synth(chunk: str, voice_name: str) -> bytes | None:
    for attempt in range(2):
        try:
            r = await _client().post(f"{BASE}/audio/speech", headers=_auth(), json={
                "model": config.TTS_MODEL, "voice": voice_name, "input": chunk,
                "response_format": "wav"})
        except httpx.TransportError:
            await asyncio.sleep(0.3)
            continue
        if r.status_code == 200:
            return r.content
        log.warning("TTS failed (%s): %s", r.status_code, r.text[:200])
        if r.status_code != 429:
            return None
        await asyncio.sleep(1.0)
    return None


async def speech_events(text: str, voice_name: str | None = None):
    """Yields audio events one sentence group at a time, so speech starts before all of it is ready.
    If anything fails, tells the browser to speak the reply with its own voice instead."""
    if config.TTS_PROVIDER != "groq" or not config.GROQ_API_KEY:
        yield {"type": "tts", "mode": "browser"}
        return
    chunks = split_text(tts_clean(text))
    if not chunks or len(chunks) > 4:
        yield {"type": "tts", "mode": "browser"}
        return
    selected_voice = voice_name.lower() if voice_name else config.TTS_VOICE
    # Never pass arbitrary user input through to the provider.
    if selected_voice not in VOICE_CHOICES:
        selected_voice = config.TTS_VOICE if config.TTS_VOICE in VOICE_CHOICES else "hannah"
    tasks = [asyncio.create_task(_synth(c, selected_voice)) for c in chunks]
    try:
        for i, task in enumerate(tasks):
            audio = await task
            if audio is None:
                raise RuntimeError("tts failed")
            yield {"type": "audio", "index": i, "total": len(tasks), "b64": base64.b64encode(audio).decode()}
    except RuntimeError:
        for t in tasks:
            t.cancel()
        yield {"type": "tts", "mode": "browser", "reason": "fallback"}
