# coding: utf-8
"""方向二（team → 默认模式）净化与指纹 fallback 单测。"""

from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.common import session_ops_service as sos


def _user(rid, text, ts):
    return {"role": "user", "request_id": rid, "content": text, "timestamp": ts}


def _final(rid, text, ts, **extra):
    record = {
        "role": "assistant",
        "request_id": rid,
        "event_type": "chat.final",
        "content": text,
        "timestamp": ts,
    }
    record.update(extra)
    return record


def _member_tool(rid, ts):
    return {
        "role": "assistant",
        "request_id": rid,
        "event_type": "chat.tool_call",
        "content": "",
        "tool_call": {"name": "write_file", "arguments": "{}", "tool_call_id": "tc1"},
        "timestamp": ts,
    }


def _member_tool_result(rid, ts):
    return {
        "role": "assistant",
        "request_id": rid,
        "event_type": "chat.tool_result",
        "content": "",
        "tool_call_id": "tc1",
        "result": "ok",
        "timestamp": ts,
    }


def test_converter_drop_member_internals():
    records = [
        _user("r1", "帮我写报告", 1.0),
        _member_tool("member-tool-bob-1", 2.0),
        _member_tool_result("member-tool-bob-1", 3.0),
        _final("member-final-bob-2", "成员产出正文" * 30, 4.0, member_name="bob"),
        _final("r1", "leader 汇总答复", 5.0),
    ]

    messages, skipped = sos._build_context_messages_from_history(
        records, drop_member_internals=True
    )

    kinds = [type(m).__name__ for m in messages]
    # 成员工具事件不成 ToolMessage/AssistantMessage(tool_calls)
    assert "ToolMessage" not in kinds
    # 成员 final 折叠为归属注记（截断 200 字）
    notes = [m for m in messages if type(m).__name__ == "AssistantMessage"
             and str(getattr(m, "content", "")).startswith("（团队成员 bob 的产出）")]
    assert len(notes) == 1
    assert len(notes[0].content) < 250
    # leader final 与用户轮次原样保留
    assert any(getattr(m, "content", "") == "leader 汇总答复" for m in messages)
    assert any(type(m).__name__ == "UserMessage" and m.content == "帮我写报告" for m in messages)
    assert skipped >= 2  # 成员 tool_call + tool_result
    # 团队期检出 → 末尾追加身份复位注记（AssistantMessage 且为序列末条）
    last = messages[-1]
    assert type(last).__name__ == "AssistantMessage"
    assert last.content == sos._TEAM_IDENTITY_RESET_NOTE


def test_identity_reset_note_via_mode_team_only():
    """无成员归因记录但记录带 mode=team（老口径团队期）→ 同样追加复位注记。"""
    records = [
        {**_user("r1", "团队期问题", 1.0), "mode": "team"},
        {**_final("r1", "作为主理人，我们团队已完成。", 2.0), "mode": "team"},
    ]

    messages, _ = sos._build_context_messages_from_history(
        records, drop_member_internals=True
    )

    assert messages[-1].content == sos._TEAM_IDENTITY_RESET_NOTE
    # leader 答复原文保留（复位注记治理口吻延续，不做内容改写）
    assert any(getattr(m, "content", "") == "作为主理人，我们团队已完成。" for m in messages)


def test_identity_reset_note_absent_without_team_era():
    """纯默认模式历史（无 mode=team、无成员记录）→ 不追加注记。"""
    records = [
        _user("r1", "你好", 1.0),
        _final("r1", "你好！", 2.0),
    ]

    messages, _ = sos._build_context_messages_from_history(
        records, drop_member_internals=True
    )

    assert all(
        getattr(m, "content", "") != sos._TEAM_IDENTITY_RESET_NOTE for m in messages
    )


def test_identity_reset_note_absent_when_fidelity_mode():
    """保真路径（drop_member_internals=False，如 rewind）即使含团队期记录也不加注记。"""
    records = [
        {**_user("r1", "团队期问题", 1.0), "mode": "team"},
        _member_tool("member-tool-bob-1", 2.0),
    ]

    messages, _ = sos._build_context_messages_from_history(records)

    assert all(
        getattr(m, "content", "") != sos._TEAM_IDENTITY_RESET_NOTE for m in messages
    )


def test_converter_default_keeps_member_records():
    """默认（rewind 等保真路径）行为不变：成员工具事件照常转换。"""
    records = [
        _member_tool("member-tool-bob-1", 1.0),
        _member_tool_result("member-tool-bob-1", 2.0),
    ]

    messages, _ = sos._build_context_messages_from_history(records)

    kinds = [type(m).__name__ for m in messages]
    assert "ToolMessage" in kinds
    assert "AssistantMessage" in kinds


class _StubContext:
    def __init__(self):
        self.messages = ["x"]


class _StubContextEngine:
    def __init__(self):
        self.cleared = False

    def get_context(self, *, session_id):
        return _StubContext()

    async def clear_context(self, *, session_id):
        self.cleared = True


class _StubReactAgent:
    def __init__(self, engine):
        self.context_engine = engine


class _StubDeepAgent:
    def __init__(self, engine):
        self.react_agent = _StubReactAgent(engine)


@pytest.mark.asyncio
async def test_refresh_fingerprint_missing_falls_back_to_rebuild(monkeypatch):
    """指纹失效（rewind/历史轮换）时全量重建而非静默停更。"""
    engine = _StubContextEngine()
    deep_agent = _StubDeepAgent(engine)
    calls: dict[str, object] = {}

    async def _fake_restore(**kwargs):
        calls["restore_kwargs"] = kwargs
        return True

    async def _fake_persist(**kwargs):
        calls["persisted"] = True
        return True

    monkeypatch.setattr(sos, "_restore_session_context_from_history", _fake_restore)
    monkeypatch.setattr(sos, "_persist_session_context", _fake_persist)
    monkeypatch.setattr(sos, "resolve_live_agent_session", lambda *_a, **_k: object())
    monkeypatch.setattr(sos, "history_exists", lambda _sid: True)
    monkeypatch.setattr(sos, "load_history_tail_request_id",
                        lambda _sid, exclude_request_id=None: "tail-r2")
    monkeypatch.setattr(
        sos,
        "load_history_records",
        lambda _sid: [
            _user("r1", "旧问题", 1.0),
            _final("r1", "旧回答", 2.0),
            _user("r2", "新问题", 3.0),
            _final("r2", "新回答", 4.0),
        ],
    )

    result = await sos.refresh_session_context_if_stale(
        deep_agent=deep_agent,
        session_id="s-rebuild",
        synced_tail_request_id="r-not-in-history",
    )

    # 指纹失效 → 清窗 + 全量重建 + 持久化 + 指纹推进到磁盘尾
    assert engine.cleared is True
    assert calls.get("restore_kwargs", {}).get("drop_member_internals") is True
    assert calls.get("persisted") is True
    assert result == "tail-r2"
