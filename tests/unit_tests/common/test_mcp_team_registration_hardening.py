# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for team MCP registration hardening.

Covers:
1. ``filter_unreachable_mcp_servers`` — preflight filter used at the team
   spec assembly boundary (drops dead HTTP servers, passes stdio through,
   caches verdicts, fail-open on probe errors).
2. ``apply_mcp_register_failsoft_patch`` — per-server degrade of openjiuwen's
   fail-fast ``DeepAgent._register_pending_mcps``.
3. ``TeamManager._preflight_filter_team_mcps`` — spec.mcps mutation wiring.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common import mcp_config as mcp_config_mod
from jiuwenswarm.common.mcp_config import filter_unreachable_mcp_servers
from jiuwenswarm.common import mcp_register_failsoft_patch as failsoft_mod


def _cfg(server_id: str, server_path: str, client_type: str = "http") -> SimpleNamespace:
    return SimpleNamespace(
        server_id=server_id,
        server_name=server_id,
        server_path=server_path,
        client_type=client_type,
        model_dump=lambda: {"server_id": server_id, "server_path": server_path},
    )


class TestFilterUnreachableMcpServers:
    async def test_unreachable_http_dropped_stdio_kept(self, monkeypatch):
        async def fake_probe(cfg, *, timeout=3.0):
            if "dead" in cfg.server_path:
                return False, "tcp connect failed"
            return True, ""

        monkeypatch.setattr(mcp_config_mod, "preflight_mcp_server_reachable", fake_probe)
        dead = _cfg("dead", "http://dead:1/mcp")
        alive = _cfg("alive", "http://alive:2/mcp")
        local = _cfg("local", "stdio://local", client_type="stdio")

        kept, dropped = await filter_unreachable_mcp_servers(
            [dead, alive, local], cache={}
        )
        assert kept == [alive, local]
        assert [cfg for cfg, _ in dropped] == [dead]
        assert "tcp connect failed" in dropped[0][1]

    async def test_verdicts_cached_by_server_path(self, monkeypatch):
        calls = []

        async def fake_probe(cfg, *, timeout=3.0):
            calls.append(cfg.server_path)
            return False, "unreachable"

        monkeypatch.setattr(mcp_config_mod, "preflight_mcp_server_reachable", fake_probe)
        cache: dict = {}
        cfg = _cfg("dead", "http://dead:1/mcp")

        await filter_unreachable_mcp_servers([cfg], cache=cache)
        await filter_unreachable_mcp_servers([cfg], cache=cache)
        assert calls == ["http://dead:1/mcp"]  # second call hit the cache

    async def test_probe_exception_is_fail_open(self, monkeypatch):
        async def exploding_probe(cfg, *, timeout=3.0):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            mcp_config_mod, "preflight_mcp_server_reachable", exploding_probe
        )
        cfg = _cfg("x", "http://x:1/mcp")
        kept, dropped = await filter_unreachable_mcp_servers([cfg], cache={})
        assert kept == [cfg]
        assert dropped == []

    async def test_empty_list(self):
        assert await filter_unreachable_mcp_servers([], cache={}) == ([], [])

    async def test_expired_entries_purged_and_reprobed(self, monkeypatch):
        calls = []

        async def fake_probe(cfg, *, timeout=3.0):
            calls.append(cfg.server_path)
            return True, ""

        monkeypatch.setattr(mcp_config_mod, "preflight_mcp_server_reachable", fake_probe)
        now = mcp_config_mod.time.monotonic()
        cache = {
            "http://stale:1/mcp": (False, "old verdict", now - 3600),  # expired
        }
        cfg = _cfg("stale", "http://stale:1/mcp")
        kept, dropped = await filter_unreachable_mcp_servers([cfg], cache=cache)
        # Expired verdict must not be honored: re-probed and kept.
        assert kept == [cfg]
        assert calls == ["http://stale:1/mcp"]
        assert list(cache) == ["http://stale:1/mcp"]  # replaced, not accumulated

    async def test_cache_capped_oldest_first(self, monkeypatch):
        async def fake_probe(cfg, *, timeout=3.0):
            return True, ""

        monkeypatch.setattr(mcp_config_mod, "preflight_mcp_server_reachable", fake_probe)
        now = mcp_config_mod.time.monotonic()
        cap = mcp_config_mod.PREFLIGHT_CACHE_MAX_ENTRIES
        cache = {
            f"http://old-{i}:1/mcp": (True, "", now - 10 - i) for i in range(cap)
        }
        cfg = _cfg("new", "http://new:1/mcp")
        await filter_unreachable_mcp_servers([cfg], cache=cache)
        assert len(cache) <= cap
        assert "http://new:1/mcp" in cache
        assert "http://old-255:1/mcp" not in cache  # oldest evicted


