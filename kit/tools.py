"""Turns the college system's OpenAPI spec into LLM tools, and runs them safely.

Why generate tools from the spec instead of hand-writing them? The college system can change
during the event (new endpoints, new fields). Reading the spec at runtime keeps the assistant
in sync with no code change.

Safety layers, all enforced in code (not only in the prompt):
  1. Visitors can only reach the six public calls, with no login token.
  2. Students cannot reach staff/approval/admin endpoints or other students' records.
  3. Every write needs `user_confirmed=true`, which the model may only set after a spoken "yes".
  4. Writes carry an Idempotency-Key so a retry never creates a duplicate request.
"""
import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from .auth import Owner
from .college import college, error_message

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SPEC_TTL = 600          # re-read the spec every 10 minutes
MAX_RESULT_CHARS = 7000
STAFF_SEGMENT = re.compile(r"^(teachers?|staff|faculty|admin\w*|approv\w*|reject\w*)", re.I)

PUBLIC_GET = [re.compile(p) for p in (
    r"^/v1/college$", r"^/v1/today$",
    r"^/v1/courses(/[^/]+)?$", r"^/v1/placements/drives(/[^/]+)?$")]
PUBLIC_POST = [re.compile(r"^/v1/admissions/applications$")]


@dataclass
class Tool:
    name: str
    method: str
    template: str
    description: str
    schema: dict
    arg_map: dict = field(default_factory=dict)   # arg name -> (where, real name)
    body_mode: str | None = None                  # "flat", "raw" or None
    write: bool = False
    generic: bool = False

    def openai(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.schema}}


# ----------------------------- permissions -----------------------------
def is_public_allowed(method: str, path: str) -> bool:
    patterns = PUBLIC_GET if method == "GET" else PUBLIC_POST if method == "POST" else []
    return any(p.match(path) for p in patterns)


def role_allows(role: str, method: str, path: str) -> bool:
    segs = [s for s in path.split("/") if s]
    if path.startswith("/v1/auth") or ".." in path:
        return False
    if role == "student":
        if any(STAFF_SEGMENT.match(s) for s in segs):
            return False
        if len(segs) > 1 and segs[1] == "students":      # other students' records
            return False
    return True


def permitted(owner: Owner, method: str, path: str) -> bool:
    if owner.role == "public":
        return is_public_allowed(method, path)
    return role_allows(owner.role, method, path)


# ----------------------------- OpenAPI -> tools -----------------------------
def _resolve(spec: dict, node, depth: int = 0):
    while isinstance(node, dict) and "$ref" in node and depth < 10:
        cur = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(cur, dict) else {}
        node, depth = cur, depth + 1
    return node


def simplify(spec: dict, schema, depth: int = 0) -> dict:
    """A small JSON schema the model can follow (no refs, no exotic keywords)."""
    s = _resolve(spec, schema)
    if not isinstance(s, dict) or depth > 4:
        return {"type": "string"}
    for key in ("anyOf", "oneOf"):
        if key in s:
            options = [o for o in s[key] if _resolve(spec, o).get("type") != "null"]
            if options:
                return simplify(spec, options[0], depth + 1)
    if "allOf" in s:
        merged: dict = {"type": "object", "properties": {}, "required": []}
        for part in s["allOf"]:
            r = _resolve(spec, part)
            merged["properties"].update(r.get("properties", {}))
            merged["required"] += r.get("required", [])
        s = merged
    t = s.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    out: dict = {}
    if "enum" in s:
        out["enum"] = s["enum"]
        t = t or "string"
    if t == "object" or "properties" in s:
        out["type"] = "object"
        out["properties"] = {k: simplify(spec, v, depth + 1) for k, v in s.get("properties", {}).items()}
        if s.get("required"):
            out["required"] = list(dict.fromkeys(s["required"]))
    elif t == "array":
        out["type"] = "array"
        out["items"] = simplify(spec, s.get("items", {}), depth + 1)
    else:
        out["type"] = t if t in ("string", "integer", "number", "boolean") else "string"
    desc = (s.get("description") or "")[:100]
    fmt = s.get("format")
    if fmt in ("date", "date-time", "time", "email"):
        desc = f"{desc} (format: {fmt})".strip()
    if desc:
        out["description"] = desc
    return out


