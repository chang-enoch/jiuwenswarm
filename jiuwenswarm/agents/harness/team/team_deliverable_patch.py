# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime patches steering team deliverables into the task workspace.


Three patches, all against the installed openjiuwen package (read-only):

1. ``_patch_worker_cwd`` — ``TeamWorkerBackend._setup_worker_workspace``
   returns ``cwd=None`` when no worktree is requested, and the caller writes
   that ``None`` into the worker spec, so the worker's cwd falls back to its
   private workspace and every relative-path write lands there. The wrapper
   backfills the base spec's cwd (the project dir injected at assembly time).
   Becomes a natural no-op once upstream fixes the ``None`` overwrite.

2. ``_patch_workspace_labels`` — the per-spawn identity block
   (``prompts/messages.py`` ``_LABELS``) described the private workspace as
   "存放你自己的产物 / holds your own artifacts", steering members to write
   deliverables into the private workspace. Rewrite both languages.

3. ``_patch_policy_templates`` — ``teammate_policy`` / ``leader_policy`` /
   ``leader_workflow`` / ``leader_workflow_hybrid`` teach "delivery documents
   go to ``.team/``". ``load_template`` re-reads the file per call (the
   ``@cache`` is on the raw loader, and we never mutate the cached template),
   so wrapping ``load_template`` applies sentence-level substitutions that
   narrow ``.team/`` to member-to-member handoffs and route user-facing
   deliverables to the task working directory.

4. Same wrapper, ``_ASK_USER_TEMPLATE_REPLACEMENTS`` — ask-user routing.
   ``ask_user`` is leader-only (teammates are headless and cannot serve user
   interrupts), but the templates never said so: leader workflow templates
   now name the ``ask_user`` tool explicitly, and ``teammate_policy`` tells
   members to route user-facing questions to the leader via ``send_message``
   instead of trying to ask the user themselves.

Every sub-patch is idempotent, detects version drift (missing symbols /
unmatched patterns) and degrades to a warning — never raises at startup.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

logger = logging.getLogger(__name__)

_WORKER_CWD_FLAG = "_team_worker_cwd_patch_applied"


def _patch_worker_cwd() -> None:
    """Backfill worker cwd with the base spec's cwd when no worktree is used."""
    from openjiuwen.agent_teams.workflow.backends.team_worker_backend import (
        TeamWorkerBackend,
    )

    if getattr(TeamWorkerBackend, _WORKER_CWD_FLAG, False):
        return
    original = TeamWorkerBackend._setup_worker_workspace  # noqa: SLF001

    @functools.wraps(original)
    def _patched(self: Any, member_name: str) -> Any:
        workspace, cwd = original(self, member_name)
        if cwd is None:
            # Upstream returns None ("use the workspace root"), which makes the
            # worker's cwd its private workspace; keep the assembly-time
            # project dir instead so deliverables land in the task workspace.
            cwd = getattr(getattr(self, "_worker_base_spec", None), "cwd", None)
        return workspace, cwd

    TeamWorkerBackend._setup_worker_workspace = _patched  # noqa: SLF001
    setattr(TeamWorkerBackend, _WORKER_CWD_FLAG, True)
    logger.info("Applied team worker cwd patch to TeamWorkerBackend")


_MEMBER_WORKSPACE_PURPOSE = {
    "cn": (
        "存放记忆、技能视图与中间草稿等内部状态；面向用户的交付物必须写入任务工作目录"
        "（当前工作目录），不要放这里，也不要把新 skill 创建到这里"
    ),
    "en": (
        "Holds internal state only: memory, skill views and intermediate drafts. "
        "User-facing deliverables must be written to the task working directory "
        "(current working directory), not here; new skills must not be created here either"
    ),
}

_TEAM_WORKSPACE_PURPOSE = {
    "cn": (
        "用于存放团队共享的中间产物（方案、设计草稿、成员间交接文件），"
        "所有成员通过该路径前缀读写同一份文件，系统自动管理版本和文件锁；"
        "面向用户的最终交付物不放这里"
    ),
    "en": (
        "Holds team-shared intermediate artifacts (plans, design drafts, member "
        "handoff files); all members read/write the same files through this path "
        "prefix. Versioning and file locks are managed automatically. User-facing "
        "final deliverables do not belong here"
    ),
}


