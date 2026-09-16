"""Regression tests for restart-safe session context warmup."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.agents.harness.common import session_ops_service


@pytest.mark.asyncio
async def test_warmup_excludes_the_current_request_from_restored_history(monkeypatch):
    """A cold-start turn must not restore its just-persisted user message."""
    context_engine = SimpleNamespace(
        get_context=MagicMock(return_value=None),
        create_context=AsyncMock(),
    )
    deep_agent = SimpleNamespace(
        react_agent=SimpleNamespace(context_engine=context_engine, _config=None),
        card=None,
    )
    records = [
        {"request_id": "previous", "role": "user", "content": "earlier question"},
        {"request_id": "current", "role": "user", "content": "current question"},
    ]
    restored: list[dict[str, object]] = []

    monkeypatch.setattr(session_ops_service, "history_exists", lambda _session_id: True)
    monkeypatch.setattr(session_ops_service, "load_history_records", lambda _session_id: records)
    monkeypatch.setattr(session_ops_service, "resolve_live_agent_session", lambda *_args: object())

    def build_context_messages(history_records):
        restored.extend(history_records)
        return [object()], 0

    monkeypatch.setattr(
        session_ops_service,
        "_build_context_messages_from_history",
        build_context_messages,
    )

    result = await session_ops_service.warmup_session_context(
        deep_agent=deep_agent,
        session_id="session-1",
        exclude_request_id="current",
    )

    assert result is True
    assert [record["request_id"] for record in restored] == ["previous"]
    context_engine.create_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_warmup_keeps_all_history_without_a_current_request(monkeypatch):
    """Non-chat callers retain the original full-history recovery behavior."""
    context_engine = SimpleNamespace(
        get_context=MagicMock(return_value=None),
        create_context=AsyncMock(),
    )
    deep_agent = SimpleNamespace(
        react_agent=SimpleNamespace(context_engine=context_engine, _config=None),
        card=None,
    )
    records = [
        {"request_id": "previous", "role": "user", "content": "earlier question"},
        {"request_id": "current", "role": "user", "content": "current question"},
    ]
    restored: list[dict[str, object]] = []

    monkeypatch.setattr(session_ops_service, "history_exists", lambda _session_id: True)
    monkeypatch.setattr(session_ops_service, "load_history_records", lambda _session_id: records)
    monkeypatch.setattr(session_ops_service, "resolve_live_agent_session", lambda *_args: object())

    def build_context_messages(history_records):
        restored.extend(history_records)
        return [object()], 0

    monkeypatch.setattr(
        session_ops_service,
        "_build_context_messages_from_history",
        build_context_messages,
    )

    result = await session_ops_service.warmup_session_context(
        deep_agent=deep_agent,
        session_id="session-1",
    )

    assert result is True
    assert [record["request_id"] for record in restored] == ["previous", "current"]
    context_engine.create_context.assert_awaited_once()
