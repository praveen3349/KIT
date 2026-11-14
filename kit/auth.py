"""Who is talking to us: a visitor, a student or a teacher."""
import re
import secrets
from dataclasses import dataclass

from . import db
from .college import college


@dataclass
class Owner:
    key: str                      # used to scope history: "v:<visitor id>" or "u:<login id>"
    role: str                     # "public", "student" or "teacher"
    name: str | None = None
    login_id: str | None = None
    token: str | None = None      # college bearer token, server side only
    session_id: str | None = None

    @property
    def mode(self) -> str:
        return self.role


def _dig(data: dict, keys: tuple[str, ...]):
    """Find the first present key at the top level or one level down."""
    layers = [data] + [v for v in data.values() if isinstance(v, dict)]
    for layer in layers:
        for k in keys:
            if layer.get(k):
                return layer[k]
    return None


def parse_login(payload: dict, login_id: str) -> tuple[str, str, str | None]:
    """Returns (token, role, display name) from whatever shape the login reply has."""
    token = _dig(payload, ("access_token", "token", "jwt", "bearer_token"))
    if not token:
        raise ValueError("Login reply had no token")
    raw_role = str(_dig(payload, ("role", "user_type", "type", "user_role")) or "").lower()
    role = "teacher" if re.search(r"teach|faculty|staff|lectur", raw_role) else "student"
    name = _dig(payload, ("name", "full_name", "display_name"))
    return str(token), role, (str(name) if name else None)


async def login(login_id: str, password: str) -> tuple[str, Owner]:
    payload = await college.login(login_id, password)
    token, role, name = parse_login(payload, login_id)
    if not name:                       # best effort profile lookup
        me = await college.request("GET", "/v1/me", token=token)
        if me["status"] == 200 and isinstance(me["data"], dict):
            name = _dig(me["data"], ("name", "full_name", "display_name"))
    sid = await db.create_session(token, login_id, role, name)
    return sid, Owner(key=f"u:{login_id}", role=role, name=name, login_id=login_id, token=token, session_id=sid)


async def owner_from_cookies(session_cookie: str | None, visitor_cookie: str | None) -> tuple[Owner, str | None]:
    """Returns (owner, new visitor id to set as a cookie, if one was needed)."""
    if session_cookie:
        s = await db.get_session(session_cookie)
        if s:
            return Owner(key=f"u:{s['login_id']}", role=s["role"], name=s["name"], login_id=s["login_id"],
                         token=s["token"], session_id=session_cookie), None
    if visitor_cookie and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", visitor_cookie):
        return Owner(key=f"v:{visitor_cookie}", role="public"), None
    vid = secrets.token_urlsafe(16)
    return Owner(key=f"v:{vid}", role="public"), vid