def _patch_workspace_labels() -> None:
    """Rewrite workspace-purpose labels in the per-spawn identity block."""
    from openjiuwen.agent_teams.prompts import messages as team_messages

    labels = getattr(team_messages, "_LABELS", None)
    if not isinstance(labels, dict):
        logger.warning("team prompt patch: messages._LABELS missing (agent-core drift?)")
        return
    for lang, table in labels.items():
        if not isinstance(table, dict):
            continue
        member_text = _MEMBER_WORKSPACE_PURPOSE.get(lang)
        team_text = _TEAM_WORKSPACE_PURPOSE.get(lang)
        if member_text is None or team_text is None:
            continue
        for key, new_text in (
                ("member_workspace_purpose", member_text),
                ("team_workspace_purpose", team_text),
        ):
            if key not in table:
                logger.warning(
                    "team prompt patch: _LABELS[%s][%s] missing (agent-core drift?)",
                    lang, key,
                )
                continue
            table[key] = new_text
    logger.info("Applied team workspace label patch to messages._LABELS")


# Keyed by (template_name, language); each entry is (old, new) exact sentence
# pairs. Unmatched patterns log a warning (version drift) and pass through.
_DELIVERABLE_TEMPLATE_REPLACEMENTS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("teammate_policy", "cn"): ((
                                    "**成型产物走文件**——调研报告、方案全文、代码、数据表、长清单、汇总或交付文档"
                                    "这类复杂、大量、需要被反复查阅的内容，先落盘成**团队共享工作空间 `.team/` 下的"
                                    "文件**（写自己工作目录别人读不到），`send_message` 里只写**文件路径 + 一两句摘要**"
                                    "，不要把正文贴进消息。这条对 Leader 和其他成员一视同仁",
                                    "**成型产物走文件**——调研报告、方案全文、代码、数据表、长清单、汇总等**成员间"
                                    "交接**的复杂内容，先落盘成**团队共享工作空间 `.team/` 下的文件**（用你私有工作区"
                                    "内 `.team/` 挂载的绝对路径读写；写自己工作目录别人读不到），`send_message` 里只写"
                                    "**文件路径 + 一两句摘要**，不要把正文贴进消息。**面向用户的交付文档不在此列**："
                                    "它必须写入当前工作目录（任务工作区），并在任务完成前用 `send_file_to_user` 发送。"
                                    "这条对 Leader 和其他成员一视同仁",
                                ),),
    ("teammate_policy", "en"): ((
                                    "**Finished artifacts go through files** — research reports, full proposals, "
                                    "code, data tables, long checklists, synthesis or delivery documents: content "
                                    "that is complex, bulky, or meant to be consulted repeatedly must first be "
                                    "written to a file **in the shared team workspace under `.team/`** (files in "
                                    "your own working directory are unreadable by others); `send_message` then "
                                    "carries only the **file path plus a one- or two-sentence summary**, never "
                                    "the body itself. This applies equally to the Leader and to other members",
                                    "**Finished artifacts go through files** — research reports, full proposals, "
                                    "code, data tables, long checklists and other **member-to-member handoff** "
                                    "content that is complex, bulky, or meant to be consulted repeatedly must "
                                    "first be written to a file **in the shared team workspace under `.team/`** "
                                    "(read/write via the absolute path of the `.team/` mount inside your private "
                                    "workspace; files in your own working directory are unreadable by others); "
                                    "`send_message` then carries only the **file path plus a one- or two-sentence "
                                    "summary**, never the body itself. **User-facing delivery documents are the "
                                    "exception**: they must be written into the current working directory (the "
                                    "task workspace) and sent with `send_file_to_user` before the task completes. "
                                    "This applies equally to the Leader and to other members",
                                ),),
    ("leader_policy", "cn"): ((
                                  "交接用的文件必须落在团队共享工作空间 `.team/` 下，否则（尤其在 worktree 隔离下）"
                                  "其他成员读不到。创建调研 / 汇总类任务时，在 content 里写清产物应落到 `.team/` "
                                  "的哪个路径",
                                  "成员间交接用的文件必须落在团队共享工作空间 `.team/` 下（经成员私有工作区内的 "
                                  "`.team/` 挂载读写），否则（尤其在 worktree 隔离下）其他成员读不到；**面向用户的"
                                  "最终交付物例外——必须写入任务工作区（当前工作目录），并在完成前用 "
                                  "`send_file_to_user` 发送**。创建调研 / 汇总类任务时，在 content 里写清中间产物"
                                  "应落到 `.team/` 的哪个路径、最终交付物写入任务工作区",
                              ),),
    ("leader_policy", "en"): ((
                                  "Handoff files must land in the shared team workspace under `.team/`, "
                                  "otherwise other members cannot read them (especially under worktree "
                                  "isolation). When creating research / synthesis tasks, state in the content "
                                  "which `.team/` path the artifact must be written to",
                                  "Member-to-member handoff files must land in the shared team workspace under "
                                  "`.team/` (via the `.team/` mount inside each member's private workspace), "
                                  "otherwise other members cannot read them (especially under worktree "
                                  "isolation); **user-facing final deliverables are the exception — they must "
                                  "be written into the task workspace (current working directory) and sent with "
                                  "`send_file_to_user` before completion**. When creating research / synthesis "
                                  "tasks, state in the content which `.team/` path intermediate artifacts go to "
                                  "and that final deliverables go to the task workspace",
                              ),),
    ("leader_workflow", "cn"): ((
                                    "要求把结论写成 `.team/` 下的文件；",
                                    "要求把结论写成团队共享工作空间 `.team/` 挂载下的文件"
                                    "（用挂载的绝对路径，不要在工作目录下新建 `.team` 目录）；",
                                ),),
    ("leader_workflow", "en"): ((
                                    "require it to write the findings to a file under `.team/`.",
                                    "require it to write the findings to a file under the team shared workspace "
                                    "`.team/` mount (use the absolute mount path; do not create a `.team` "
                                    "directory in the working directory).",
                                ),),
    ("leader_workflow_hybrid", "cn"): ((
                                           "要求其把结论写成 `.team/` 下的文件；",
                                           "要求其把结论写成团队共享工作空间 `.team/` 挂载下的文件"
                                           "（用挂载的绝对路径，不要在工作目录下新建 `.team` 目录）；",
                                       ),),
    ("leader_workflow_hybrid", "en"): ((
                                           "requiring it to write the findings to a file under `.team/`.",
                                           "requiring it to write the findings to a file under the team shared workspace "
                                           "`.team/` mount (use the absolute mount path; do not create a `.team` "
                                           "directory in the working directory).",
                                       ),),
}

