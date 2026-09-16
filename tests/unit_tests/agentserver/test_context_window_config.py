from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_code
from jiuwenswarm.server.runtime.agent_adapter.interface_code import (
    JiuwenSwarmCodeAdapter,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    _deep_agent_context_engine_config,
    _resolve_completion_timeout,
)


def test_deep_agent_context_engine_config_forwards_context_window_tokens():
    config = _deep_agent_context_engine_config(
        {"context_engine_config": {"context_window_tokens": "123456"}}
    )

    assert config.context_window_tokens == 123456


def test_deep_agent_context_engine_config_ignores_invalid_context_window_tokens():
    config = _deep_agent_context_engine_config(
        {"context_engine_config": {"context_window_tokens": "not-a-number"}}
    )

    assert config.context_window_tokens is None


def test_resolve_completion_timeout_defaults_to_3600_when_missing():
    assert _resolve_completion_timeout({}) == 3600.0
    assert _resolve_completion_timeout(None) == 3600.0


def test_resolve_completion_timeout_null_and_zero_mean_unlimited():
    assert _resolve_completion_timeout({"completion_timeout": None}) is None
    assert _resolve_completion_timeout({"completion_timeout": 0}) is None
    assert _resolve_completion_timeout({"completion_timeout": 0.0}) is None
    assert _resolve_completion_timeout({"completion_timeout": ""}) is None
    assert _resolve_completion_timeout({"completion_timeout": "0"}) is None


def test_resolve_completion_timeout_positive_seconds():
    assert _resolve_completion_timeout({"completion_timeout": 6000}) == 6000.0
    assert _resolve_completion_timeout({"completion_timeout": "600"}) == 600.0


def test_resolve_completion_timeout_invalid_falls_back_to_3600():
    assert _resolve_completion_timeout({"completion_timeout": "not-a-number"}) == 3600.0


@pytest.mark.asyncio
async def test_code_adapter_forwards_context_window_tokens(tmp_path, monkeypatch):
    config_base = {
        "react": {
            "context_engine_config": {
                "context_window_tokens": "123456",
            },
        },
    }
    monkeypatch.setattr(interface_code, "get_config", lambda: config_base)
    monkeypatch.setattr(interface_code, "get_agent_workspace_dir", lambda: tmp_path)

    created_instance = MagicMock(ensure_initialized=AsyncMock())
    adapter = JiuwenSwarmCodeAdapter()

    with (
        patch.object(adapter, "set_checkpoint", AsyncMock()),
        patch.object(adapter, "_skip_own_instance_build", return_value=False),
        patch.object(adapter, "_refresh_multimodal_configs"),
        patch.object(adapter, "_create_model", return_value=object()),
        patch.object(adapter, "_get_tool_cards", AsyncMock(return_value=[])),
        patch.object(adapter, "_build_agent_rails", return_value=[]),
        patch.object(adapter, "_create_sys_operation", return_value=MagicMock()),
        patch.object(adapter, "_build_configured_subagents", return_value=(None, False)),
        patch.object(adapter, "_ensure_project_gitignore_agent_history"),
        patch.object(adapter, "_seed_runtime_cwd"),
        patch.object(adapter, "_register_mcp_servers_from_config", AsyncMock()),
        patch.object(adapter, "load_user_rails", AsyncMock()),
        patch.object(interface_code, "create_deep_agent", return_value=created_instance) as create_agent,
    ):
        await adapter.create_instance()

    context_config = create_agent.call_args.kwargs["context_engine_config"]
    assert context_config.context_window_tokens == 123456
