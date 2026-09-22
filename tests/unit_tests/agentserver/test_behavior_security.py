import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.harness.rails.interrupt.interrupt_base import ApproveResult
from openjiuwen.harness.security import PermissionLevel, PermissionResult
from jiuwenswarm.common.behavior_security import (
    BehaviorSecurityBridge, check_agent_skill_install, raw_event,
)
from jiuwenswarm.agents.harness.common.rails.behavior_security_rail import BehaviorSecurityRail


def context(session=None):
    if session is None:
        data = {}
        session = SimpleNamespace(get_session_id=lambda: 'session', get_state=data.get, update_state=data.update)
    call = ToolCall(id='call-1', type='function', name='install_skill', arguments='{}')
    return SimpleNamespace(session=session, exception=None, extra={}, inputs=SimpleNamespace(
        tool_call=call, tool_name=call.name, tool_args=call.arguments, tool_result=None, execution_started=False))


def bridge(enabled=True, decision='allow'):
    events = []
    async def handler(request):
        events.append(json.loads(request.content))
        return httpx.Response(200, json={'decision': decision})
    b = BehaviorSecurityBridge({'permissions': {'enabled': True}, 'xiaoyi_work_security': {
        'cloud_authorization': {'enabled': enabled}}}, transport=httpx.MockTransport(handler))
    return b, events


@pytest.mark.asyncio
@pytest.mark.parametrize('level', [PermissionLevel.ALLOW, PermissionLevel.ASK, PermissionLevel.DENY])
async def test_explicit_rules_report_without_cloud_wait(level):
    b, events = bridge()
    wait = asyncio.Event()
    async def delayed(_):
        await wait.wait()
        return {'decision': 'deny'}
    b._send = AsyncMock(side_effect=delayed)
    result = await asyncio.wait_for(b.decide(raw_event(context(), 'tool.before'), PermissionResult(level)), .1)
    assert result.permission == level
    await asyncio.sleep(0)
    assert b._send.call_args.args[0]['mode'] == 'report'
    await b.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('decision,expected', [('allow', PermissionLevel.ALLOW), ('deny', PermissionLevel.DENY),
    ('clarify', PermissionLevel.ALLOW), ('unavailable', PermissionLevel.ASK)])
async def test_unknown_one_waited_request_and_no_second_report(decision, expected):
    b, events = bridge(decision=decision)
    event = raw_event(context(), 'tool.before')
    for _ in range(2):
        result = await b.decide(event, PermissionResult(PermissionLevel.UNDETERMINED))
        assert result.permission == expected
    assert len(events) == 1
    assert events[0]['mode'] == 'authorize'
    assert 'businessId' not in events[0]


@pytest.mark.asyncio
async def test_cloud_switch_off_reports_and_asks():
    b, events = bridge(enabled=False)
    result = await b.decide(raw_event(context(), 'tool.before'), PermissionResult(PermissionLevel.UNDETERMINED))
    assert result.needs_approval
    await asyncio.gather(*b.tasks)
    assert events[0]['mode'] == 'report'


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [True, False])
async def test_install_confirmation_stops_before_write_and_resumes_once(tmp_path, approved):
    b, events = bridge(decision='unavailable')
    rail = BehaviorSecurityRail(b)
    ctx = context()
    staged = tmp_path / 'staged'
    staged.mkdir()
    (staged / 'SKILL.md').write_text('skill content')
    dest = tmp_path / 'installed'
    dest.mkdir()
    old = dest / 'SKILL.md'
    old.write_text('original')
    await rail.before_tool_call(ctx)
    with pytest.raises(PermissionError):
        await check_agent_skill_install(staged, dest, 'example', 'download')
    assert old.read_text() == 'original'
    assert events[0]['stage'] == 'skill.before_install'
    ctx.inputs.tool_result = {'success': False}
    with pytest.raises(AbortError):
        await rail.after_tool_call(ctx)
    resumed = context(ctx.session)
    resumed.extra[RESUME_USER_INPUT_KEY] = {'approved': approved}
    await rail.before_tool_call(resumed)
    if approved:
        await check_agent_skill_install(staged, dest, 'example', 'download')
    else:
        with pytest.raises(PermissionError, match='用户拒绝'):
            await check_agent_skill_install(staged, dest, 'example', 'download')
    assert len(events) == 1
    assert old.read_text() == 'original'
    await rail.after_tool_call(resumed)


@pytest.mark.asyncio
async def test_output_is_report_only_and_deny_has_no_output():
    b, events = bridge(decision='deny')
    rail = BehaviorSecurityRail(b)
    ctx = context()
    await rail.before_tool_call(ctx)
    ctx.extra['_interrupt_decision'] = ApproveResult()
    ctx.inputs.tool_result = {'result': 'ok'}
    ctx.inputs.execution_started = True
    await rail.after_tool_call(ctx)
    await asyncio.gather(*b.tasks)
    assert len(events) == 1 and events[0]['stage'] == 'tool.after' and events[0]['mode'] == 'report'
    assert ctx.inputs.tool_result == {'result': 'ok'}