_LOAD_TEMPLATE_FLAG = "_team_template_patch_applied"

# Ask-user routing (design: ask_user is leader-only). The leader workflow
# templates name the tool explicitly; teammate_policy routes member questions
# to the leader. Same exact-match + drift-warning semantics as above.
_ASK_USER_TEMPLATE_REPLACEMENTS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {
    ("leader_workflow", "cn"): ((
                                    "如有歧义先向用户提问；",
                                    "如有歧义，调用 `ask_user` 工具向用户提问"
                                    "（完整问题写进工具参数；写在回复正文里不算提问）；",
                                ),),
    ("leader_workflow_predefined", "cn"): ((
                                               "如有歧义先向用户提问；",
                                               "如有歧义，调用 `ask_user` 工具向用户提问"
                                               "（完整问题写进工具参数；写在回复正文里不算提问）；",
                                           ),),
    ("leader_workflow_hybrid", "cn"): ((
                                           "如有歧义先向用户提问；",
                                           "如有歧义，调用 `ask_user` 工具向用户提问"
                                           "（完整问题写进工具参数；写在回复正文里不算提问）；",
                                       ),),
    ("leader_workflow", "en"): ((
                                    "Ask the user if anything is ambiguous.",
                                    "If anything is ambiguous, call the `ask_user` tool to ask the user "
                                    "(put the full question in the tool arguments; writing a question in "
                                    "your reply body does not count as asking).",
                                ),),
    ("leader_workflow_predefined", "en"): ((
                                               "Ask the user if anything is ambiguous.",
                                               "If anything is ambiguous, call the `ask_user` tool to ask the user "
                                               "(put the full question in the tool arguments; writing a question in "
                                               "your reply body does not count as asking).",
                                           ),),
    ("leader_workflow_hybrid", "en"): ((
                                           "Ask the user if anything is ambiguous.",
                                           "If anything is ambiguous, call the `ask_user` tool to ask the user "
                                           "(put the full question in the tool arguments; writing a question in "
                                           "your reply body does not count as asking).",
                                       ),),
    ("teammate_policy", "cn"): ((
                                    "遇到**方向性阻塞**（需求不清晰、目标冲突）时升级给 Leader",
                                    "遇到**方向性阻塞**（需求不清晰、目标冲突）时升级给 Leader——你没有 "
                                    "`ask_user` 工具、不能直接向用户提问（问题写在正文里用户也看不到），"
                                    "需要用户澄清的问题一律 `send_message` 给 Leader 代问",
                                ),),
    ("teammate_policy", "en"): ((
                                    "Escalate **directional blockers** (unclear requirements, goal conflicts) to Leader",
                                    "Escalate **directional blockers** (unclear requirements, goal conflicts) to "
                                    "Leader — you have no `ask_user` tool and cannot ask the user directly "
                                    "(questions in your reply body are invisible to the user); route anything "
                                    "needing user clarification to the Leader via `send_message`",
                                ),),
}

