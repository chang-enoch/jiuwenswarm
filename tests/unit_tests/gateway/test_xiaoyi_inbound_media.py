# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""手机入站附件：save_dir 落盘 + 入站 GET 代理分流。"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils import media as media_mod
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.media import (
    MediaFile,
    _proxy_for_url,
    download_and_save_media,
    download_and_save_media_list,
)


class _FakeResponse:
    def __init__(self, body: bytes = b"ok") -> None:
        self.status = 200
        self.headers = {"content-type": "text/plain"}
        self._body = body

    def raise_for_status(self) -> None:
        return None

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeSession:
    last_get: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    def get(self, url: str, **kwargs):
        type(self).last_get = {"url": url, **kwargs}
        return _FakeResponse()


def test_proxy_for_public_host(monkeypatch):
    monkeypatch.setenv("CLAW_HTTP_PROXY", "http://127.0.0.1:7890")
    assert _proxy_for_url("https://obs.example.com/a.md") == "http://127.0.0.1:7890"


def test_proxy_for_loopback_and_ip_is_direct(monkeypatch):
    monkeypatch.setenv("CLAW_HTTP_PROXY", "http://127.0.0.1:7890")
    assert _proxy_for_url("http://127.0.0.1:8080/a.md") is None
    assert _proxy_for_url("http://localhost/a.md") is None
    assert _proxy_for_url("http://10.0.0.8/a.md") is None


@pytest.mark.asyncio
async def test_fetch_from_url_passes_proxy_for_public_host(monkeypatch):
    monkeypatch.setenv("CLAW_HTTP_PROXY", "http://127.0.0.1:7890")
    _FakeSession.last_get = None
    monkeypatch.setattr(media_mod.aiohttp, "ClientSession", _FakeSession)
    await media_mod._fetch_from_url("https://obs.example.com/a.md", 1000, 5000)
    assert _FakeSession.last_get is not None
    assert _FakeSession.last_get["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_fetch_from_url_skips_proxy_for_loopback(monkeypatch):
    monkeypatch.setenv("CLAW_HTTP_PROXY", "http://127.0.0.1:7890")
    _FakeSession.last_get = None
    monkeypatch.setattr(media_mod.aiohttp, "ClientSession", _FakeSession)
    await media_mod._fetch_from_url("http://127.0.0.1:9/a.md", 1000, 5000)
    assert _FakeSession.last_get is not None
    assert "proxy" not in _FakeSession.last_get


@pytest.mark.asyncio
async def test_download_and_save_media_honors_save_dir(tmp_path, monkeypatch):
    async def fake_fetch(*_a, **_k):
        return b"hello", "text/plain"

    monkeypatch.setattr(media_mod, "_fetch_from_url", fake_fetch)
    dest = tmp_path / "ws"
    dest.mkdir()
    result = await download_and_save_media(
        "https://example.com/a.txt",
        "text/plain",
        "a.txt",
        save_dir=str(dest),
    )
    assert Path(result.path) == dest / "a.txt"
    assert (dest / "a.txt").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_download_and_save_media_list_forwards_save_dir(tmp_path, monkeypatch):
    async def fake_fetch(*_a, **_k):
        return b"x", "application/pdf"

    monkeypatch.setattr(media_mod, "_fetch_from_url", fake_fetch)
    dest = tmp_path / "ws"
    dest.mkdir()
    results = await download_and_save_media_list(
        [MediaFile(uri="https://example.com/a.pdf", mime_type="application/pdf", name="a.pdf")],
        save_dir=str(dest),
    )
    assert len(results) == 1
    assert Path(results[0].path) == dest / "a.pdf"
    assert (dest / "a.pdf").read_bytes() == b"x"