@pytest.mark.asyncio
async def test_cancelled_cloud_request_propagates():
    b, _ = bridge()
    b._send = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await b.decide(raw_event(context(), 'tool.before'), PermissionResult(PermissionLevel.UNDETERMINED))


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['allow', 'deny'])
async def test_actual_builtin_install_checkpoint(tmp_path, monkeypatch, decision):
    from jiuwenswarm.agents.harness.common.tools.skill_toolkits import SkillToolkit
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
    name = 'xiaoyi-security-smoke'
    builtin = tmp_path / 'builtin'
    src = builtin / name
    src.mkdir(parents=True)
    (src / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: Synthetic installation test skill.\nversion: 1.0.0\n---\n'
        '\n# Installation test\n\nRespond with XIAOYI_SKILL_INSTALL_OK.\n',
        encoding='utf-8',
    )
    monkeypatch.setattr('jiuwenswarm.server.runtime.skill.skill_manager.get_builtin_skills_dir', lambda: builtin)
    manager = SkillManager(workspace_dir=str(tmp_path / 'workspace'))
    toolkit = SkillToolkit(manager)
    b, events = bridge(decision=decision)
    rail = BehaviorSecurityRail(b)
    ctx = context()
    await rail.before_tool_call(ctx)
    try:
        result = await toolkit.install_skill(identifier=name, source='builtin')
        dest = manager._skills_dir / name
        assert result['success'] is (decision == 'allow')
        assert dest.exists() is (decision == 'allow')
        assert len(events) == 1
        assert events[0]['stage'] == 'skill.before_install'
        assert events[0]['mode'] == 'authorize'
        if decision == 'allow':
            assert (dest / 'SKILL.md').read_bytes() == (src / 'SKILL.md').read_bytes()
            assert any(item['name'] == name for item in manager.get_installed_plugins())
        else:
            assert not any(item['name'] == name for item in manager.get_installed_plugins())
    finally:
        await rail.after_tool_call(ctx)
        await b.close()


@pytest.mark.asyncio
async def test_desktop_permission_factory_falls_back_to_real_interrupt(monkeypatch):
    from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import build_permission_rail
    from jiuwenswarm.agents.harness.common.channel_runtime_context import CURRENT_CHANNEL_ID
    from openjiuwen.harness.rails.interrupt.interrupt_base import InterruptResult
    monkeypatch.setenv('CLAW_BEHAVIOR_SECURITY', '1')
    monkeypatch.setenv('CLAW_SKILL_TOKEN', 'test-token')
    cfg = {'enabled': True, 'package_builtin_rules': False, 'defaults': {'*': 'allow'}}
    monkeypatch.setattr('jiuwenswarm.agents.harness.common.rails.permissions.permissions_persist.get_permissions_with_session_overlay',
                        lambda **_: cfg.copy())
    b, events = bridge(decision='unavailable')
    ctx = context()
    rail = BehaviorSecurityRail(b)
    await rail.before_tool_call(ctx)
    channel = CURRENT_CHANNEL_ID.set('desktop')
    try:
        permission = build_permission_rail({'permissions': cfg})
        assert permission is not None
        result = await permission.resolve_interrupt(ctx, ctx.inputs.tool_call, None)
        assert isinstance(result, InterruptResult)
        assert len(events) == 1 and events[0]['mode'] == 'authorize'
    finally:
        CURRENT_CHANNEL_ID.reset(channel)
        await rail.after_tool_call(ctx)


@pytest.mark.asyncio
async def test_real_core_callback_chain_install_interrupt(tmp_path):
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs, AgentCallbackEvent
    from openjiuwen.core.single_agent.agent_callback_manager import AgentCallbackManager
    from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
    b, events = bridge(decision='unavailable')
    rail = BehaviorSecurityRail(b)
    callback_manager = AgentCallbackManager('behavior-security-test')
    agent = SimpleNamespace(agent_callback_manager=callback_manager)
    # Register callbacks via the actual core callback framework (which unwraps AbortError).
    await callback_manager.register_callback(AgentCallbackEvent.BEFORE_TOOL_CALL, rail.before_tool_call, 100)
    await callback_manager.register_callback(AgentCallbackEvent.AFTER_TOOL_CALL, rail.after_tool_call, 100)
    simple = context()
    ctx = AgentCallbackContext(agent=agent, session=simple.session, inputs=ToolCallInputs(
        tool_call=simple.inputs.tool_call, tool_name='install_skill', tool_args='{}'))
    staged = tmp_path / 'SKILL.md'
    staged.write_text('test')
    manager = AbilityManager()
    async def invoke(**_):
        try:
            await check_agent_skill_install(staged, tmp_path / 'dest', 'test', 'download')
        except PermissionError:
            return {'success': False}, None
        return {'success': True}, None
    manager._execute_single_tool_call = invoke
    with pytest.raises(ToolInterruptException):
        await manager._railed_execute_single_tool_call(ctx, simple.inputs.tool_call, simple.session)
    assert ctx.inputs.execution_started
    assert len(events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', ['deny', 'unavailable'])
async def test_skillnet_worker_stops_before_force_replace(tmp_path, monkeypatch, decision):
    from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
    manager = SkillManager(workspace_dir=str(tmp_path / 'workspace'))
    src = tmp_path / 'downloaded'
    src.mkdir()
    (src / 'SKILL.md').write_text('---\nname: existing\ndescription: new\n---\nnew content')
    dest = manager._skills_dir / 'existing'
    dest.mkdir(parents=True)
    old = dest / 'SKILL.md'
    old.write_text('original content')
    monkeypatch.setattr(manager, '_skillnet_download_sync', lambda *args: str(src))
    b, events = bridge(decision=decision)
    rail = BehaviorSecurityRail(b)
    ctx = context()
    await rail.before_tool_call(ctx)
    result = await asyncio.to_thread(manager._skillnet_install_files_sync, 'https://example.test/skill', True)
    assert not result['ok']
    assert old.read_text() == 'original content'
    assert len(events) == 1
    if decision == 'unavailable':
        with pytest.raises(AbortError):
            await rail.after_tool_call(ctx)
    else:
        await rail.after_tool_call(ctx)