# Merged view consumed by the load_template wrapper: multiple maps may target
# the same (name, language), so entries concatenate rather than overwrite.
_TEMPLATE_REPLACEMENTS: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
for _source in (_DELIVERABLE_TEMPLATE_REPLACEMENTS, _ASK_USER_TEMPLATE_REPLACEMENTS):
    for _key, _pairs in _source.items():
        _TEMPLATE_REPLACEMENTS[_key] = _TEMPLATE_REPLACEMENTS.get(_key, ()) + _pairs


def _wrap_load_template(original: Any) -> Any:
    """Wrap ``load_template`` applying deliverable-location substitutions."""

    @functools.wraps(original)
    def _patched(name: str, language: str = "cn") -> Any:
        template = original(name, language)
        replacements = _TEMPLATE_REPLACEMENTS.get((name, language))
        if not replacements or not isinstance(template.content, str):
            return template
        content = template.content
        for old, new in replacements:
            if old in content:
                content = content.replace(old, new)
            elif new not in content:
                logger.warning(
                    "team prompt patch: pattern not found in %s/%s (agent-core drift?)",
                    language, name,
                )
        if content == template.content:
            return template
        # Never mutate the cached template: return a patched copy.
        return template.model_copy(update={"content": content})

    return _patched


def _patch_policy_templates() -> None:
    """Patch both ``load_template`` lookup sites (loader + sections)."""
    from openjiuwen.agent_teams.prompts import loader, sections

    for module in (loader, sections):
        current = module.load_template
        if getattr(current, _LOAD_TEMPLATE_FLAG, False):
            continue
        wrapped = _wrap_load_template(current)
        setattr(wrapped, _LOAD_TEMPLATE_FLAG, True)
        module.load_template = wrapped
    logger.info("Applied team policy template patch to load_template")


def apply_team_deliverable_patches() -> None:
    """Apply all team deliverable-location patches (idempotent, fail-soft)."""
    for patch in (
            _patch_worker_cwd,
            _patch_workspace_labels,
            _patch_policy_templates,
    ):
        try:
            patch()
        except Exception:  # noqa: BLE001 — patches must never block startup
            logger.warning("team deliverable patch %s failed", patch.__name__, exc_info=True)


__all__ = ["apply_team_deliverable_patches"]
