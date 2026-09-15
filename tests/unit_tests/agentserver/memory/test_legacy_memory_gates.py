"""Regression coverage for legacy entry points leaking into Celia mode."""

import builtins
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from openjiuwen.harness.prompts import SystemPromptBuilder
from openjiuwen.harness.schema.deep_agent_spec import WorkspaceSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig

from jiuwenswarm.common import config as config_module
from jiuwenswarm.agents.harness.common import memory_rpc
from jiuwenswarm.agents.harness.common.memory.dreaming import sweeper, start_dreaming
from jiuwenswarm.agents.harness.common.memory.workspace import configure_workspace_memory
from jiuwenswarm.agents.harness.common.rails.avatar_rail import AvatarPromptRail
from jiuwenswarm.agents.harness.common.rails.memory_forbidden_rail import MemoryForbiddenRail
from jiuwenswarm.agents.harness.common.rails.permissions.owner_scopes import PermissionContext, TOOL_PERMISSION_CONTEXT
from jiuwenswarm.agents.swarm import SwarmBuildContext, register_swarm_providers, registry
from jiuwenswarm.agents.swarm.config_specs import build_member_capability_specs
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.memory_query import MemoryQueryContext, handle_memory_query
from jiuwenswarm.gateway.im_pipeline.im_inbound import IMConversationProcessor
from jiuwenswarm.server.runtime.agent_adapter import interface_code, interface_deep


@pytest.fixture
def config(monkeypatch):
    # Stale individual switches must not bypass the external engine selection.
    cfg = {"memory": {"mode": "cloud", "engine": "external", "external": {"provider": "celia"}},
           "modes": {mode: {"memory": {"enabled": True, "auto_coding_memory": True}}
                     for mode in ("agent", "code")}, "auto_memory_enabled": True}
    for module in (config_module, memory_rpc, interface_code, interface_deep):
        monkeypatch.setattr(module, "get_config", lambda: cfg)
    return cfg


@pytest.mark.parametrize("mode", ["code.team", "team.plan", "design", "design.plan"])
@pytest.mark.parametrize("role", ["leader", "teammate"])
@pytest.mark.parametrize("legacy_enabled", [False, True])
def test_member_providers_respect_legacy_switch(config, tmp_path, mode, role, legacy_enabled):
    if legacy_enabled:
        config["memory"].update(mode="local", engine="builtin")
    register_swarm_providers()
    workspace = WorkspaceSpec(root_path=str(tmp_path), language="en").build()
    configure_workspace_memory(workspace, config)
    ctx = SwarmBuildContext(config=config, workspace=workspace, mode=mode, role=role,
                           project_dir=str(tmp_path / "project"), language="en")
    specs, _ = build_member_capability_specs(config, mode, role)
    for spec in specs:
        if spec.type in (registry.CODE_CODING_MEMORY, registry.CODE_PROJECT_MEMORY):
            rail = spec.build(language="en", context=ctx)
            assert (rail is not None) is legacy_enabled
    assert (tmp_path / "coding_memory").exists() is legacy_enabled
    assert bool(ctx.extras.get("_coding_memory_rail")) is legacy_enabled


