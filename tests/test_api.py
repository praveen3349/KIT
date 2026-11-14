import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kit import llm
from kit.main import app


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def say(text):
    async def fake(messages, tools):
        return SimpleNamespace(content=text, tool_calls=None)
    return fake


@pytest.fixture()
def client(fake_college):
    return TestClient(app, base_url="http://testserver")


def test_visitor_chat_streams_reply_and_saves_history(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", say("We have a B.Tech in Computer Science."))
    r = client.post("/api/chat", json={"message": "What courses do you have?"})
    evs = events(r)
    assert [e["type"] for e in evs][0] == "meta" and evs[-1]["type"] == "done"
    assert any(e["type"] == "reply" and "B.Tech" in e["text"] for e in evs)
    convs = client.get("/api/conversations").json()
    assert len(convs) == 1 and convs[0]["mode"] == "public"
    detail = client.get(f"/api/conversations/{convs[0]['id']}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_history_not_visible_to_another_visitor(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", say("Hi"))
    client.post("/api/chat", json={"message": "hello"})
    cid = client.get("/api/conversations").json()[0]["id"]
    other = TestClient(app, base_url="http://testserver")
    assert other.get("/api/conversations").json() == []
    assert other.get(f"/api/conversations/{cid}").status_code == 404


def test_login_logout_and_cookie_flags(client):
    bad = client.post("/api/login", json={"login_id": "arjun", "password": "nope"})
    assert bad.status_code == 401
    ok = client.post("/api/login", json={"login_id": "arjun", "password": "Kit@2026"})
    assert ok.status_code == 200 and ok.json()["role"] == "student" and ok.json()["name"] == "Arjun"
    assert "httponly" in ok.headers["set-cookie"].lower()
    assert "tok-arjun" not in ok.text                      # college token never reaches the browser
    assert client.get("/api/session").json()["login_id"] == "arjun"
    client.post("/api/logout")
    assert client.get("/api/session").json()["role"] == "public"


def test_student_flow_uses_tool_then_answers(client, monkeypatch):
    client.post("/api/login", json={"login_id": "arjun", "password": "Kit@2026"})
    calls = []

    async def fake(messages, tools):
        calls.append([t["function"]["name"] for t in tools])
        if not any(m["role"] == "tool" for m in messages):
            call = SimpleNamespace(id="1", function=SimpleNamespace(name="get_me_attendance", arguments="{}"))
            return SimpleNamespace(content=None, tool_calls=[call])
        tool_msg = [m for m in messages if m["role"] == "tool"][-1]["content"]
        assert "82.5" in tool_msg
        return SimpleNamespace(content="Your attendance is eighty two percent.", tool_calls=None)
    monkeypatch.setattr(llm, "complete", fake)
    evs = events(client.post("/api/chat", json={"message": "What is my attendance?"}))
    types = [e["type"] for e in evs]
    assert "status" in types and types.index("status") < types.index("reply")
    assert [e for e in evs if e["type"] == "reply"][0]["tools"] == ["get_me_attendance"]
    assert "get_me_attendance" in calls[0]


def test_llm_outage_degrades_to_document_answer(client, monkeypatch):
    import asyncio
    from kit.knowledge import knowledge
    asyncio.run(knowledge.sync())

    async def boom(messages, tools):
        raise llm.LLMUnavailable("down")
    monkeypatch.setattr(llm, "complete", boom)
    evs = events(client.post("/api/chat", json={"message": "what is the annual fee"}))
    reply = [e for e in evs if e["type"] == "reply"][0]["text"]
    assert "1,20,000" in reply


def test_voice_endpoint_transcribes_then_answers(client, monkeypatch):
    from kit import voice

    async def fake_stt(data, filename, ctype):
        return "tell me about admissions"
    monkeypatch.setattr(voice, "transcribe", fake_stt)
    monkeypatch.setattr(llm, "complete", say("Admissions close on June thirtieth."))
    r = client.post("/api/voice", files={"audio": ("a.webm", b"x" * 4000, "audio/webm")}, data={"speak": "true"})
    evs = events(r)
    assert any(e["type"] == "transcript" and "admissions" in e["text"] for e in evs)
    assert any(e["type"] == "tts" and e["mode"] == "browser" for e in evs)   # tests run with TTS_PROVIDER=browser


def test_cross_origin_post_is_blocked(client):
    r = client.post("/api/chat", json={"message": "hi"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_rejects_oversized_message(client):
    assert client.post("/api/chat", json={"message": "x" * 2000}).status_code == 422


def test_pages_assets_and_security_headers(client):
    for path in ("/", "/login", "/history", "/static/app.css", "/static/assistant.js"):
        r = client.get(path)
        assert r.status_code == 200, path
    h = client.get("/").headers
    assert "script-src 'self'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"
    assert client.get("/api/health").json()["status"] == "ok"
