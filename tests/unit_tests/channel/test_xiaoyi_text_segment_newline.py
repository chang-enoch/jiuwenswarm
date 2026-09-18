"""正文流式输出分段：每段（每轮模型调用的正文）最后一帧末尾补一个换行.

端侧把同一 task 的正文增量前后拼接，模型每轮调用产出的一段正文之间会粘成一行。
网关按「扣住最后一片」实现：增量帧只发到倒数第二片，最后一片等这段正文结束时补
一个换行再下发（帧数不变、不补空帧）。本用例锁定该行为，防止段间换行丢失，也防
止回退成「整轮末尾把整段正文重发一遍」。
"""
import json
import time
from typing import Any

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    XiaoyiChannel,
    XiaoyiChannelConfig,
)


def _build_channel() -> "tuple[XiaoyiChannel, list[dict[str, Any]]]":
    channel = XiaoyiChannel(
        XiaoyiChannelConfig(agent_id="agent-1", enable_streaming=True),
        RobotMessageRouter(),
    )
    sent: list[dict[str, Any]] = []

    async def fake_safe_ws_send(url_key: str, payload: dict[str, Any]) -> None:
        sent.append(payload)

    channel._ws_connections = {"ws_url1": object()}
    channel._safe_ws_send = fake_safe_ws_send
    channel._mark_session_active("xiaoyi-session-1")
    return channel, sent


def _message(event_type: EventType, payload: dict[str, Any]) -> Message:
    return Message(
        id=f"request-{time.time()}",
        type="event",
        channel_id="xiaoyi",
        session_id="jiuwen-session-1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload=payload,
        event_type=event_type,
        metadata={
            "xiaoyi_session_id": "xiaoyi-session-1",
            "xiaoyi_task_id": "xiaoyi-task-1",
        },
    )


def _artifact(wrapper: dict[str, Any]) -> dict[str, Any]:
    return json.loads(wrapper["msgDetail"])["result"]


def _text_frames(sent: list[dict[str, Any]]) -> list[tuple[str, bool, bool]]:
    """(正文, append, lastChunk)，只取正文 artifact-update 帧。"""
    frames: list[tuple[str, bool, bool]] = []
    for wrapper in sent:
        result = _artifact(wrapper)
        if result.get("kind") != "artifact-update":
            continue
        parts = (result.get("artifact") or {}).get("parts") or [{}]
        frames.append((parts[0].get("text", ""), result["append"], result["lastChunk"]))
    return frames


def _tool_call_payload() -> dict[str, Any]:
    return {
        "event_type": "chat.tool_call",
        "tool_call": {
            "name": "bash",
            "tool_call_id": "call_01",
            "arguments": "{}",
            "display_name": "执行 pwd",
        },
    }


@pytest.mark.asyncio
async def test_each_model_call_segment_ends_with_newline() -> None:
    channel, sent = _build_channel()

    # ── 第 1 轮模型调用的正文 ──
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "保持最简，"}))
    # 首片被扣住，还不外发（避免这一片永远等不到收尾）
    assert _text_frames(sent) == []

    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "两个只读调用"}))
    assert _text_frames(sent) == [("保持最简，", True, False)]

    # 工具阶段开始 = 第 1 轮正文结束 → 扣住的那片补一个换行发出
    await channel.send(_message(EventType.CHAT_TOOL_CALL, _tool_call_payload()))
    assert _text_frames(sent) == [
        ("保持最简，", True, False),
        ("两个只读调用\n", True, False),
    ]

    # ── 第 2 轮模型调用的正文（终稿与增量一致）──
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "两个调用都成功。"}))
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "再发三个只读调用。"}))
    await channel.send(_message(EventType.CHAT_TOOL_CALL, _tool_call_payload()))
    assert _text_frames(sent)[-2:] == [
        ("两个调用都成功。", True, False),
        ("再发三个只读调用。\n", True, False),
    ]

    # ── 第 3 轮（也是本轮最后一段）──
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "全部完成，"}))
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "链路跑通。"}))
    await channel.send(
        _message(
            EventType.CHAT_FINAL,
            {"event_type": "chat.final", "content": "全部完成，链路跑通。"},
        )
    )
    frames = _text_frames(sent)
    # 终稿已被增量覆盖：只把扣住的那片补换行后发出，不重发整段
    assert frames[-2:] == [
        ("全部完成，", True, False),
        ("链路跑通。\n", True, True),
    ]
    # 段与段之间都有换行：端侧拼接后三段正文各自成行
    assert "".join(text for text, _, _ in frames) == (
        "保持最简，两个只读调用\n两个调用都成功。再发三个只读调用。\n全部完成，链路跑通。\n"
    )

    # 段末片都带换行；lastChunk 只在整轮终帧为 true（本轮正文仅一段流式输出）
    assert [f for f in frames if f[0].endswith("\n")] == [
        ("两个只读调用\n", True, False),
        ("再发三个只读调用。\n", True, False),
        ("链路跑通。\n", True, True),
    ]
    assert all(text for text, _, _ in frames)
    assert all(append is True for _, append, _ in frames)


@pytest.mark.asyncio
async def test_final_rewritten_text_only_sends_missing_tail() -> None:
    """终稿没被增量覆盖时：只补发缺的尾巴，并在尾巴上补换行。"""
    channel, sent = _build_channel()

    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "前半段"}))
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "被改写"}))
    await channel.send(
        _message(
            EventType.CHAT_FINAL,
            {"event_type": "chat.final", "content": "前半段终稿版本"},
        )
    )

    assert _text_frames(sent) == [
        ("前半段", True, False),
        ("被改写", True, False),
        ("终稿版本\n", True, True),
    ]


@pytest.mark.asyncio
async def test_empty_delta_does_not_duplicate_pending_piece() -> None:
    """空增量帧不能让扣住的正文片被重复外发。"""
    channel, sent = _build_channel()

    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "第一片"}))
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": ""}))
    await channel.send(_message(EventType.CHAT_DELTA, {"event_type": "chat.delta", "content": "第三片"}))
    await channel.send(
        _message(EventType.CHAT_FINAL, {"event_type": "chat.final", "content": "第一片第三片"})
    )

    assert _text_frames(sent) == [
        ("第一片", True, False),
        ("第三片\n", True, True),
    ]