@pytest.mark.asyncio
async def test_celia_rpc_ignores_stale_files_and_never_initializes_old_index(config, tmp_path, monkeypatch):
    index = AsyncMock(side_effect=AssertionError("old index must not be initialized"))
    monkeypatch.setattr(memory_rpc, "get_memory_manager", index)
    (tmp_path / "coding_memory/project").mkdir(parents=True)
    (tmp_path / "coding_memory/project/stale.md").write_text("OLD MEMORY")
    monkeypatch.delenv("GSPD_CELIAWORK_CONFIG", raising=False)
    monkeypatch.setenv("GSPD_CELIAWORK_DATA_DIR", str(tmp_path / "celia"))
    status = await memory_rpc.handle_memory_status(str(tmp_path), "code", {"detailed": True})
    assert status["enabled"] is False and status["auto_coding_memory"] is False
    assert status["external_memory"]["database_path"] == str(tmp_path / "celia/celia_memory.db")
    assert "index" not in status
    assert (await memory_rpc.handle_memory_list(str(tmp_path), "code", {}))["files"] == []
    result = await memory_rpc.handle_memory_open(str(tmp_path), {"project_dir": str(tmp_path / "project")})
    assert result["memory_dir"] == str(tmp_path / "celia")
    assert "coding_memory_dir" not in result
    assert not (tmp_path / "memory").exists() and not (tmp_path / "celia").exists()
    index.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabling_local_and_code_memory_unmounts_existing_rails(config):
    adapter = object.__new__(interface_code.JiuwenSwarmCodeAdapter)
    rails = [object(), object(), object()]
    adapter._memory_rail, adapter._coding_memory_rail, adapter._project_memory_rail = rails
    adapter._instance = SimpleNamespace(unregister_rail=AsyncMock())
    await adapter._handle_memory_rail_by_config("code")
    assert adapter._memory_rail is adapter._coding_memory_rail is adapter._project_memory_rail is None
    assert {call.args[0] for call in adapter._instance.unregister_rail.await_args_list} == set(rails)


@pytest.mark.asyncio
async def test_switching_to_celia_removes_registered_coding_tools(config, tmp_path):
    config["memory"].update(mode="local", engine="builtin")
    card = AgentCard(id="legacy-switch", name="legacy-switch")
    agent = DeepAgent(card)
    agent.configure(DeepAgentConfig(card=card, system_prompt="Test", language="en"))
    adapter = object.__new__(interface_code.JiuwenSwarmCodeAdapter)
    adapter._instance = agent
    adapter._memory_rail = adapter._project_memory_rail = None
    rail = interface_code.create_coding_memory_rail(
        project_dir=None, agent_workspace_dir=str(tmp_path), config=config)
    adapter._coding_memory_rail = rail
    await agent.register_rail(rail)
    names = ("coding_memory_read", "coding_memory_write", "coding_memory_edit")
    try:
        assert all(agent.ability_manager.get(name) is not None for name in names)
        config["memory"].update(mode="cloud", engine="external")
        await adapter._handle_memory_rail_by_config("code")
        assert all(agent.ability_manager.get(name) is None for name in names)
        assert adapter._coding_memory_rail is None
    finally:
        if adapter._coding_memory_rail is not None:
            await agent.unregister_rail(rail)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_external_provider_switch_flushes_unmounts_and_builds_once(config):
    calls = []
    async def flush():
        calls.append("flush")
    old = SimpleNamespace(_provider=SimpleNamespace(on_session_end=flush))
    new = object()
    def build():
        calls.append("build")
        return new
    adapter = object.__new__(interface_deep.JiuWenSwarmDeepAdapter)
    adapter._external_memory_rail = old
    adapter._external_memory_rail_registered = True
    adapter._external_memory_provider = "old-celia"
    adapter._build_external_memory_rail = Mock(side_effect=build)
    adapter._instance = SimpleNamespace(
        unregister_rail=AsyncMock(side_effect=lambda rail: calls.append("unmount")),
        register_rail=AsyncMock(side_effect=lambda rail: calls.append("mount")))
    await adapter._handle_external_memory_rail_by_config()
    await adapter._handle_external_memory_rail_by_config()
    assert calls == ["flush", "unmount", "build", "mount"]
    assert adapter._external_memory_rail is new
    config["memory"]["engine"] = "none"
    await adapter._handle_external_memory_rail_by_config()
    assert adapter._external_memory_rail is None
    assert calls[-1] == "unmount"


