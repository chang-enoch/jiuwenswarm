# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from jiuwenswarm.agents.harness.code.prompt.code_prompt_builder import build_code_system_prompt
from jiuwenswarm.agents.harness.code.prompt.code_todo_tool_prompts import (
    CODE_TODO_CREATE_DESCRIPTION_EN,
    CODE_TODO_GET_DESCRIPTION_EN,
    CODE_TODO_LIST_DESCRIPTION_EN,
    CODE_TODO_MODIFY_DESCRIPTION_EN,
    CODE_TODO_TOOL_PROMPTS,
    get_code_todo_create_input_params,
)
from openjiuwen.harness.prompts.sections import context


def _build_tools_content_en() -> str:
    """Return the merged Tool Usage Rules content used by all three modes."""
    from types import SimpleNamespace
    from openjiuwen.core.foundation.tool.base import ToolCard
    ability_manager = SimpleNamespace(list=lambda: [ToolCard(name="read_file")])
    content = context.build_tools_content(ability_manager, language="en")
    assert content is not None
    return content


def test_todo_create_tool_prompt_is_usage_only():
    assert "## Usage" in CODE_TODO_CREATE_DESCRIPTION_EN
    assert "id, content, activeForm, and description" in CODE_TODO_CREATE_DESCRIPTION_EN
    assert "replaces the current list" in CODE_TODO_CREATE_DESCRIPTION_EN
    assert "todo_modify" in CODE_TODO_CREATE_DESCRIPTION_EN
    assert "When to skip" not in CODE_TODO_CREATE_DESCRIPTION_EN
    assert "2–3" not in CODE_TODO_CREATE_DESCRIPTION_EN
    assert "4–6 max" not in CODE_TODO_CREATE_DESCRIPTION_EN


def test_todo_modify_tool_prompt_is_actions_only():
    assert "## Actions" in CODE_TODO_MODIFY_DESCRIPTION_EN
    assert "todo_create" in CODE_TODO_MODIFY_DESCRIPTION_EN
    assert "Avoid todo-only rounds" not in CODE_TODO_MODIFY_DESCRIPTION_EN
    assert "2–3" not in CODE_TODO_MODIFY_DESCRIPTION_EN


def test_todo_list_and_get_tool_prompts_are_short():
    assert "Prefer todo_modify" in CODE_TODO_LIST_DESCRIPTION_EN
    assert "id" in CODE_TODO_GET_DESCRIPTION_EN.lower()
    assert "Do not call routinely" not in CODE_TODO_LIST_DESCRIPTION_EN


def test_todo_create_schema_describes_required_fields():
    params = get_code_todo_create_input_params()
    tasks_desc = params["properties"]["tasks"]["description"]
    assert "id, content, activeForm, and description" in tasks_desc
    assert "2–3" not in tasks_desc
    assert "4–6 max" not in tasks_desc
    item_required = params["properties"]["tasks"]["items"]["required"]
    assert item_required == ["id", "content", "activeForm", "description"]


def test_code_system_prompt_has_task_planning_section():
    text = _build_tools_content_en()
    assert "# Tool Usage Rules" in text
    assert "## Task planning (todos)" in text
    assert "2–3 outcome-based milestones" in text
    assert "4–6 milestones max" in text
    assert "avoid todo-only rounds" in text
    assert "don't batch" not in text.lower()


def test_code_system_prompt_does_not_reference_disabled_subagents():
    prompt = build_code_system_prompt()
    assert "explore_agent" not in prompt
    assert "plan_agent" not in prompt


def test_code_system_prompt_keeps_media_generation_routing_in_skills():
    prompt = build_code_system_prompt()
    assert "# Doing tasks" in prompt
    assert "media deliverable" not in prompt
    assert "If the user wants an image, video, or audio file generated" not in prompt
    assert "If the user asks for help or wants to give feedback" not in prompt
    assert "## Generative media skills" not in prompt
    assert "`seedream-image-gen`" not in prompt
    assert "`invoke`" not in prompt
    assert "PluginSkillExecTool" not in prompt
    assert "seedreamLite4Skill" not in prompt


def test_all_code_todo_tools_registered():
    assert set(CODE_TODO_TOOL_PROMPTS) == {
        "todo_create",
        "todo_list",
        "todo_get",
        "todo_modify",
    }
