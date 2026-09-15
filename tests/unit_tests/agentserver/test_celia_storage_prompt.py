"""Celia storage guidance follows the service config, not legacy workspace paths."""

import json
from types import SimpleNamespace

import pytest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import SystemPromptBuilder
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentManager

from jiuwenswarm.agents.harness.common.memory.external_memory_config import get_celia_database_path
from jiuwenswarm.agents.harness.common.rails import runtime_prompt_rail as runtime
from jiuwenswarm.common import utils


@pytest.fixture(autouse=True)
def isolated_storage_environment(monkeypatch, tmp_path):
    for name in ("GSPD_CELIAWORK_CONFIG", "GSPD_CELIAWORK_DATA_DIR", "GSPD_SERVICE_CWD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(utils, "get_config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime, "get_agent_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(runtime, "get_user_workspace_dir", lambda: tmp_path)


def _service_config(tmp_path, monkeypatch, path):
    config_file = tmp_path / "celia-service.json"
    config_file.write_text(json.dumps({
        "storage": {"memoryStore": {"path": path}},
        "llm": {"apiKey": "test-secret-not-for-prompt"},
    }), encoding="utf-8")
    monkeypatch.setenv("GSPD_CELIAWORK_CONFIG", str(config_file))
    return config_file


@pytest.mark.parametrize("data_dir,expected", [
    ("/var/lib/celia", "/var/lib/celia/celia_memory.db"),
    (r"C:\Users\测试用户\gausspd", r"C:\Users\测试用户\gausspd\celia_memory.db"),
])
def test_database_path_from_desktop_data_directory(monkeypatch, data_dir, expected):
    monkeypatch.setenv("GSPD_CELIAWORK_DATA_DIR", data_dir)
    assert get_celia_database_path() == expected


def test_service_config_overrides_default_filename(tmp_path, monkeypatch):
    data = tmp_path / "external-data"
    monkeypatch.setenv("GSPD_CELIAWORK_DATA_DIR", str(data))
    _service_config(tmp_path, monkeypatch, "${GSPD_CELIAWORK_DATA_DIR}/custom.db")
    assert get_celia_database_path() == str(data / "custom.db")
    assert not data.exists()


@pytest.mark.parametrize("value", [None, "", "${UNSET_CELIA_TEST_VARIABLE}/data.db", "relative.db"])
def test_unknown_service_path_does_not_fall_back_to_another_database(tmp_path, monkeypatch, value):
    monkeypatch.delenv("UNSET_CELIA_TEST_VARIABLE", raising=False)
    monkeypatch.setenv("GSPD_CELIAWORK_DATA_DIR", str(tmp_path / "misleading-default"))
    _service_config(tmp_path, monkeypatch, value)
    assert get_celia_database_path() is None


@pytest.mark.parametrize("contents", ["{invalid", "{}", "[]"])
def test_invalid_service_config_does_not_break_prompt_loading(tmp_path, monkeypatch, contents):
    path = _service_config(tmp_path, monkeypatch, "/unused.db")
    path.write_text(contents, encoding="utf-8")
    assert get_celia_database_path() is None
    path.unlink()
    assert get_celia_database_path() is None


@pytest.mark.parametrize("cwd,expected", [
    ("/var/lib/celia", "/var/lib/celia/custom.db"),
    (r"D:\Celia data", r"D:\Celia data\custom.db"),
])
def test_relative_database_is_resolved_against_service_cwd(tmp_path, monkeypatch, cwd, expected):
    _service_config(tmp_path, monkeypatch, "custom.db")
    monkeypatch.setenv("GSPD_SERVICE_CWD", cwd)
    assert get_celia_database_path() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["cn", "en"])
@pytest.mark.parametrize("provider,engine,legacy", [
    ("celia", "external", False),
    ("old-celia", "external", True),
    ("celia", "none", False),
    ("", "builtin", True),
])
async def test_storage_prompt_follows_provider_and_refreshes_without_duplication(
    tmp_path, monkeypatch, language, provider, engine, legacy,
):
    database = tmp_path / "celia-data" / "configured.db"
    config_file = _service_config(tmp_path, monkeypatch, str(database))
    config = {
        "memory": {"engine": engine, "mode": "local", "external": {
            "provider": provider, "celia": {"db_path": "/legacy-client-only.db"},
        }},
        "modes": {"agent": {"memory": {"enabled": True}}},
    }
    monkeypatch.setattr(runtime, "get_config", lambda: config)
    builder = SystemPromptBuilder(language=language)
    agent = SimpleNamespace(system_prompt_builder=builder, prompt_attachment_manager=PromptAttachmentManager())
    rail = runtime.RuntimePromptRail(language=language, channel="desktop")
    rail.init(agent)
    ctx = AgentCallbackContext(agent=agent, session=SimpleNamespace(get_session_id=lambda: "storage-test"))
    await rail.before_model_call(ctx)
    prompt = builder.build()

    assert (f"{tmp_path / 'workspace'}/memory" in prompt) is legacy
    assert ("USER.md" in prompt) is legacy
    assert ("保存身份、记忆" in prompt or "stores identity, memory" in prompt) is legacy
    assert "test-secret-not-for-prompt" not in prompt
    assert "/legacy-client-only.db" not in prompt
    if provider == "celia" and engine == "external":
        assert str(database) in prompt
        assert "通过 Celia 记忆工具检索和更新" in prompt or "through the Celia memory tools" in prompt
        assert not database.parent.exists()
        assert "memory/MEMORY.md" not in prompt and "daily_memory" not in prompt
        config_file.write_text(json.dumps({"storage": {"memoryStore": {"path": str(database.with_name('new.db'))}}}))
        await rail.before_model_call(ctx)
        refreshed = builder.build()
        assert str(database) not in refreshed
        assert refreshed.count(str(database.with_name('new.db'))) == 1
        config["memory"]["engine"] = "none"
        await rail.before_model_call(ctx)
        assert "Celia" not in builder.build()
    else:
        assert str(database) not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["cn", "en"])
async def test_missing_location_is_explicit_without_guessing(tmp_path, monkeypatch, language):
    monkeypatch.setattr(runtime, "get_config", lambda: {
        "memory": {"engine": "external", "external": {"provider": "celia"}},
    })
    builder = SystemPromptBuilder(language=language)
    agent = SimpleNamespace(system_prompt_builder=builder, prompt_attachment_manager=PromptAttachmentManager())
    rail = runtime.RuntimePromptRail(language=language, channel="desktop")
    rail.init(agent)
    await rail.before_model_call(AgentCallbackContext(agent=agent))
    prompt = builder.build()
    assert "未提供可确认的 Celia 数据库路径" in prompt or "not provided a confirmed Celia database path" in prompt
    assert "celia_memory.db" not in prompt
    assert f"{tmp_path / 'workspace'}/memory" not in prompt
