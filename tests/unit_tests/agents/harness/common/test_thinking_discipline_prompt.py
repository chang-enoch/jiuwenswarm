# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Thinking-discipline prompt section: content, priority, and rail injection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.harness.prompts import SystemPromptBuilder

from jiuwenswarm.agents.harness.common.prompt.prompt_builder import (
    LocalSectionName,
    PromptPriority,
    _final_visible_reply_prompt,
    _thinking_discipline_prompt,
)
from jiuwenswarm.agents.harness.common.rails.response_prompt_rail import ResponsePromptRail


def test_thinking_discipline_sits_before_final_visible_reply():
    section = _thinking_discipline_prompt("cn")
    assert section.name == LocalSectionName.THINKING_DISCIPLINE
    assert PromptPriority.RESPONSE < section.priority < PromptPriority.FINAL_VISIBLE_REPLY
    assert section.priority < _final_visible_reply_prompt("cn").priority


def test_thinking_discipline_cn_covers_hard_rules():
    content = _thinking_discipline_prompt("cn").content["cn"]
    assert "# 思考纪律" in content
    assert "严禁誊写产物" in content
    assert "严禁复述上下文" in content
    assert "思考预算" in content
    assert "tool_calls" in content


def test_thinking_discipline_en_covers_hard_rules():
    content = _thinking_discipline_prompt("en").content["en"]
    assert "# Thinking discipline" in content
    assert "Do not transcribe artifacts" in content
    assert "Do not restate context" in content
    assert "Thinking budget" in content
    assert "tool_calls" in content


def test_assembled_prompt_places_discipline_before_final_reply():
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(_thinking_discipline_prompt("cn"))
    builder.add_section(_final_visible_reply_prompt("cn"))
    prompt = builder.build()
    assert prompt.index("思考纪律") < prompt.index("最终可见回复")


@pytest.mark.asyncio
async def test_response_prompt_rail_injects_thinking_discipline():
    rail = ResponsePromptRail()
    builder = SystemPromptBuilder(language="cn")
    rail.init(SimpleNamespace(system_prompt_builder=builder))

    await rail.before_model_call(SimpleNamespace(inputs={"channel": "feishu"}))

    assert builder.get_section(LocalSectionName.THINKING_DISCIPLINE) is not None
    assert builder.get_section(LocalSectionName.FINAL_VISIBLE_REPLY) is not None
    prompt = builder.build()
    assert prompt.index("思考纪律") < prompt.index("最终可见回复")

    rail.uninit(None)
    assert builder.get_section(LocalSectionName.THINKING_DISCIPLINE) is None
    assert builder.get_section(LocalSectionName.FINAL_VISIBLE_REPLY) is None
