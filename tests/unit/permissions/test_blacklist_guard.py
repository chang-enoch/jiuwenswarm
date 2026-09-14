# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for desensitive blacklist guard in ``security_guard``."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from jiuwenclaw.agentserver.permissions import security_guard as bl


@pytest.fixture(autouse=True)
def _enable_guard(monkeypatch):
    monkeypatch.setenv("BLACKLIST_GUARD_ENABLED", "true")
    bl.clear_blacklist_cache()
    yield
    bl.clear_blacklist_cache()


@pytest.fixture
def _with_fallback(monkeypatch):
    monkeypatch.delenv("OFFICE_CLAW_LOGIN_STATS_URL", raising=False)
    bl.clear_blacklist_cache()
    with mock.patch.object(bl, "_bl_fetch_with_retry", return_value=None):
        yield


@pytest.fixture
def _with_mocked_api(monkeypatch):
    fake = {"url": ["w3.huawei.com", "www.oracle.com"], "filename": ["AT会议纪要", "喜报", "W3发文"]}
    monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_URL", "https://fake.test:8443")
    monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_TOKEN", "test-token")
    bl.clear_blacklist_cache()
    with mock.patch.object(bl, "_bl_fetch_with_retry", return_value=fake):
        yield fake


class TestCheckBlacklistFile:

    @pytest.mark.asyncio
    async def test_exact_match(self, _with_mocked_api):
        assert await bl.check_blacklist_file("D:\\docs\\喜报.md") == "喜报"

    @pytest.mark.asyncio
    async def test_fuzzy_match(self, _with_mocked_api):
        assert await bl.check_blacklist_file("D:\\docs\\AT会议纪要-2026Q3.md") == "AT会议纪要"

    @pytest.mark.asyncio
    async def test_case_insensitive(self, _with_mocked_api):
        assert await bl.check_blacklist_file("D:\\docs\\w3发文.txt") == "W3发文"

    @pytest.mark.asyncio
    async def test_no_match(self, _with_mocked_api):
        assert await bl.check_blacklist_file("D:\\docs\\design-doc.md") is None

    @pytest.mark.asyncio
    async def test_empty_path(self, _with_mocked_api):
        assert await bl.check_blacklist_file("") is None
        assert await bl.check_blacklist_file("   ") is None

    @pytest.mark.asyncio
    async def test_non_string(self, _with_mocked_api):
        assert await bl.check_blacklist_file(None) is None
        assert await bl.check_blacklist_file(123) is None

    @pytest.mark.asyncio
    async def test_dir_only(self, _with_mocked_api):
        assert await bl.check_blacklist_file("D:\\docs\\") is None


class TestCheckBlacklistUrl:

    @pytest.mark.asyncio
    async def test_domain_match(self, _with_mocked_api):
        assert await bl.check_blacklist_url("https://w3.huawei.com/some/page") == "w3.huawei.com"

    @pytest.mark.asyncio
    async def test_case_insensitive(self, _with_mocked_api):
        assert await bl.check_blacklist_url("https://WWW.ORACLE.COM/index.html") == "www.oracle.com"

    @pytest.mark.asyncio
    async def test_no_match(self, _with_mocked_api):
        assert await bl.check_blacklist_url("https://github.com/user/repo") is None

    @pytest.mark.asyncio
    async def test_empty_url(self, _with_mocked_api):
        assert await bl.check_blacklist_url("") is None
        assert await bl.check_blacklist_url("   ") is None


class TestGuardDisabled:

    @pytest.mark.asyncio
    async def test_disabled_file(self, monkeypatch, _with_mocked_api):
        monkeypatch.setenv("BLACKLIST_GUARD_ENABLED", "false")
        bl.clear_blacklist_cache()
        assert await bl.check_blacklist_file("D:\\docs\\喜报.md") is None

    @pytest.mark.asyncio
    async def test_disabled_url(self, monkeypatch, _with_mocked_api):
        monkeypatch.setenv("BLACKLIST_GUARD_ENABLED", "false")
        bl.clear_blacklist_cache()
        assert await bl.check_blacklist_url("https://w3.huawei.com/") is None


