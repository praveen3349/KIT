"""Thin client for the KIT college system. Every call carries the participant key."""
import asyncio
import logging

import httpx

from . import config

log = logging.getLogger("kit.college")
OPENAPI_PATHS = ("/openapi.json", "/api/openapi.json", "/v1/openapi.json", "/swagger.json")


class AuthFailed(Exception):
    pass


def error_message(data) -> str:
    """Pull a human readable message out of {"error": {"message": ...}}."""
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
        if data.get("detail"):
            return str(data["detail"])[:200]
    return "Request failed."


class CollegeClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=config.COLLEGE_API_URL,
                headers={"X-Participant-Key": config.COLLEGE_API_KEY, "Accept": "application/json"},
                timeout=httpx.Timeout(15.0, connect=8.0))
        return self._client

    async def request(self, method: str, path: str, *, token: str | None = None,
                      params: dict | None = None, json_body=None, idem: str | None = None) -> dict:
        """Returns {"status": int, "data": parsed body}. Never raises for HTTP errors."""
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idem:
            headers["Idempotency-Key"] = idem
        retry_ok = method == "GET" or bool(idem)          # safe to repeat
        for attempt in range(3):
            try:
                r = await self._http().request(method, path, params=params, json=json_body, headers=headers)
            except httpx.TransportError as exc:
                log.warning("college %s %s transport error: %s", method, path, exc)
                if not retry_ok or attempt == 2:
                    break
                await asyncio.sleep(0.4 * 2 ** attempt)
                continue
            if r.status_code in (429, 502, 503, 504) and retry_ok and attempt < 2:
                await asyncio.sleep(0.4 * 2 ** attempt)
                continue
            try:
                data = r.json()
            except ValueError:
                data = r.text[:500]
            return {"status": r.status_code, "data": data}
        return {"status": 0, "data": {"error": {
            "code": "unreachable", "message": "The college system could not be reached right now."}}}

    async def download(self, path: str) -> bytes | None:
        for attempt in range(2):
            try:
                r = await self._http().get(path, headers={"Accept": "*/*"})
                return r.content if r.status_code == 200 else None
            except httpx.TransportError:
                await asyncio.sleep(0.4)
        return None

    async def openapi(self) -> dict | None:
        for path in OPENAPI_PATHS:
            res = await self.request("GET", path)
            if res["status"] == 200 and isinstance(res["data"], dict) and "paths" in res["data"]:
                return res["data"]
        return None

    async def login(self, login_id: str, password: str) -> dict:
        res = await self.request("POST", "/v1/auth/login",
                                 json_body={"login_id": login_id, "password": password})
        if res["status"] != 200 or not isinstance(res["data"], dict):
            if res["status"] in (0, 500, 502, 503, 504):
                raise ConnectionError("The college system is not reachable right now.")
            raise AuthFailed(error_message(res["data"]) if res["status"] != 200 else "Login failed.")
        return res["data"]


college = CollegeClient()
