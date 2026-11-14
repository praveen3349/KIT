import asyncio

from kit.auth import Owner
from kit.tools import build_tools, execute, is_public_allowed, registry, role_allows, simplify

SPEC = {"paths": {"/v1/x": {"post": {"summary": "Make x", "requestBody": {"content": {"application/json": {
    "schema": {"$ref": "#/components/schemas/X"}}}}}}},
    "components": {"schemas": {"X": {"type": "object", "required": ["a"], "properties": {
        "a": {"type": "string"}, "b": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "c": {"enum": ["u", "v"]}}}}}}


def test_simplify_resolves_refs_and_nullable():
    s = simplify(SPEC, {"$ref": "#/components/schemas/X"})
    assert s["properties"]["b"]["type"] == "integer"
    assert s["properties"]["c"]["enum"] == ["u", "v"]


def test_build_tools_flattens_body_and_adds_confirmation():
    tools = build_tools(SPEC)
    t = tools["post_x"]
    assert t.write and t.body_mode == "flat"
    assert "user_confirmed" in t.schema["required"] and "a" in t.schema["required"]


def test_public_allow_list():
    assert is_public_allowed("GET", "/v1/courses")
    assert is_public_allowed("POST", "/v1/admissions/applications")
    assert not is_public_allowed("GET", "/v1/me/attendance")
    assert not is_public_allowed("POST", "/v1/leave-requests")
    assert not is_public_allowed("GET", "/v1/placements/drives/1/registrations")


def test_student_cannot_reach_staff_or_other_students():
    assert role_allows("student", "GET", "/v1/me/attendance")
    assert not role_allows("student", "GET", "/v1/teacher/approvals")
    assert not role_allows("student", "POST", "/v1/leave-requests/5/approve")
    assert not role_allows("student", "GET", "/v1/students/7")
    assert role_allows("teacher", "GET", "/v1/teacher/approvals")
    assert not role_allows("teacher", "POST", "/v1/auth/login")


def test_tools_from_live_spec_and_per_role_filtering(fake_college):
    async def go():
        await registry.ensure()
        assert registry.source == "openapi"
        pub, _ = registry.for_owner(Owner("v:1", "public"))
        stu, _ = registry.for_owner(Owner("u:a", "student", token="tok-a"))
        tea, _ = registry.for_owner(Owner("u:l", "teacher", token="tok-l"))
        return set(pub), set(stu), set(tea)
    pub, stu, tea = asyncio.run(go())
    assert pub == {"get_courses", "post_admissions_applications"}
    assert "get_me_attendance" in stu and "get_teacher_approvals" not in stu
    assert "get_teacher_approvals" in tea


def test_write_needs_confirmation_and_is_idempotent(fake_college):
    owner = Owner("u:a", "student", token="tok-a")

    async def go():
        await registry.ensure()
        tool = registry.tools["post_me_leave_requests"]
        args = {"from_date": "2026-12-01", "to_date": "2026-12-02", "reason": "fever"}
        r1 = await execute(tool, args, owner, "conv1")
        r2 = await execute(tool, {**args, "user_confirmed": True}, owner, "conv1")
        r3 = await execute(tool, {**args, "user_confirmed": True}, owner, "conv1")
        return r1, r2, r3
    r1, r2, r3 = asyncio.run(go())
    assert r1["error"] == "not_confirmed"
    assert r2["status"] == 200
    keys = fake_college.state.seen["idem"]
    assert len(keys) == 2 and keys[0] == keys[1] and keys[0]


def test_api_validation_error_is_passed_back(fake_college):
    owner = Owner("u:a", "student", token="tok-a")

    async def go():
        await registry.ensure()
        tool = registry.tools["post_me_leave_requests"]
        return await execute(tool, {"from_date": "not-a-date", "to_date": "x", "reason": "r",
                                    "user_confirmed": True}, owner, "c")
    res = asyncio.run(go())
    assert res["status"] == 422


def test_visitor_cannot_call_student_endpoint_even_if_model_tries(fake_college):
    async def go():
        await registry.ensure()
        return await execute(registry.tools["get_me_attendance"], {}, Owner("v:1", "public"), "c")
    assert "not allowed" in asyncio.run(go())["error"]
