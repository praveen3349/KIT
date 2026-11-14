import asyncio
import os
import tempfile
from datetime import date

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("COLLEGE_API_KEY", "test-key")
os.environ.setdefault("GROQ_API_KEY", "test-groq")
os.environ["TTS_PROVIDER"] = "browser"

from kit import config, db  # noqa: E402
from kit.college import college  # noqa: E402
from kit.knowledge import knowledge  # noqa: E402
from kit.tools import registry  # noqa: E402

HANDBOOK = ("Student Handbook. Attendance rule: students must maintain a minimum of 75 percent attendance. "
            "Leave rule: no leave is allowed on exam days. Hostel curfew is 9 PM for all residents. " * 3)
BROCHURE = ("Admissions Brochure. B.Tech Computer Science needs 85 percent in 12th grade. "
            "The annual fee is 1,20,000 rupees and there are 60 seats. Applications close on 30 June. " * 3)


class LeaveIn(BaseModel):
    from_date: date
    to_date: date
    reason: str
    leave_type: str = "casual"


class AppIn(BaseModel):
    name: str
    email: str
    course: str


def make_fake_college() -> FastAPI:
    fake = FastAPI()
    seen = {"idem": []}

    def need_token(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer tok-"):
            raise HTTPException(401, {"error": {"code": "unauthorized", "message": "Login required"}})

    @fake.post("/v1/auth/login")
    async def login(body: dict):
        if body.get("password") != "Kit@2026":
            raise HTTPException(401, {"error": {"code": "bad_login", "message": "Wrong ID or password"}})
        role = "teacher" if body["login_id"] == "lakshmi" else "student"
        return {"access_token": f"tok-{body['login_id']}", "user": {"name": body["login_id"].title(), "role": role}}

    @fake.get("/v1/me/attendance")
    async def attendance(authorization: str | None = Header(None)):
        need_token(authorization)
        return {"overall_percent": 82.5}

    @fake.post("/v1/me/leave-requests")
    async def leave(body: LeaveIn, request: Request, authorization: str | None = Header(None)):
        need_token(authorization)
        seen["idem"].append(request.headers.get("idempotency-key"))
        return {"id": "L1", "status": "pending"}

    @fake.get("/v1/teacher/approvals")
    async def approvals(authorization: str | None = Header(None)):
        need_token(authorization)
        return []

    @fake.get("/v1/students/{sid}")
    async def student(sid: str, authorization: str | None = Header(None)):
        need_token(authorization)
        return {"id": sid}

    @fake.get("/v1/courses")
    async def courses():
        return [{"name": "B.Tech CSE", "seats": 60}]

    @fake.post("/v1/admissions/applications")
    async def apply(body: AppIn):
        return {"id": "A1"}

    @fake.get("/v1/documents")
    async def docs():
        return [{"id": "d1", "title": "Student Handbook", "category": "handbook"},
                {"id": "d2", "title": "Admissions Brochure", "category": "brochure"}]

    @fake.get("/v1/documents/{doc_id}/file")
    async def doc_file(doc_id: str):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(HANDBOOK if doc_id == "d1" else BROCHURE)

    fake.state.seen = seen
    return fake


@pytest.fixture()
def fake_college():
    app = make_fake_college()
    college._client = httpx.AsyncClient(base_url="http://college", transport=httpx.ASGITransport(app=app))
    registry.tools, registry.source, registry.loaded_at = {}, "none", 0.0
    knowledge.signature = None
    asyncio.run(db.init())
    yield app
    college._client = None