def tool_name(method: str, template: str) -> str:
    parts = [p.strip("{}") for p in template.split("/") if p and p != "v1"]
    name = re.sub(r"[^a-zA-Z0-9_]", "_", f"{method.lower()}_" + "_".join(parts))
    return name[:60]


def build_tools(spec: dict) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    for template, item in (spec.get("paths") or {}).items():
        if not template.startswith("/v1") or template.startswith("/v1/auth") or template.endswith("/file"):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            body_schema, body_required = None, []
            rb = _resolve(spec, op.get("requestBody") or {})
            if rb:
                content = rb.get("content", {})
                if "application/json" not in content:
                    continue                                   # uploads etc. are out of scope
                body_schema = _resolve(spec, content["application/json"].get("schema", {}))

            props: dict = {}
            required: list = []
            arg_map: dict = {}
            params = [_resolve(spec, p) for p in item.get("parameters", []) + op.get("parameters", [])]
            for p in params:
                if p.get("in") not in ("path", "query"):
                    continue
                props[p["name"]] = simplify(spec, p.get("schema", {}))
                arg_map[p["name"]] = (p["in"], p["name"])
                if p.get("required") or p["in"] == "path":
                    required.append(p["name"])

            body_mode = None
            if body_schema:
                flat = simplify(spec, body_schema)
                if flat.get("type") == "object" and flat.get("properties"):
                    body_mode = "flat"
                    for k, v in flat["properties"].items():
                        arg = k if k not in props else f"body_{k}"
                        props[arg] = v
                        arg_map[arg] = ("body", k)
                        if k in flat.get("required", []):
                            required.append(arg)
                else:
                    body_mode = "raw"
                    props["body"] = flat
                    arg_map["body"] = ("rawbody", "body")
                    required.append("body")

            write = method != "get"
            if write:
                props["user_confirmed"] = {
                    "type": "boolean",
                    "description": "true ONLY after the user clearly said yes to these exact details"}
                required.append("user_confirmed")

            summary = (op.get("summary") or op.get("description") or f"{method.upper()} {template}")
            summary = re.sub(r"\s+", " ", summary).strip()[:150]
            name = tool_name(method, template)
            n = 2
            while name in tools:
                name = f"{tool_name(method, template)[:56]}_{n}"
                n += 1
            tools[name] = Tool(
                name=name, method=method.upper(), template=template,
                description=f"{summary} [{method.upper()} {template}]",
                schema={"type": "object", "properties": props, "required": list(dict.fromkeys(required))},
                arg_map=arg_map, body_mode=body_mode, write=write)
    return tools


def fallback_tools() -> dict[str, Tool]:
    """Used only if the OpenAPI spec cannot be downloaded."""
    def simple(name, template, desc):
        return Tool(name, "GET", template, desc, {"type": "object", "properties": {}})
    tools = [
        simple("get_college", "/v1/college", "College details"),
        simple("get_courses", "/v1/courses", "List courses with eligibility, fees and seats"),
        simple("get_today", "/v1/today", "Today's date and campus information"),
        simple("get_placements_drives", "/v1/placements/drives", "Upcoming placement drives"),
        simple("get_me_attendance", "/v1/me/attendance", "The logged-in person's attendance"),
        Tool("post_admissions_applications", "POST", "/v1/admissions/applications",
             "Submit an admission application with the applicant's details",
             {"type": "object", "required": ["body", "user_confirmed"], "properties": {
                 "body": {"type": "object", "description": "Applicant fields such as name, email, phone, course"},
                 "user_confirmed": {"type": "boolean"}}},
             arg_map={"body": ("rawbody", "body")}, body_mode="raw", write=True),
        Tool("call_college_api", "ANY", "", "Call any college endpoint under /v1/ when no other tool fits",
             {"type": "object", "required": ["method", "path"], "properties": {
                 "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                 "path": {"type": "string", "description": "Path such as /v1/me/timetable"},
                 "query": {"type": "object"}, "body": {"type": "object"},
                 "user_confirmed": {"type": "boolean", "description": "Required true for non-GET"}}},
             generic=True),
    ]
    return {t.name: t for t in tools}


