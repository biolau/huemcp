"""Thin wrapper around the Hue CLIP v2 HTTP API with env-driven config."""
from __future__ import annotations

import os
from functools import cached_property
from typing import Any
from urllib.parse import quote

import requests
import urllib3
from dotenv import load_dotenv


class HueError(RuntimeError):
    """Raised when the bridge returns an error or a request fails."""


def _resolve_verify(raw: str | None) -> bool | str:
    if raw is None or raw == "" or raw == "0":
        return False
    if raw == "1":
        return True
    return raw


class HueClient:
    """Authenticated session against a Hue bridge's CLIP v2 API."""

    def __init__(
        self,
        host: str | None = None,
        application_key: str | None = None,
        verify: bool | str | None = None,
        timeout: float | None = None,
        user_agent: str = "HueMCP/0.1 (+local)",
    ):
        load_dotenv()
        host = host if host is not None else os.getenv("HUE_BRIDGE_HOST")
        if not host:
            raise HueError("HUE_BRIDGE_HOST is not set")
        self._host = host

        key = application_key if application_key is not None else os.getenv("HUE_APPLICATION_KEY")
        if not key:
            raise HueError("HUE_APPLICATION_KEY is not set")
        self._key = key

        self._verify: bool | str = (
            verify if verify is not None else _resolve_verify(os.getenv("HUE_VERIFY_TLS"))
        )
        self._timeout = float(timeout if timeout is not None else os.getenv("HUE_TIMEOUT", "10"))
        self._user_agent = user_agent

    @cached_property
    def session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(
            {
                "hue-application-key": self._key,
                "User-Agent": self._user_agent,
                "Accept": "application/json",
            }
        )
        s.verify = self._verify
        if self._verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        return s

    @property
    def base_url(self) -> str:
        return f"https://{self._host}"

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        try:
            resp = self.session.request(
                method, self._url(path), json=json, timeout=self._timeout
            )
        except requests.RequestException as e:
            raise HueError(f"{method} {path} failed: {e}") from e

        if resp.status_code >= 400:
            raise HueError(
                f"{method} {path} returned {resp.status_code}: {resp.text[:500]}"
            )

        try:
            body = resp.json()
        except ValueError as e:
            raise HueError(f"{method} {path} returned non-JSON: {resp.text[:200]}") from e

        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            raise HueError(f"{method} {path} bridge errors: {errors}")
        return body

    def get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", path, json=payload)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json=payload)

    # Convenience helpers --------------------------------------------------

    def list_resource(self, rtype: str) -> list[dict[str, Any]]:
        body = self.get(f"/clip/v2/resource/{quote(rtype, safe='')}")
        return list(body.get("data", []))

    def get_resource(self, rtype: str, rid: str) -> dict[str, Any] | None:
        body = self.get(
            f"/clip/v2/resource/{quote(rtype, safe='')}/{quote(rid, safe='')}"
        )
        data = body.get("data", [])
        return data[0] if data else None

    def update_resource(
        self, rtype: str, rid: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self.put(
            f"/clip/v2/resource/{quote(rtype, safe='')}/{quote(rid, safe='')}",
            payload,
        )

    def bridge_info(self) -> dict[str, Any]:
        body = self.get("/clip/v2/resource/bridge")
        data = body.get("data", [])
        return data[0] if data else {}
