# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task-planning rail shared by work, code, and design modes."""

from __future__ import annotations

from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

from jiuwenswarm.agents.harness.common.tools.todo_compat import (
    install_todo_modify_compat_patch,
)


class CodeTaskPlanningRail(TaskPlanningRail):
    """Use the same todo tools and task-planning behavior as work mode.

    The compatibility class name is retained because code/design assembly and
    swarm manifests reference it. All behavior, tool metadata, schemas, prompt
    injection, model selection, and progress handling come from the upstream
    ``TaskPlanningRail`` used by work mode.
    """

    def __init__(self, *args, **kwargs) -> None:
        install_todo_modify_compat_patch()
        super().__init__(*args, **kwargs)