class ToolRegistry:
    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}
        self.source = "none"
        self.loaded_at = 0.0
        self._lock = asyncio.Lock()

    async def ensure(self, force: bool = False) -> None:
        async with self._lock:
            ttl = SPEC_TTL if self.source == "openapi" else 30
            if self.tools and not force and time.time() - self.loaded_at < ttl:
                return
            spec = await college.openapi()
            self.loaded_at = time.time()
            built = build_tools(spec) if spec else {}
            if built:
                self.tools, self.source = built, "openapi"
            elif not self.tools or self.source != "openapi":
                self.tools, self.source = fallback_tools(), "fallback"

    def for_owner(self, owner: Owner) -> tuple[dict[str, Tool], list[dict]]:
        allowed = {}
        for name, t in self.tools.items():
            if t.generic:
                if owner.role != "public":
                    allowed[name] = t
            elif permitted(owner, t.method, t.template):
                allowed[name] = t
        return allowed, [t.openai() for t in allowed.values()]


registry = ToolRegistry()


# ----------------------------- execution -----------------------------
def shrink(data, depth: int = 0):
    """Keep tool results small so they fit the model's token budget."""
    if isinstance(data, list):
        items = [shrink(x, depth + 1) for x in data[:30]]
        if len(data) > 30:
            items.append(f"... {len(data) - 30} more items not shown")
        return items
    if isinstance(data, dict):
        return {k: shrink(v, depth + 1) for k, v in list(data.items())[:60]}
    if isinstance(data, str) and len(data) > 500:
        return data[:500] + "..."
    return data


def _idem(conv_id: str, name: str, parts) -> str:
    raw = f"{conv_id}|{name}|{json.dumps(parts, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _outcome(res: dict, owner: Owner) -> dict:
    status, data = res["status"], res["data"]
    if status == 401 and owner.role != "public":
        return {"auth_expired": True, "error": "The login session expired. Ask the user to log in again."}
    if 200 <= status < 300:
        return {"status": status, "data": shrink(data)}
    err = data.get("error") if isinstance(data, dict) and "error" in data else data
    return {"status": status, "error": shrink(err), "message": error_message(data)}


async def execute(tool: Tool, args: dict, owner: Owner, conv_id: str) -> dict:
    if not isinstance(args, dict):
        return {"error": "Arguments must be a JSON object."}
    confirmed = args.get("user_confirmed") is True

    if tool.generic:
        method = str(args.get("method", "")).upper()
        path = str(args.get("path", ""))
        if not path.startswith("/v1/") or "?" in path or method not in HTTP_METHODS_UP:
            return {"error": "Path must start with /v1/ and method must be valid."}
        if not permitted(owner, method, path):
            return {"error": "You are not allowed to use this endpoint for this user."}
        if method != "GET" and not confirmed:
            return {"error": "not_confirmed", "message": "Read the details back and get a clear yes first."}
        body = args.get("body") if method != "GET" else None
        idem = _idem(conv_id, tool.name, [method, path, body]) if method != "GET" else None
        res = await college.request(method, path, token=owner.token, params=args.get("query") or None,
                                    json_body=body, idem=idem)
        return _outcome(res, owner)

    if not permitted(owner, tool.method, tool.template):
        return {"error": "You are not allowed to use this action for this user."}
    if tool.write and not confirmed:
        return {"error": "not_confirmed",
                "message": "Read the details back to the user and get a clear yes before submitting."}

    path_vals, query, body = {}, {}, {}
    for arg, val in args.items():
        where, real = tool.arg_map.get(arg, (None, None))
        if where == "path":
            path_vals[real] = val
        elif where == "query" and val not in (None, ""):
            query[real] = val
        elif where == "body" and val is not None:
            body[real] = val
        elif where == "rawbody":
            body = val
    missing = [p for p in re.findall(r"\{([^}]+)\}", tool.template) if p not in path_vals]
    if missing:
        return {"error": f"Missing required value(s): {', '.join(missing)}"}

    path = re.sub(r"\{([^}]+)\}", lambda m: quote(str(path_vals[m.group(1)]), safe=""), tool.template)
    json_body = body if tool.body_mode else None
    idem = _idem(conv_id, tool.name, [path, query, json_body]) if tool.write else None
    res = await college.request(tool.method, path, token=owner.token if owner.role != "public" else None,
                                params=query or None, json_body=json_body, idem=idem)
    return _outcome(res, owner)


HTTP_METHODS_UP = tuple(m.upper() for m in HTTP_METHODS)