def _fake_result(is_err: bool, msg: str = "err"):
    return SimpleNamespace(is_err=lambda: is_err, msg=lambda: msg)


@pytest.fixture()
def patched_deep_agent(monkeypatch):
    """Apply the fail-soft patch with a fake resource_mgr; restore after."""
    from openjiuwen.core.runner.runner import Runner
    from openjiuwen.harness.deep_agent import DeepAgent

    original = DeepAgent._register_pending_mcps
    original_patched_flag = failsoft_mod._PATCHED
    failsoft_mod._PATCHED = False

    resource_mgr = MagicMock()
    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr)

    failsoft_mod.apply_mcp_register_failsoft_patch()
    try:
        yield resource_mgr
    finally:
        DeepAgent._register_pending_mcps = original
        failsoft_mod._PATCHED = original_patched_flag


def _fake_agent(mcps):
    return SimpleNamespace(
        _deep_config=SimpleNamespace(mcps=mcps),
        card=SimpleNamespace(id="agent-1"),
        ability_manager=MagicMock(),
    )


class TestMcpRegisterFailsoftPatch:
    def test_patch_is_idempotent(self):
        from openjiuwen.harness.deep_agent import DeepAgent

        original = DeepAgent._register_pending_mcps
        original_patched_flag = failsoft_mod._PATCHED
        failsoft_mod._PATCHED = False
        try:
            failsoft_mod.apply_mcp_register_failsoft_patch()
            first = DeepAgent._register_pending_mcps
            failsoft_mod.apply_mcp_register_failsoft_patch()
            assert DeepAgent._register_pending_mcps is first
        finally:
            DeepAgent._register_pending_mcps = original
            failsoft_mod._PATCHED = original_patched_flag

    async def test_add_failure_skips_server_without_raising(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        patched_deep_agent.get_mcp_server_config.return_value = None
        patched_deep_agent.add_mcp_server = AsyncMock(
            return_value=_fake_result(True, "connect refused")
        )
        agent = _fake_agent([_cfg("bad", "http://bad:1/mcp")])

        await DeepAgent._register_pending_mcps(agent)  # must not raise
        agent.ability_manager.add.assert_not_called()

    async def test_add_exception_skips_server_without_raising(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        patched_deep_agent.get_mcp_server_config.return_value = None
        patched_deep_agent.add_mcp_server = AsyncMock(side_effect=RuntimeError("boom"))
        agent = _fake_agent([_cfg("bad", "stdio://bad", client_type="stdio")])

        await DeepAgent._register_pending_mcps(agent)  # must not raise
        agent.ability_manager.add.assert_not_called()

    async def test_success_registers_ability(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        patched_deep_agent.get_mcp_server_config.return_value = None
        patched_deep_agent.add_mcp_server = AsyncMock(return_value=_fake_result(False))
        cfg = _cfg("ok", "http://ok:1/mcp")
        agent = _fake_agent([cfg])

        await DeepAgent._register_pending_mcps(agent)
        agent.ability_manager.add.assert_called_once_with(cfg)

    async def test_conflicting_server_id_keeps_existing(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        patched_deep_agent.get_mcp_server_config.return_value = _cfg(
            "dup", "http://other:9/mcp"
        )
        agent = _fake_agent([_cfg("dup", "http://dup:1/mcp")])

        await DeepAgent._register_pending_mcps(agent)  # stock raised here
        patched_deep_agent.add_mcp_server.assert_not_called()
        agent.ability_manager.add.assert_not_called()

    async def test_same_config_retags_and_adds_ability(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        cfg = _cfg("same", "http://same:1/mcp")
        patched_deep_agent.get_mcp_server_config.return_value = cfg
        patched_deep_agent.add_resource_tag.return_value = _fake_result(False)
        patched_deep_agent.get_mcp_tool_ids.return_value = ["tool-1"]
        agent = _fake_agent([cfg])

        await DeepAgent._register_pending_mcps(agent)
        agent.ability_manager.add.assert_called_once_with(cfg)

    async def test_ability_failure_does_not_raise(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        patched_deep_agent.get_mcp_server_config.return_value = None
        patched_deep_agent.add_mcp_server = AsyncMock(return_value=_fake_result(False))
        agent = _fake_agent([_cfg("ok", "http://ok:1/mcp")])
        agent.ability_manager.add.side_effect = RuntimeError("ability boom")

        await DeepAgent._register_pending_mcps(agent)  # must not raise

    async def test_one_bad_server_does_not_block_others(self, patched_deep_agent):
        from openjiuwen.harness.deep_agent import DeepAgent

        patched_deep_agent.get_mcp_server_config.return_value = None
        patched_deep_agent.add_mcp_server = AsyncMock(
            side_effect=[_fake_result(True, "dead"), _fake_result(False)]
        )
        good = _cfg("good", "http://good:1/mcp")
        agent = _fake_agent([_cfg("bad", "http://bad:1/mcp"), good])

        await DeepAgent._register_pending_mcps(agent)
        agent.ability_manager.add.assert_called_once_with(good)


class TestTeamManagerPreflightFilter:
    async def test_dropped_servers_removed_from_member_specs(self, monkeypatch):
        from jiuwenswarm.agents.harness.team.team_manager import TeamManager

        dead = _cfg("dead", "http://dead:1/mcp")
        alive = _cfg("alive", "http://alive:2/mcp")
        leader = SimpleNamespace(mcps=[dead, alive])
        member = SimpleNamespace(mcps=[dead])
        spec = SimpleNamespace(agents={"leader": leader, "teammate": member})

        async def fake_filter(mcps):
            kept = [c for c in mcps if "dead" not in c.server_path]
            dropped = [(c, "unreachable") for c in mcps if "dead" in c.server_path]
            return kept, dropped

        monkeypatch.setattr(
            "jiuwenswarm.common.mcp_config.filter_unreachable_mcp_servers", fake_filter
        )
        await TeamManager._preflight_filter_team_mcps(spec)
        assert leader.mcps == [alive]
        assert member.mcps == []

    async def test_filter_exception_keeps_mcps(self, monkeypatch):
        from jiuwenswarm.agents.harness.team.team_manager import TeamManager

        cfg = _cfg("x", "http://x:1/mcp")
        leader = SimpleNamespace(mcps=[cfg])
        spec = SimpleNamespace(agents={"leader": leader})

        async def exploding_filter(mcps):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "jiuwenswarm.common.mcp_config.filter_unreachable_mcp_servers",
            exploding_filter,
        )
        await TeamManager._preflight_filter_team_mcps(spec)  # must not raise
        assert leader.mcps == [cfg]

    async def test_no_mcps_noop(self):
        from jiuwenswarm.agents.harness.team.team_manager import TeamManager

        spec = SimpleNamespace(agents={"leader": SimpleNamespace(mcps=None)})
        await TeamManager._preflight_filter_team_mcps(spec)
