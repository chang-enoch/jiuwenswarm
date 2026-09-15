"""Tests for cross-platform system-operation policy construction."""

from __future__ import annotations

from jiuwenswarm.server.runtime.agent_adapter import sysop_builder


def test_build_process_policy_uses_current_user_without_unix_account_db(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sysop_builder, "grp", None)
    monkeypatch.setattr(sysop_builder, "pwd", None)
    monkeypatch.setattr(sysop_builder.getpass, "getuser", lambda: "windows-user")

    assert sysop_builder.build_process_policy() == {
        "run_as_user": "windows-user",
        "run_as_group": "windows-user",
    }
