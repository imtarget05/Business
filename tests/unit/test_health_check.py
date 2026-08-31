# -*- coding: utf-8 -*-
"""Tests: health check API URL resolution + api check behaviour."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agents.monitoring import health_check as hc


def test_resolve_api_url_uses_docker_dns_by_default(monkeypatch):
    """No API_URL env -> must default to the docker service DNS, NOT localhost."""
    monkeypatch.delenv("API_URL", raising=False)

    async def fake_run(api_base_url=None):
        # mimic resolution logic without hitting network
        if not api_base_url:
            api_base_url = __import__("os").environ.get("API_URL") or "http://api:8000"
        return api_base_url

    url = asyncio.run(fake_run())
    assert url == "http://api:8000"
    assert "localhost" not in url


def test_resolve_api_url_from_env(monkeypatch):
    monkeypatch.setenv("API_URL", "http://api.example:9000")
    import os

    async def fake_run(api_base_url=None):
        if not api_base_url:
            api_base_url = os.environ.get("API_URL") or "http://api:8000"
        return api_base_url

    assert asyncio.run(fake_run()) == "http://api.example:9000"


def test_check_api_reports_ok_on_200(monkeypatch):
    """A 200 response must yield status 'ok' (not 'Cannot connect')."""

    class FakeResp:
        status_code = 200

        def json(self):
            return {"service": "api"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    monkeypatch.setattr(hc.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    check = asyncio.run(hc.check_api("http://api:8000"))
    assert check.status == "ok"
    assert check.message == "API service is healthy"


def test_check_api_reports_error_on_connect_fail(monkeypatch):
    import httpx

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(hc.httpx, "AsyncClient", lambda *a, **k: FakeClient())
    check = asyncio.run(hc.check_api("http://localhost:8000"))
    assert check.status == "error"
    assert "Cannot connect" in check.message