@pytest.mark.parametrize("legacy_enabled", [False, True])
def test_phone_and_im_file_reads_follow_old_celia_switch(config, tmp_path, monkeypatch, legacy_enabled):
    if legacy_enabled:
        config["memory"]["external"]["provider"] = "old-celia"
    for filename in ("USER.md", "MEMORY.md"):
        (tmp_path / filename).write_text("LEGACY SENTINEL")
    processor = object.__new__(IMConversationProcessor)
    processor._user_profile_path = tmp_path / "USER.md"
    assert bool(processor._load_user_profile()) is legacy_enabled
    for action in ("UserMdQuery", "MemoryMdQuery"):
        result = handle_memory_query(MemoryQueryContext(action, {}, "s", "t", "m"), workspace_dir=tmp_path)
        assert (result["fileDetail"] == "LEGACY SENTINEL") is legacy_enabled
    history = Mock(return_value=[{"content": "LEGACY HISTORY"}])
    monkeypatch.setattr("jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.memory_query._history_buckets", history)
    result = handle_memory_query(MemoryQueryContext("MemoryHistory", {}, "s", "t", "m"), workspace_dir=tmp_path)
    assert bool(result) is legacy_enabled
    assert history.called is legacy_enabled
    # Files are retained for users who switch back to the old provider.
    assert (tmp_path / "USER.md").read_text() == "LEGACY SENTINEL"


@pytest.mark.asyncio
async def test_external_mode_blocks_legacy_dreaming_even_with_env_override(config, tmp_path, monkeypatch):
    monkeypatch.setenv("DREAMING_AGENT_ENABLED", "true")
    monkeypatch.setenv("DREAMING_CODE_ENABLED", "true")
    for mode in ("agent", "code"):
        assert not sweeper.DreamingConfig.load(mode).enabled
        assert await start_dreaming(str(tmp_path / "sessions"), str(tmp_path / mode), mode=mode) is None
        assert not (tmp_path / mode).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,tool,blocked", [
    (True, "memory_store", True),
    (True, "mcp_celia-memory_celia.memory_store", True),
    (True, "mcp_celia-memory_celia.memory_record_search", False),
    (False, "mcp_celia-memory_celia.memory_record_search", True),
    (False, "read_file", False),
])
async def test_avatar_permissions_cover_current_memory_tools(enabled, tool, blocked):
    token = TOOL_PERMISSION_CONTEXT.set(PermissionContext(group_digital_avatar=True, avatar_mode=True, enable_memory=enabled))
    try:
        builder = SystemPromptBuilder(language="en")
        rail = AvatarPromptRail()
        ctx = SimpleNamespace(agent=SimpleNamespace(system_prompt_builder=builder), extra={},
                              inputs=SimpleNamespace(tool_name=tool, tool_call=None))
        await rail.before_model_call(ctx)
        await rail.before_tool_call(ctx)
        assert bool(ctx.extra.get("_skip_tool")) is blocked
        assert "write_memory" not in builder.build()
    finally:
        TOOL_PERMISSION_CONTEXT.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_sensitive_filter_obeys_existing_switch_for_celia(config, enabled):
    config["memory"]["forbidden_memory_definition"] = {"enabled": enabled, "patterns": ["AUDIT_SECRET"]}
    ctx = SimpleNamespace(extra={}, inputs=SimpleNamespace(tool_name="mcp_celia-memory_celia.memory_store",
                          tool_args={"content": "AUDIT_SECRET"}, tool_call=None))
    await MemoryForbiddenRail().before_tool_call(ctx)
    assert bool(ctx.extra.get("_skip_tool")) is enabled


def test_standalone_daily_collector_respects_yaml_and_skips_invalid_config(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[4] / "jiuwenswarm/resources/agent/workspace/skills/advanced-daily-report/collectors/memory_collector.py"
    spec = importlib.util.spec_from_file_location("audit_daily_memory_collector", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    workspace = tmp_path / "agent/workspace"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "memory/MEMORY.md").write_text("LEGACY DAILY")
    (tmp_path / "config").mkdir()
    config_path = tmp_path / "config/config.yaml"
    monkeypatch.setenv("JIUWENSWARM_DATA_DIR", str(tmp_path))
    original_import = builtins.__import__
    def standalone_import(name, *args, **kwargs):
        if name.startswith("jiuwenswarm"):
            raise ImportError("standalone skill")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", standalone_import)
    for provider in ("celia", "old-celia"):
        config_path.write_text(yaml.safe_dump({"memory": {"engine": "external", "external": {"provider": provider}}}))
        result = module.MemoryCollector(workspace).collect("2026-09-15")
        assert (result.long_term_content == "LEGACY DAILY") is (provider == "old-celia")
    config_path.write_text("memory: [broken")
    assert not module.MemoryCollector(workspace).collect().long_term_content
