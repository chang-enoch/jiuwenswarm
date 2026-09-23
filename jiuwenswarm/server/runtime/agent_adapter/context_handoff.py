# coding: utf-8
"""模式切换上下文交接（agent/code/design → team 首轮前情注入）。

机制：装团（expert.load team 分支）时在 session metadata 打交接标记（含
history.jsonl 快照尾指纹）；首个团队回合消费标记，把 history.jsonl 里的前情
构建为结构化文本块拼在用户 query 前缀，随首轮 USER_INPUT 进入团队会话，
后续轮次由团队 checkpoint 自然携带，无需重复注入。

压缩感知：切换前发生过上下文压缩时，用最近一次 compact_summary 承接早期
对话（摘要之前的原文轮次不再重复注入），摘要边界之后的原文轮次最近优先
承接。rewind 物理截断历史后天然兼容。

方向二（team 卸载回默认模式）不在本模块——传输复用 refresh/warmup 存量
链路，净化在 session_ops_service 的转换器参数里。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from jiuwenswarm.server.runtime.session.session_history import (
    flush_history_writes,
    history_exists,
    load_history_records,
    load_history_tail_request_id,
)
from jiuwenswarm.server.runtime.session.session_metadata import (
    _enqueue_write,
    _read_metadata,
)

logger = logging.getLogger(__name__)

_MARKER_KEY = "context_handoff"
_MAX_ATTEMPTS = 3

_ENV_ENABLED = "CONTEXT_HANDOFF_ENABLED"
_ENV_MAX_CHARS = "CONTEXT_HANDOFF_MAX_CHARS"
_ENV_MAX_TURNS = "CONTEXT_HANDOFF_MAX_TURNS"
_ENV_MAX_SUMMARY_CHARS = "CONTEXT_HANDOFF_MAX_SUMMARY_CHARS"

_DEFAULT_MAX_CHARS = 6000
_DEFAULT_MAX_TURNS = 20
_DEFAULT_MAX_SUMMARY_CHARS = 3000

# 成员内部事件的 request_id 前缀（成员落盘见 team_helpers 的
# _persist_member_final_output / _persist_member_tool_event：member-final-*
# / member-tool-*，且 extra.member_name 并入记录顶层）
_MEMBER_REQUEST_PREFIX = "member-"

# 成员产出注记的截断长度（与方向二净化口径一致）
_MEMBER_OUTPUT_NOTE_CHARS = 200


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def handoff_enabled() -> bool:
    return os.environ.get(_ENV_ENABLED, "true").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def set_context_handoff_marker(
        session_id: str,
        *,
        team_name: str,
        from_label: str = "",
) -> bool:
    """装团时打交接标记；会话无历史时不打（空前情无可注入）。返回是否打上。

    快照尾指纹固定"装团时刻"的历史末尾，装团后到首发前的其他写入（系统
    事件等）不混入前情。打标记前 flush 历史写入队列，避免异步队列里的
    末轮记录漏出快照。

    ``from_label``：切换来源角色的展示名（如「通用助手」「专家「XX」」），
    写进前情块模板，避免 leader 把前任角色的话误认成自己说的。
    """
    if not handoff_enabled():
        return False
    if not history_exists(session_id):
        return False
    metadata = _read_metadata(session_id, cache_bust=True)
    if not metadata:
        return False
    flush_history_writes()
    metadata[_MARKER_KEY] = {
        "snapshot_tail_request_id": load_history_tail_request_id(session_id),
        "team_name": str(team_name or ""),
        "from_label": str(from_label or ""),
        "set_at": time.time(),
        "attempts": 0,
    }
    _enqueue_write(session_id, metadata, sync_write=True)
    logger.info(
        "[session_id=%s] [ContextHandoff] 打交接标记: team=%s from=%s",
        session_id,
        team_name,
        from_label or "-",
    )
    return True


def get_context_handoff_marker(session_id: str) -> dict[str, Any] | None:
    metadata = _read_metadata(session_id)
    marker = metadata.get(_MARKER_KEY)
    return marker if isinstance(marker, dict) else None


def clear_context_handoff_marker(session_id: str) -> None:
    metadata = _read_metadata(session_id)
    if _MARKER_KEY not in metadata:
        return
    metadata.pop(_MARKER_KEY, None)
    _enqueue_write(session_id, metadata, sync_write=True)


def try_set_context_handoff_marker(
        session_id: str,
        *,
        team_name: str,
        from_label: str = "",
) -> bool:
    """打标记的安全包装：失败仅告警不抛（交接是增强，不阻塞装团）。"""
    try:
        return set_context_handoff_marker(
            session_id, team_name=team_name, from_label=from_label
        )
    except Exception as exc:
        logger.warning(
            "[session_id=%s] [ContextHandoff] 打交接标记失败: %s",
            session_id,
            exc,
        )
        return False


def try_clear_context_handoff_marker(session_id: str) -> None:
    """清标记的安全包装：失败仅告警不抛（残留标记有 attempts 上限兜底）。"""
    try:
        clear_context_handoff_marker(session_id)
    except Exception as exc:
        logger.warning(
            "[session_id=%s] [ContextHandoff] 清交接标记失败: %s",
            session_id,
            exc,
        )


def _write_marker_attempts(session_id: str, marker: dict[str, Any], attempts: int) -> None:
    metadata = _read_metadata(session_id)
    if not metadata:
        return
    marker = dict(marker)
    marker["attempts"] = attempts
    metadata[_MARKER_KEY] = marker
    _enqueue_write(session_id, metadata, sync_write=True)


def is_member_history_record(record: dict[str, Any]) -> bool:
    """成员归因记录判定：member_name 顶层字段或 member-* request_id 前缀。"""
    if str(record.get("member_name") or "").strip():
        return True
    return str(record.get("request_id") or "").startswith(_MEMBER_REQUEST_PREFIX)


def _record_text(record: dict[str, Any]) -> str:
    content = record.get("content", "")
    if isinstance(content, list):
        content = " ".join(
            str(part)
            for part in content
            if isinstance(part, str)
            or (isinstance(part, dict) and part.get("type") == "text")
        )
    return str(content).strip()


def _resolve_limits(
        max_chars: int | None,
        max_turns: int | None,
        max_summary_chars: int | None,
) -> tuple[int, int, int]:
    """显式入参优先，缺省回落 env 配置（再缺省用内置默认）。"""
    return (
        max_chars or _env_int(_ENV_MAX_CHARS, _DEFAULT_MAX_CHARS),
        max_turns or _env_int(_ENV_MAX_TURNS, _DEFAULT_MAX_TURNS),
        max_summary_chars or _env_int(
            _ENV_MAX_SUMMARY_CHARS, _DEFAULT_MAX_SUMMARY_CHARS
        ),
    )


def _cut_at_snapshot_tail(
        records: list[dict[str, Any]],
        snapshot_tail_request_id: str | None,
) -> list[dict[str, Any]]:
    """截到打标记时刻的快照尾；找不到（rewind 截断等）则保留全部现存记录。"""
    if not snapshot_tail_request_id:
        return records
    for idx in range(len(records) - 1, -1, -1):
        if str(records[idx].get("request_id") or "") == snapshot_tail_request_id:
            return records[: idx + 1]
    return records


def _exclude_in_flight_request(
        records: list[dict[str, Any]],
        exclude_request_id: str | None,
) -> list[dict[str, Any]]:
    """排除在途请求（当前用户消息已先于团队路径落盘，不重复进前情）。"""
    excluded = (exclude_request_id or "").strip()
    if not excluded:
        return records
    return [
        record
        for record in records
        if str(record.get("request_id") or "") != excluded
    ]


def _find_latest_compact_summary(
        records: list[dict[str, Any]],
        max_summary_chars: int,
) -> tuple[str, int | None]:
    """最近一次压缩摘要（多次压缩只认最近一次，旧摘要已被新摘要吸纳）。

    返回 (摘要文本, 摘要记录下标)；无有效摘要返回 ("", None)。
    """
    for idx in range(len(records) - 1, -1, -1):
        record = records[idx]
        if (record.get("event_type") or "") != "context.compact_summary":
            continue
        text = _record_text(record)
        if not text:
            return "", None
        if len(text) > max_summary_chars:
            text = text[:max_summary_chars].rstrip() + "…（摘要过长已截断）"
        return text, idx
    return "", None


def _extract_turns(records: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """提取原文轮次：用户原文 + leader/单 agent 的 chat.final 正文（成员记录跳过）。"""
    turns: list[tuple[str, str]] = []
    for record in records:
        if is_member_history_record(record):
            continue
        role = str(record.get("role") or "").strip().lower()
        if role == "user":
            text = _record_text(record)
            if text:
                turns.append(("用户", text))
        elif role == "assistant" and (record.get("event_type") or "") == "chat.final":
            text = _record_text(record)
            if text:
                turns.append(("助手", text))
    return turns


def _select_recent_turns(
        turns: list[tuple[str, str]],
        *,
        max_turns: int,
        max_chars: int,
) -> tuple[list[tuple[str, str]], bool]:
    """最近优先装填，全有或全无按轮次；最新一轮超长时尾部截断保底。

    返回 (按时间序的选中轮次, 是否有更早轮次被省略)。
    """
    selected: list[tuple[str, str]] = []
    total = 0
    truncated = False
    for speaker, text in reversed(turns):
        if len(selected) >= max_turns or total + len(text) > max_chars:
            if not selected and len(text) > max_chars:
                selected.append((speaker, "…" + text[-max_chars:]))
            truncated = True
            break
        selected.append((speaker, text))
        total += len(text)
    selected.reverse()
    return selected, truncated


def _render_handoff_block(
        summary_text: str,
        selected: list[tuple[str, str]],
        truncated: bool,
        from_label: str = "",
) -> str:
    """拼装前情块模板：【会话前情】…（摘要）…原文轮次…【前情结束】。

    措辞如实标注来源角色：前情是「你接管之前」由来源角色（通用助手/单专家/
    另一团）接待用户的记录，不是"你身份生效之前自己说的话"——否则 leader
    会把前任的口吻/承诺误认成自己的，人设穿帮。
    """
    label = (from_label or "").strip() or "本会话的前任角色"
    lines = [
        f"【会话前情】以下是本会话在你接管之前的对话记录（当时由「{label}」接待用户，"
        "不是你），仅作背景参考，不是本次提问的内容：",
        "---",
    ]
    if summary_text:
        lines.append("（早期对话摘要）")
        lines.append(summary_text)
        lines.append("---")
    if truncated:
        lines.append("（更早的原文对话已省略）")
    for speaker, text in selected:
        lines.append(f"{speaker}：{text}")
    lines.append("---")
    lines.append("【前情结束】以下是用户当前的问题，请结合前情回答：")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def build_handoff_block(
        session_id: str,
        *,
        exclude_request_id: str | None = None,
        snapshot_tail_request_id: str | None = None,
        max_chars: int | None = None,
        max_turns: int | None = None,
        max_summary_chars: int | None = None,
        from_label: str = "",
) -> str | None:
    """从 history.jsonl 构建前情块（不含当前问题）。无有效前情返回 None。

    压缩感知：存在最近一次 compact_summary 时，摘要承接早期对话（限额
    max_summary_chars），摘要之后的原文轮次最近优先承接（限额 max_chars /
    max_turns）；摘要之前的原文轮次不重复注入。
    """
    max_chars, max_turns, max_summary_chars = _resolve_limits(
        max_chars, max_turns, max_summary_chars
    )

    records = load_history_records(session_id)
    if not isinstance(records, list):
        return None
    records = _cut_at_snapshot_tail(records, snapshot_tail_request_id)
    records = _exclude_in_flight_request(records, exclude_request_id)
    if not records:
        return None

    summary_text, summary_idx = _find_latest_compact_summary(
        records, max_summary_chars
    )
    raw_records = records[summary_idx + 1:] if summary_idx is not None else records
    selected, truncated = _select_recent_turns(
        _extract_turns(raw_records),
        max_turns=max_turns,
        max_chars=max_chars,
    )

    if not summary_text and not selected:
        return None
    return _render_handoff_block(summary_text, selected, truncated, from_label)


def maybe_wrap_query_with_handoff(
        *,
        session_id: str,
        request_id: str,
        query: Any,
) -> Any:
    """首个团队回合消费交接标记：有前情则包装 query；无标记/空前情原样返回。

    构建失败保留标记并累计 attempts，达 _MAX_ATTEMPTS 上限后清标记放弃——
    前情注入是增强而非前置条件，永不阻塞团队起跑。
    """
    if not handoff_enabled() or not isinstance(query, str) or not query.strip():
        return query
    marker = get_context_handoff_marker(session_id)
    if marker is None:
        return query
    try:
        block = build_handoff_block(
            session_id,
            exclude_request_id=request_id,
            snapshot_tail_request_id=marker.get("snapshot_tail_request_id"),
            from_label=str(marker.get("from_label") or ""),
        )
    except Exception as exc:
        attempts = int(marker.get("attempts") or 0) + 1
        logger.warning(
            "[session_id=%s] [ContextHandoff] 前情块构建失败: attempts=%d error=%s",
            session_id,
            attempts,
            exc,
        )
        if attempts >= _MAX_ATTEMPTS:
            clear_context_handoff_marker(session_id)
        else:
            _write_marker_attempts(session_id, marker, attempts)
        return query
    clear_context_handoff_marker(session_id)
    if not block:
        return query
    logger.info(
        "[session_id=%s] [ContextHandoff] 前情注入完成: team=%s block_chars=%d",
        session_id,
        marker.get("team_name") or "",
        len(block),
    )
    return f"{block}{query}"
