# coding: utf-8
"""模式切换上下文交接单测：标记生命周期 + 前情块构建 + query 包装。"""

from __future__ import annotations

import json

import pytest

from jiuwenswarm.server.runtime.agent_adapter import context_handoff
from jiuwenswarm.server.runtime.session import session_history, session_metadata


@pytest.fixture()
def sessions_dir(tmp_path, monkeypatch):
    """把 history/metadata 的存储根切到 tmp_path，并隔离 metadata 缓存。"""
    monkeypatch.setattr(session_history, "get_agent_sessions_dir", lambda: tmp_path)
    monkeypatch.setattr(session_metadata, "get_agent_sessions_dir", lambda: tmp_path)
    with session_metadata._CACHE_LOCK:
        session_metadata._METADATA_CACHE.clear()
    yield tmp_path
    with session_metadata._CACHE_LOCK:
        session_metadata._METADATA_CACHE.clear()


def _make_session(sessions_dir, session_id, records, *, with_metadata=True):
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    if with_metadata:
        (session_dir / "metadata.json").write_text(
            json.dumps({"session_id": session_id}), encoding="utf-8"
        )
    if records is not None:
        session_history.write_history_records(session_id, records)
    return session_dir


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


def _compact_pair(rid, summary, ts):
    return [
        {
            "role": "assistant",
            "request_id": rid,
            "event_type": "context.compact_boundary",
            "content": "Conversation compacted",
            "timestamp": ts,
        },
        {
            "role": "assistant",
            "request_id": rid,
            "event_type": "context.compact_summary",
            "content": summary,
            "is_compact_summary": True,
            "transcript_only": True,
            "timestamp": ts + 0.001,
        },
    ]


# ---- 标记生命周期 ----


def test_marker_set_get_clear(sessions_dir):
    sid = "desktop_t1_aaaa11111111"
    _make_session(sessions_dir, sid, [_user("r1", "你好", 1.0)])

    assert context_handoff.set_context_handoff_marker(
        sid, team_name="t-1", from_label="通用助手"
    ) is True

    marker = context_handoff.get_context_handoff_marker(sid)
    assert marker is not None
    assert marker["team_name"] == "t-1"
    assert marker["from_label"] == "通用助手"
    assert marker["snapshot_tail_request_id"] == "r1"
    assert marker["attempts"] == 0

    context_handoff.clear_context_handoff_marker(sid)
    assert context_handoff.get_context_handoff_marker(sid) is None


def test_marker_not_set_without_history(sessions_dir):
    sid = "desktop_t2_aaaa11111111"
    _make_session(sessions_dir, sid, None)
    assert context_handoff.set_context_handoff_marker(sid, team_name="t-1") is False
    assert context_handoff.get_context_handoff_marker(sid) is None


def test_marker_not_set_without_metadata(sessions_dir):
    sid = "desktop_t3_aaaa11111111"
    _make_session(sessions_dir, sid, [_user("r1", "你好", 1.0)], with_metadata=False)
    assert context_handoff.set_context_handoff_marker(sid, team_name="t-1") is False


def test_clear_marker_noop_when_absent(sessions_dir):
    sid = "desktop_t4_aaaa11111111"
    _make_session(sessions_dir, sid, [_user("r1", "你好", 1.0)])
    context_handoff.clear_context_handoff_marker(sid)  # 不抛即通过


# ---- 前情块构建 ----


def test_build_block_basic_turns(sessions_dir):
    sid = "desktop_t5_aaaa11111111"
    records = [
        _user("r1", "你好", 1.0),
        _final("r1", "你好！有什么可以帮你？", 2.0),
        # 成员产出不进前情
        _final("member-final-bob-1", "成员结论", 3.0, member_name="bob"),
        # 成员内部工具事件不进前情
        {
            "role": "assistant",
            "request_id": "member-tool-bob-x",
            "event_type": "chat.tool_call",
            "content": "",
            "timestamp": 3.5,
        },
        _user("r2", "介绍下自己", 4.0),
        _final("r2", "我是小艺Work", 5.0),
    ]
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid)

    assert block is not None
    assert "用户：你好" in block
    assert "助手：你好！有什么可以帮你？" in block
    assert "用户：介绍下自己" in block
    assert "助手：我是小艺Work" in block
    assert "成员结论" not in block
    assert block.index("用户：你好") < block.index("用户：介绍下自己")


