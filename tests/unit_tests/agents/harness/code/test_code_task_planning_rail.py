# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
import openjiuwen.harness.tools as tools_module

from jiuwenswarm.agents.harness.common.tools.todo_compat import (
    CompatibleTodoModifyTool,
)
from jiuwenswarm.agents.harness.code.rails.code_task_planning_rail import (
    CodeTaskPlanningRail,
)


def test_code_task_planning_rail_uses_work_mode_behavior() -> None:
    rail = CodeTaskPlanningRail()

    assert isinstance(rail, TaskPlanningRail)
    assert rail.inject_prompt is True
    assert CodeTaskPlanningRail.init is TaskPlanningRail.init
    assert CodeTaskPlanningRail.before_model_call is TaskPlanningRail.before_model_call
    assert CodeTaskPlanningRail.after_tool_call is TaskPlanningRail.after_tool_call
    assert CodeTaskPlanningRail.after_task_iteration is TaskPlanningRail.after_task_iteration
    assert tools_module.TodoModifyTool is CompatibleTodoModifyTool
