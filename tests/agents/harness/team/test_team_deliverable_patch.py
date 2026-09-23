# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for team deliverable patches."""

from __future__ import annotations

import copy
import logging
from types import SimpleNamespace

from jiuwenswarm.agents.harness.team import team_deliverable_patch as patch_module


# ---------------------------------------------------------------------------
# worker cwd patch
# ---------------------------------------------------------------------------


def test_worker_cwd_backfilled_from_base_spec(monkeypatch) -> None:
    """cwd=None from upstream is replaced by the base spec's project dir."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import (
        TeamWorkerBackend,
    )

    sentinel_ws = object()

    def fake_setup(self, member_name):  # noqa: ANN001, ANN202
        return sentinel_ws, None

    monkeypatch.setattr(TeamWorkerBackend, "_setup_worker_workspace", fake_setup)
    monkeypatch.delattr(TeamWorkerBackend, patch_module._WORKER_CWD_FLAG, raising=False)
    try:
        patch_module._patch_worker_cwd()
        backend = SimpleNamespace(_worker_base_spec=SimpleNamespace(cwd="D:/proj"))
        ws, cwd = TeamWorkerBackend._setup_worker_workspace(backend, "m")
        assert ws is sentinel_ws
        assert cwd == "D:/proj"
        # Idempotent: a second apply keeps the same wrapper.
        wrapped = TeamWorkerBackend._setup_worker_workspace
        patch_module._patch_worker_cwd()
        assert TeamWorkerBackend._setup_worker_workspace is wrapped
    finally:
        if hasattr(TeamWorkerBackend, patch_module._WORKER_CWD_FLAG):
            delattr(TeamWorkerBackend, patch_module._WORKER_CWD_FLAG)


def test_worker_cwd_worktree_path_preserved(monkeypatch) -> None:
    """A non-None cwd (worktree isolation) is passed through untouched."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import (
        TeamWorkerBackend,
    )

    def fake_setup(self, member_name):  # noqa: ANN001, ANN202
        return object(), "/tmp/worktree-x"

    monkeypatch.setattr(TeamWorkerBackend, "_setup_worker_workspace", fake_setup)
    monkeypatch.delattr(TeamWorkerBackend, patch_module._WORKER_CWD_FLAG, raising=False)
    try:
        patch_module._patch_worker_cwd()
        backend = SimpleNamespace(_worker_base_spec=SimpleNamespace(cwd="D:/proj"))
        _, cwd = TeamWorkerBackend._setup_worker_workspace(backend, "m")
        assert cwd == "/tmp/worktree-x"
    finally:
        if hasattr(TeamWorkerBackend, patch_module._WORKER_CWD_FLAG):
            delattr(TeamWorkerBackend, patch_module._WORKER_CWD_FLAG)


# ---------------------------------------------------------------------------
# workspace label patch
# ---------------------------------------------------------------------------


def test_workspace_labels_rewritten(monkeypatch) -> None:
    """Private workspace is no longer described as holding member artifacts."""
    from openjiuwen.agent_teams.prompts import messages as team_messages

    fake = copy.deepcopy(team_messages._LABELS)
    monkeypatch.setattr(team_messages, "_LABELS", fake)

    patch_module._patch_workspace_labels()

    cn = fake["cn"]["member_workspace_purpose"]
    assert "你自己的产物" not in cn
    assert "任务工作目录" in cn
    en = fake["en"]["member_workspace_purpose"]
    assert "your own artifacts" not in en
    assert "task working directory" in en
    assert "最终交付物不放这里" in fake["cn"]["team_workspace_purpose"]


def test_workspace_labels_missing_key_degrades(monkeypatch, caplog) -> None:
    """Missing label keys warn instead of raising (version drift)."""
    from openjiuwen.agent_teams.prompts import messages as team_messages

    monkeypatch.setattr(team_messages, "_LABELS", {"cn": {}, "en": {}})
    # The "jiuwenswarm" parent logger sets propagate=False, so caplog's root
    # handler never sees the record — attach its handler directly.
    patch_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger=patch_module.logger.name):
            patch_module._patch_workspace_labels()
    finally:
        patch_module.logger.removeHandler(caplog.handler)
    assert "agent-core drift" in caplog.text