def test_build_block_excludes_in_flight_request(sessions_dir):
    sid = "desktop_t6_aaaa11111111"
    records = [
        _user("r1", "你好", 1.0),
        _final("r1", "你好！", 2.0),
        _user("r9", "当前问题", 9.0),  # 在途请求的 user 记录已落盘
    ]
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid, exclude_request_id="r9")

    assert block is not None
    assert "当前问题" not in block
    assert "用户：你好" in block


def test_build_block_snapshot_tail_cut(sessions_dir):
    sid = "desktop_t7_aaaa11111111"
    records = [
        _user("r1", "快照内", 1.0),
        _final("r1", "快照内回答", 2.0),
        _user("r2", "快照后写入", 3.0),
        _final("r2", "快照后回答", 4.0),
    ]
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid, snapshot_tail_request_id="r1")

    assert block is not None
    assert "快照内" in block
    assert "快照后写入" not in block
    assert "快照后回答" not in block


def test_build_block_compaction_aware(sessions_dir):
    """压缩史：摘要承接早期，摘要边界后原文承接近期，摘要前原文不双份。"""
    sid = "desktop_t8_aaaa11111111"
    records = [
        _user("r1", "早期问题一", 1.0),
        _final("r1", "早期回答一", 2.0),
        *_compact_pair("rc", "早期摘要：用户咨询了A和B。", 3.0),
        _user("r2", "压缩后问题", 4.0),
        _final("r2", "压缩后回答", 5.0),
    ]
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid)

    assert block is not None
    assert "（早期对话摘要）" in block
    assert "早期摘要：用户咨询了A和B。" in block
    assert "压缩后问题" in block
    assert "压缩后回答" in block
    # 摘要前的原文轮次不重复注入
    assert "早期问题一" not in block
    assert "早期回答一" not in block
    # 分隔条无内容，不进前情
    assert "Conversation compacted" not in block


def test_build_block_uses_latest_compaction_only(sessions_dir):
    sid = "desktop_t9_aaaa11111111"
    records = [
        _user("r1", "第一批", 1.0),
        _final("r1", "第一批回答", 2.0),
        *_compact_pair("rc1", "第一次摘要", 3.0),
        _user("r2", "第二批", 4.0),
        _final("r2", "第二批回答", 5.0),
        *_compact_pair("rc2", "第二次摘要", 6.0),
        _user("r3", "最新问题", 7.0),
        _final("r3", "最新回答", 8.0),
    ]
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid)

    assert block is not None
    assert "第二次摘要" in block
    assert "第一次摘要" not in block
    assert "第二批" not in block  # 已被第二次摘要吸纳
    assert "最新问题" in block


def test_build_block_truncates_by_turns(sessions_dir):
    sid = "desktop_t10_aaaa11111111"
    records = []
    ts = 1.0
    for i in range(5):
        records.append(_user(f"ru{i}", f"问题{i}", ts))
        ts += 1
        records.append(_final(f"ra{i}", f"回答{i}", ts))
        ts += 1
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid, max_turns=2, max_chars=10_000)

    assert block is not None
    assert "回答4" in block
    assert "问题4" in block
    assert "问题0" not in block
    assert "（更早的原文对话已省略）" in block


def test_build_block_truncates_by_chars(sessions_dir):
    sid = "desktop_t11_aaaa11111111"
    records = [
        _user("r1", "短", 1.0),
        _final("r1", "短答", 2.0),
        _user("r2", "长" * 100, 3.0),
        _final("r2", "最近回答", 4.0),
    ]
    _make_session(sessions_dir, sid, records)

    block = context_handoff.build_handoff_block(sid, max_chars=20, max_turns=20)

    assert block is not None
    assert "最近回答" in block
    assert "长" * 50 not in block  # 超长轮次整体让位
    assert "（更早的原文对话已省略）" in block


def test_build_block_empty_returns_none(sessions_dir):
    sid = "desktop_t12_aaaa11111111"
    records = [
        {  # 只有成员/工具类记录，无有效轮次
            "role": "assistant",
            "request_id": "member-tool-x-1",
            "event_type": "chat.tool_call",
            "content": "",
            "timestamp": 1.0,
        }
    ]
    _make_session(sessions_dir, sid, records)
    assert context_handoff.build_handoff_block(sid) is None


# ---- query 包装（标记消费）----