class TestFallback:

    @pytest.mark.asyncio
    async def test_empty_when_no_url(self, _with_fallback):
        assert await bl.get_blacklist() == bl._BL_EMPTY

    @pytest.mark.asyncio
    async def test_pass_through_file(self, _with_fallback):
        assert await bl.check_blacklist_file("D:\\docs\\喜报.md") is None

    @pytest.mark.asyncio
    async def test_pass_through_url(self, _with_fallback):
        assert await bl.check_blacklist_url("https://w3.huawei.com/") is None

    @pytest.mark.asyncio
    async def test_empty_when_api_fails(self, monkeypatch):
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_URL", "https://fake.invalid:9999")
        bl.clear_blacklist_cache()
        with mock.patch.object(bl, "_bl_fetch_with_retry", return_value=None):
            assert await bl.get_blacklist() == bl._BL_EMPTY


class TestCache:

    @pytest.mark.asyncio
    async def test_cache_hit(self, _with_mocked_api):
        first = await bl.get_blacklist()
        with mock.patch.object(bl, "_bl_fetch_with_retry") as m:
            second = await bl.get_blacklist()
            m.assert_not_called()
        assert first is second

    @pytest.mark.asyncio
    async def test_clear_forces_refetch(self, _with_mocked_api):
        await bl.get_blacklist()
        bl.clear_blacklist_cache()
        new = {"url": ["test.com"], "filename": ["testfile"]}
        with mock.patch.object(bl, "_bl_fetch_with_retry", return_value=new) as m:
            assert await bl.get_blacklist() == new
        assert m.call_count == 1


class TestApiFetch:

    def test_parse_success(self, monkeypatch):
        api_resp = {
            "status": 200, "success": True,
            "data": [
                {"sceneName": "url黑名单场景", "blacklist": [
                    {"enWord": None, "zhWord": "w3.huawei.com"},
                    {"enWord": None, "zhWord": "www.oracle.com"},
                ]},
                {"sceneName": "文件名黑名单场景", "blacklist": [
                    {"enWord": None, "zhWord": "AT会议纪要"},
                    {"enWord": None, "zhWord": "W3发文"},
                ]},
            ],
        }
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_URL", "https://fake.test:8443")
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_TOKEN", "t")
        bl.clear_blacklist_cache()
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps(api_resp).encode("utf-8")
        mock_resp.__enter__ = mock.Mock(return_value=mock_resp)
        mock_resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            result = bl._bl_fetch_from_api()
        assert result is not None
        assert "w3.huawei.com" in result["url"]
        assert "AT会议纪要" in result["filename"]

    def test_returns_none_on_api_error(self, monkeypatch):
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_URL", "https://fake.test:8443")
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_TOKEN", "t")
        bl.clear_blacklist_cache()
        mock_resp = mock.Mock()
        mock_resp.read.return_value = json.dumps({"status": 500, "success": False}).encode("utf-8")
        mock_resp.__enter__ = mock.Mock(return_value=mock_resp)
        mock_resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            assert bl._bl_fetch_from_api() is None

    def test_returns_none_on_network_error(self, monkeypatch):
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_URL", "https://fake.test:8443")
        monkeypatch.setenv("OFFICE_CLAW_LOGIN_STATS_TOKEN", "t")
        bl.clear_blacklist_cache()
        with mock.patch("urllib.request.urlopen", side_effect=Exception("refused")):
            assert bl._bl_fetch_from_api() is None

    def test_retry_succeeds_first(self, monkeypatch):
        monkeypatch.setattr(bl, "_BL_RETRY_DELAY_SECONDS", 0)
        data = {"url": ["x"], "filename": []}
        with mock.patch.object(bl, "_bl_fetch_from_api", return_value=data) as m:
            assert bl._bl_fetch_with_retry() == data
        assert m.call_count == 1

    def test_retry_all_fail(self, monkeypatch):
        monkeypatch.setattr(bl, "_BL_RETRY_DELAY_SECONDS", 0)
        with mock.patch.object(bl, "_bl_fetch_from_api", return_value=None) as m:
            assert bl._bl_fetch_with_retry() is None
        assert m.call_count == bl._BL_MAX_RETRIES

    def test_retry_succeeds_second(self, monkeypatch):
        monkeypatch.setattr(bl, "_BL_RETRY_DELAY_SECONDS", 0)
        data = {"url": ["x"], "filename": []}
        with mock.patch.object(bl, "_bl_fetch_from_api", side_effect=[None, data]) as m:
            assert bl._bl_fetch_with_retry() == data
        assert m.call_count == 2
