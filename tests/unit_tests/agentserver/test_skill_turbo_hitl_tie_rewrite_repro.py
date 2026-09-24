# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""验证 skill_turbo HITL TIC 经工具链后仍被 after_tool_call TIE 改写消费。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
)
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness import create_deep_agent

from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)
from jiuwenswarm.server.runtime.skill_turbo import skill_turbo_tools
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
    build_skill_turbo_hitl_result,
    set_skill_turbo_hitl_tic,
)

TOOL_NAME = "skill_turbo_hitl_repro_tool"


class _StreamSession:
    def __init__(self):
        self.chunks = []

    async def write_stream(self, chunk):
        self.chunks.append(chunk)


def _make_tic() -> ToolInterruptException:
    inner_tc = SimpleNamespace(
        id="skill_turbo-tc-ask_user-stub-0",
        name="ask_user",
        arguments={"questions": [{"question": "页数"}]},
    )
    return ToolInterruptException(
        request=InterruptRequest(message="页数"),
        tool_call=inner_tc,
    )


def _hitl_result_marker() -> dict:
    return build_skill_turbo_hitl_result(_make_tic())


async def _hitl_stub(query: str = "") -> dict:
    set_skill_turbo_hitl_tic(_make_tic())
    return _hitl_result_marker()


class _HitlTool(Tool):
    def __init__(self, name: str):
        super().__init__(ToolCard(name=name, description="hitl repro"))
        self.calls = 0

    async def invoke(self, inputs, **kwargs):
        self.calls += 1
        set_skill_turbo_hitl_tic(_make_tic())
        return _hitl_result_marker()

    async def stream(self, inputs, **kwargs):
        yield await self.invoke(inputs, **kwargs)


class _HitlModel:
    """Fake model: always emits the HITL tool call.

    ImageModalityProbe may consume ``invoke`` calls before the ReAct loop;
    using a call counter would misfire on CI. Always returning the tool call
    is safe: if the TIE rewrite works, the loop pauses on the first tool
    execution (result_type=interrupt); if it fails, the loop exhausts
    max_iterations (result_type=error) — the assertion correctly fails.
    """

    def __init__(self):
        self.model_client_config = ModelClientConfig(
            client_provider="OpenAI", api_key="fake", api_base="https://fake.invalid"
        )
        self.model_config = ModelRequestConfig(model_name="fake")
        self.calls = []

    def _tool_call_message(self):
        from openjiuwen.core.foundation.llm import AssistantMessage

        return AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    id="call-hitl-1",
                    type="function",
                    name=TOOL_NAME,
                    arguments="{}",
                )
            ],
        )

    async def invoke(self, messages, **kwargs):
        self.calls.append(list(messages))
        return self._tool_call_message()

    async def stream(self, messages, **kwargs):
        self.calls.append(list(messages))
        yield AssistantMessageChunk(content="", tool_calls=self._tool_call_message().tool_calls)


@pytest.mark.asyncio
async def test_skill_turbo_hitl_tie_rewrite_survives_ability_manager_chain():
    tool_name = f"skill_turbo_hitl_repro_{uuid.uuid4().hex[:8]}"
    from openjiuwen.core.foundation.tool import LocalFunction

    tool = LocalFunction(
        ToolCard(id=tool_name, name=tool_name, description="repro", stateless=True),
        _hitl_stub,
    )
    Runner.resource_mgr.add_tool(tool)
    ability_manager = AbilityManager()
    ability_manager.add(tool.card)

    rail = JiuSwarmStreamEventRail()
    rail.set_skill_turbo_adapter(SimpleNamespace(_instance=None))
    ns = f"repro_{uuid.uuid4().hex[:8]}"
    agent = SimpleNamespace(agent_callback_manager=AgentCallbackManager(ns))
    await agent.agent_callback_manager.register_rail(rail, agent)

    session = _StreamSession()
    tool_call = ToolCall(
        id=f"call_{uuid.uuid4().hex[:24]}",
        type="function",
        name=tool_name,
        arguments="{}",
    )
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args={},
        ),
        config=None,
        session=session,
        context=None,
        extra={},
    )
    try:
        results = await ability_manager.execute(ctx, tool_call, session)
        result = results[0][0]
        assert isinstance(result, ToolInterruptException), (
            f"TIE rewrite ineffective: outer agent got {result!r} "
            "and will treat it as user-interrupted then abandon the turbo channel"
        )
    finally:
        Runner.resource_mgr.remove_tool(tool.card.id)
        await agent.agent_callback_manager.unregister_rail(rail, agent)


@pytest.mark.asyncio
async def test_skill_turbo_hitl_tie_rewrite_pauses_deep_agent_react_loop():
    await Runner.start()
    tool = _HitlTool(TOOL_NAME)
    model = _HitlModel()
    rail = JiuSwarmStreamEventRail()
    rail.set_skill_turbo_adapter(SimpleNamespace(_instance=None))
    core = create_deep_agent(
        model=model,
        tools=[tool],
        rails=[rail],
        system_prompt="Test",
        enable_task_loop=False,
        max_iterations=6,
    )
    sid = "hitl-tie-repro-" + uuid.uuid4().hex
    session = Session(session_id=sid)
    try:
        await core.start(session=session)
        result = await core.invoke({"query": "make a ppt"}, session=session)
        assert result.get("result_type") == "interrupt", (
            "outer ReAct loop consumed the HITL placeholder as a normal "
            f"tool_result and continued: got {result!r}"
        )
    finally:
        await core.stop()


@pytest.mark.asyncio
async def test_skill_turbo_hitl_tie_rewrite_survives_context_isolation(monkeypatch):
    """验证 ContextVar 跨上下文不可见时，返回值标记仍能完成 TIE 改写并暂停。"""
    monkeypatch.setattr(skill_turbo_tools, "get_skill_turbo_hitl_tic", lambda: None)
    await Runner.start()
    tool = _HitlTool(TOOL_NAME)
    model = _HitlModel()
    rail = JiuSwarmStreamEventRail()
    rail.set_skill_turbo_adapter(SimpleNamespace(_instance=None))
    core = create_deep_agent(
        model=model,
        tools=[tool],
        rails=[rail],
        system_prompt="Test",
        enable_task_loop=False,
        max_iterations=6,
    )
    sid = "hitl-tie-ctx-" + uuid.uuid4().hex
    session = Session(session_id=sid)
    try:
        await core.start(session=session)
        result = await core.invoke({"query": "make a ppt"}, session=session)
        assert result.get("result_type") == "interrupt", (
            "TIC invisible across contexts and result-marker rewrite "
            f"ineffective: outer agent got {result!r}"
        )
    finally:
        await core.stop()


def test_skill_turbo_hitl_result_marker_is_deepcopy_safe():
    """验证 HITL 标记可被 SDK tracer 对含工具返回值的 span 做 deepcopy。"""
    import copy

    marker = _hitl_result_marker()
    cloned = copy.deepcopy(marker)
    assert cloned == marker
    assert cloned["request"]["message"] == "页数"
