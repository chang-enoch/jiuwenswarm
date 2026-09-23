# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inject the team deliverable location policy into member prompts."""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail


class TeamDeliverableLocationRail(DeepAgentRail):
    """Tell team members to write user-facing deliverables into the task cwd.

    Team member file tools resolve relative paths against the member's cwd
    (the task project directory on desktop sessions), while the member's
    private workspace lives under the runtime data directory. Earlier team
    guidance steered deliverables into the private workspace / ``.team/``
    mount, where users cannot see them. This section sits after the team role
    policy and the report-path rule so it wins that conflict;
    the generic directory_boundaries section independently reinforces
    the same cwd-first rule at the tail of the prompt.
    """

    priority = 5
    SECTION_NAME = "team_deliverable_location"
    SECTION_PRIORITY = 68

    def __init__(
        self,
        *,
        project_dir: str,
        member_workspace_root: str | None = None,
        team_ws_root: str | None = None,
        language: str = "cn",
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._project_dir = project_dir
        self._member_workspace_root = member_workspace_root
        self._team_ws_root = team_ws_root
        self._language = language

    def init(self, agent) -> None:
        """Capture the prompt builder owned by the member."""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """Remove the injected policy section."""
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject the deliverable location policy before each model call."""
        _ = ctx
        if self.system_prompt_builder is None:
            return

        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={
                    "cn": self._build_cn_content(),
                    "en": self._build_en_content(),
                },
                priority=self.SECTION_PRIORITY,
            )
        )

    def _workspace_line(self, language: str) -> str:
        """Format the private-workspace contrast line for the prompt."""
        if not self._member_workspace_root:
            return ""
        if language == "cn":
            return (
                f"- 你的私有工作区 `{self._member_workspace_root}` 只用于存放记忆、技能视图和中间草稿等"
                "内部状态；不要把交付物写进去，用户在那里看不到文件。\n"
            )
        return (
            f"- Your private workspace `{self._member_workspace_root}` is only for internal state such as "
            "memory, skill views, and intermediate drafts; never write deliverables there, because users "
            "cannot see files in it.\n"
        )

    def _build_cn_content(self) -> str:
        """Build the Chinese prompt section."""
        if not self._project_dir:
            return self._build_cn_fallback()
        return (
            "# 交付物落盘位置\n\n"
            "**基本原则**：团队共享工作空间 `.team/` 只用于成员间交接的中间产物；"
            "面向用户的交付物必须写入任务工作目录，并在任务完成前用 `send_file_to_user` "
            "从任务工作目录发送——交付物若只留在 `.team/` 或你的私有工作区，用户拿不到。\n\n"
            "面向用户的交付物（报告、文档、表格、图片、导出文件、代码成果等）必须写入当前工作目录——"
            "即本任务的工作区：\n\n"
            f"- 任务工作区（当前工作目录 cwd）：`{self._project_dir}`\n"
            "- 写交付物时直接使用相对路径（如 `报告.md`），文件工具会解析到当前工作目录；"
            "所有团队成员共享同一工作目录，彼此可见。\n"
            f"{self._workspace_line('cn')}"
            "- 团队内部共享的中间产物仍可走团队共享工作空间 `.team/`；但当其他规则中"
            "\"产物落盘到 `.team/` 或私有工作区\"的默认约定与本节冲突时，以本节为准。\n"
            "- 读写团队共享工作空间时，使用你私有工作区内 `.team/` 挂载的绝对路径；"
            "不要以相对路径 `.team/...` 在当前工作目录下新建 `.team` 目录——"
            "那不是共享挂载，其他成员读不到。\n"
            "- 若你处于 worktree 隔离（当前工作目录是独立 worktree），交付物仍写入当前工作目录，"
            "合并与共享由团队流程处理。\n"
            "- 任务完成前调用 `send_file_to_user` 发送交付物，并向用户汇报交付物在任务工作区的"
            "真实绝对路径。"
        )

    def _build_cn_fallback(self) -> str:
        """Fallback content when the session has no bound project directory."""
        return (
            "# 交付物落盘位置\n\n"
            "本会话未绑定任务工作区（例外情形：正常情况下交付物应写入任务工作目录，"
            "团队共享工作空间只用于成员间交接）。面向用户的交付物（报告、文档、表格、图片、"
            "导出文件、代码成果等）必须写入团队共享工作区：\n\n"
            f"- 团队共享工作区：`{self._team_ws_root}`（使用绝对路径写入，成员间彼此可见）\n"
            f"{self._workspace_line('cn')}"
            "- 任务完成前调用 `send_file_to_user` 发送交付物，并向用户汇报交付物的真实绝对路径。"
        )

    def _build_en_fallback(self) -> str:
        """Fallback content when the session has no bound project directory."""
        return (
            "# Deliverable Location\n\n"
            "This session has no bound task workspace (an exception: normally deliverables "
            "go to the task working directory, and the team shared workspace is only for "
            "member-to-member handoffs). User-facing deliverables (reports, documents, "
            "spreadsheets, images, exports, code results, etc.) must be written into the "
            "team shared workspace:\n\n"
            f"- Team shared workspace: `{self._team_ws_root}` (write with absolute paths; "
            "all members can see each other's files there)\n"
            f"{self._workspace_line('en')}"
            "- Before completing the task, call `send_file_to_user` with each deliverable and "
            "report its real absolute path to the user."
        )

    def _build_en_content(self) -> str:
        """Build the English prompt section."""
        if not self._project_dir:
            return self._build_en_fallback()
        return (
            "# Deliverable Location\n\n"
            "**Core rule**: the team shared workspace `.team/` is only for member-to-member "
            "handoffs of intermediate artifacts. User-facing deliverables must be written "
            "into the task working directory and sent with `send_file_to_user` from there "
            "before the task completes — a deliverable left in `.team/` or in your private "
            "workspace never reaches the user.\n\n"
            "User-facing deliverables (reports, documents, spreadsheets, images, exports, "
            "code results, etc.) must be written into the current working directory — "
            "the workspace of this task:\n\n"
            f"- Task workspace (current working directory, cwd): `{self._project_dir}`\n"
            "- Use relative paths (e.g. `report.md`) when writing deliverables; file tools resolve them "
            "against the current working directory. All team members share the same working directory and "
            "can see each other's files there.\n"
            f"{self._workspace_line('en')}"
            "- Intermediate artifacts shared between members may still go to the team shared workspace "
            "`.team/`; whenever other rules default deliverables to `.team/` or to the private workspace "
            "and conflict with this section, this section wins.\n"
            "- When reading or writing the team shared workspace, use the absolute path of the `.team/` "
            "mount inside your private workspace; never create a `.team` directory under the current "
            "working directory via relative paths like `.team/...` — that is not the shared mount and "
            "other members cannot read it.\n"
            "- If you run under worktree isolation (the current working directory is a dedicated "
            "worktree), still write deliverables into the current working directory; merging and sharing "
            "are handled by the team workflow.\n"
            "- Before completing the task, call `send_file_to_user` with each deliverable and report its "
            "real absolute path under the task workspace to the user."
        )


__all__ = ["TeamDeliverableLocationRail"]