def test_wrap_consumes_marker_and_prefixes(sessions_dir):
    sid = "desktop_t13_aaaa11111111"
    _make_session(
        sessions_dir,
        sid,
        [_user("r1", "你好", 1.0), _final("r1", "你好！", 2.0)],
    )
    assert context_handoff.set_context_handoff_marker(
        sid, team_name="t-x", from_label="通用助手"
    ) is True

    wrapped = context_handoff.maybe_wrap_query_with_handoff(
        session_id=sid, request_id="r-new", query="现在的问题"
    )

    assert "【会话前情】" in wrapped
    assert "用户：你好" in wrapped
    # 来源角色如实标注，不伪造"是leader自己说的"
    assert "当时由「通用助手」接待用户，不是你" in wrapped
    assert wrapped.endswith("现在的问题")
    # 消费即清标记
    assert context_handoff.get_context_handoff_marker(sid) is None


def test_wrap_legacy_marker_without_from_label_uses_fallback(sessions_dir):
    """存量标记（无 from_label 字段）走兜底来源文案。"""
    sid = "desktop_t18_aaaa11111111"
    _make_session(
        sessions_dir,
        sid,
        [_user("r1", "你好", 1.0), _final("r1", "你好！", 2.0)],
    )
    assert context_handoff.set_context_handoff_marker(sid, team_name="t-x") is True
    # 模拟旧版本写入的标记：抹掉 from_label 字段
    metadata = context_handoff._read_metadata(sid)
    metadata[context_handoff._MARKER_KEY].pop("from_label", None)
    context_handoff._enqueue_write(sid, metadata, sync_write=True)

    wrapped = context_handoff.maybe_wrap_query_with_handoff(
        session_id=sid, request_id="r-new", query="现在的问题"
    )

    assert "当时由「本会话的前任角色」接待用户，不是你" in wrapped


def test_wrap_without_marker_returns_identity(sessions_dir):
    sid = "desktop_t14_aaaa11111111"
    _make_session(sessions_dir, sid, [_user("r1", "你好", 1.0)])
    assert context_handoff.maybe_wrap_query_with_handoff(
        session_id=sid, request_id="r2", query="原样"
    ) == "原样"


def test_wrap_failure_retries_then_gives_up(sessions_dir, monkeypatch):
    sid = "desktop_t15_aaaa11111111"
    _make_session(sessions_dir, sid, [_user("r1", "你好", 1.0)])
    assert context_handoff.set_context_handoff_marker(sid, team_name="t-x") is True

    def _boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(context_handoff, "build_handoff_block", _boom)

    for expected_attempts in (1, 2):
        assert context_handoff.maybe_wrap_query_with_handoff(
            session_id=sid, request_id="r2", query="q"
        ) == "q"
        marker = context_handoff.get_context_handoff_marker(sid)
        assert marker is not None
        assert marker["attempts"] == expected_attempts

    # 第三次失败：放弃并清标记
    assert context_handoff.maybe_wrap_query_with_handoff(
        session_id=sid, request_id="r2", query="q"
    ) == "q"
    assert context_handoff.get_context_handoff_marker(sid) is None


# ---- try_* 安全包装（expert_service 调用面） ----


def test_try_set_swallows_errors(sessions_dir, monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(context_handoff, "set_context_handoff_marker", _boom)
    assert context_handoff.try_set_context_handoff_marker(
        "desktop_t16_aaaa11111111", team_name="t-x"
    ) is False


def test_try_clear_swallows_errors(sessions_dir, monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(context_handoff, "clear_context_handoff_marker", _boom)
    # 不抛即通过
    context_handoff.try_clear_context_handoff_marker("desktop_t17_aaaa11111111")


# ---- 来源角色标注映射（expert_service._handoff_from_label） ----


def test_handoff_from_label_mapping(monkeypatch):
    from jiuwenswarm.server.runtime.expert import expert_service

    # 通用问答（无前任专家）→ 通用助手
    assert expert_service._handoff_from_label("", "") == "通用助手"
    # 换团 → 另一个专家团
    assert expert_service._handoff_from_label("e1", "team") == "另一个专家团"
    # 单专家 → 显示名；解析失败兜底
    monkeypatch.setattr(
        expert_service, "_resolve_expert_display_name", lambda *_a: "小柯"
    )
    assert expert_service._handoff_from_label("e1", "agent") == "专家「小柯」"
    monkeypatch.setattr(
        expert_service, "_resolve_expert_display_name", lambda *_a: ""
    )
    assert expert_service._handoff_from_label("e1", "agent") == "单专家"