# ---------------------------------------------------------------------------
# policy template patch
# ---------------------------------------------------------------------------


def test_wrap_load_template_substitutes_without_mutating_cache() -> None:
    """Targeted templates get the narrowed wording; the cached raw stays intact."""
    from openjiuwen.agent_teams.prompts import loader

    raw_before = loader.load_template("teammate_policy", "cn").content
    wrapped = patch_module._wrap_load_template(loader.load_template)

    patched = wrapped("teammate_policy", "cn")
    assert "交付文档不在此列" in patched.content
    assert "send_file_to_user" in patched.content
    assert "汇总或交付文档" not in patched.content

    patched_en = wrapped("leader_policy", "en")
    assert "user-facing final deliverables are the exception" in patched_en.content

    # The @cache'd raw template is a shared object and must stay untouched.
    assert loader.load_template("teammate_policy", "cn").content == raw_before


def test_wrap_load_template_passthrough_for_untargeted() -> None:
    """Templates outside the replacement map pass through unchanged."""
    from openjiuwen.agent_teams.prompts import loader

    wrapped = patch_module._wrap_load_template(loader.load_template)
    assert wrapped("inbound_tags", "cn") is loader.load_template("inbound_tags", "cn")


def test_wrap_load_template_ask_user_routing() -> None:
    """ask_user routing wording: leader workflow names the tool, teammates escalate."""
    from openjiuwen.agent_teams.prompts import loader

    wrapped = patch_module._wrap_load_template(loader.load_template)

    for name in ("leader_workflow", "leader_workflow_predefined", "leader_workflow_hybrid"):
        cn = wrapped(name, "cn").content
        assert "调用 `ask_user` 工具向用户提问" in cn
        assert "如有歧义先向用户提问；" not in cn
        en = wrapped(name, "en").content
        assert "call the `ask_user` tool to ask the user" in en

    cn_member = wrapped("teammate_policy", "cn").content
    assert "不能直接向用户提问" in cn_member
    assert "send_message` 给 Leader 代问" in cn_member
    en_member = wrapped("teammate_policy", "en").content
    assert "no `ask_user` tool and cannot ask the user directly" in en_member


def test_wrap_load_template_warns_on_drift(caplog) -> None:
    """Unmatched patterns warn and pass the original content through."""

    def fake_load(name, language="cn"):  # noqa: ANN001, ANN202
        return SimpleNamespace(name=name, content="unrelated content")

    wrapped = patch_module._wrap_load_template(fake_load)
    patch_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger=patch_module.logger.name):
            out = wrapped("teammate_policy", "cn")
    finally:
        patch_module.logger.removeHandler(caplog.handler)
    assert out.content == "unrelated content"
    assert "pattern not found" in caplog.text


def test_patch_policy_templates_wraps_both_sites() -> None:
    """Both loader and sections module references get wrapped, idempotently."""
    from openjiuwen.agent_teams.prompts import loader, sections

    orig_loader, orig_sections = loader.load_template, sections.load_template
    try:
        patch_module._patch_policy_templates()
        assert getattr(loader.load_template, patch_module._LOAD_TEMPLATE_FLAG, False)
        assert getattr(sections.load_template, patch_module._LOAD_TEMPLATE_FLAG, False)
        assert "交付文档不在此列" in sections.load_template("teammate_policy", "cn").content

        wrapped_loader = loader.load_template
        patch_module._patch_policy_templates()  # idempotent
        assert loader.load_template is wrapped_loader
    finally:
        loader.load_template = orig_loader
        sections.load_template = orig_sections


def test_apply_all_is_fail_soft(monkeypatch) -> None:
    """A failing sub-patch never blocks the remaining ones or raises."""
    calls: list[str] = []

    def ok() -> None:
        calls.append("ok")

    def boom() -> None:
        raise RuntimeError("simulated agent-core drift")

    monkeypatch.setattr(patch_module, "_patch_worker_cwd", boom)
    monkeypatch.setattr(patch_module, "_patch_workspace_labels", ok)
    monkeypatch.setattr(patch_module, "_patch_policy_templates", ok)

    patch_module.apply_team_deliverable_patches()

    assert calls == ["ok", "ok"]
