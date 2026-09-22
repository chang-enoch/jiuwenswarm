# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Inject per-skill credentials into shell tool calls via tool_args["env"].

另含 SKILL_GATE 闸门：命令以「解释器 + 脚本」形态直接运行另一个有凭据技能
的脚本而该技能未激活时，拒绝执行并指引先调用 skill_tool 激活，防止凭据
静默缺失（脚本缺 key 失败）与跨技能凭据泄漏。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.rails.read_file_validation import (
    resolve_language_from_context,
)
from jiuwenswarm.agents.harness.common.rails.skill_active_state import (
    get_session_active_skill,
    resolve_skill_session_id,
)

logger = logging.getLogger(__name__)


def _nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


SHELL_PERMISSION_TOOLS = frozenset(
    {"bash", "shell", "mcp_exec_command", "create_terminal", "exec_command"}
)
_DEFAULT_SHELL_TYPES = frozenset({"auto", "cmd", "bash", "sh"})

SKILL_GATE_PREFIX = "[SKILL_GATE]"

GATE_MESSAGE_CN = (
    f"{SKILL_GATE_PREFIX} 检测到直接运行技能「{{skill}}」的脚本，但当前激活的技能不是它，"
    "其凭据环境变量不会被注入，脚本将因缺少 key 而失败。"
    '请先调用 skill_tool(skill_name="{skill}")，然后重新执行原命令。'
)
GATE_MESSAGE_EN = (
    f"{SKILL_GATE_PREFIX} The command directly runs a script of skill \"{{skill}}\", "
    "which is not the active skill: its credential env vars will NOT be injected "
    'and the script would fail on a missing key. Call '
    'skill_tool(skill_name="{skill}") first, then rerun the original command.'
)

# 「技能路径 + 脚本」形态：含路径分隔符且以 .py/.js/.mjs 结尾的 token
# （归一化 \ → / 后匹配）。不要求解释器前缀——真实命令的解释器常为带
# 引号的绝对路径（"C:\...\python3.exe" "D:\...\skill\scripts\x.py"）。
# token 允许成对引号包裹（含空格路径）；pip3 -r .../requirements.txt、
# cat .../SKILL.md 等非 .py/.js/.mjs 结尾的引用天然不命中。
_SCRIPT_TOKEN_RE = re.compile(
    r"""(?:"[^"]*/[^"]*\.(?:py|js|mjs)\b"|'[^']*/[^']*\.(?:py|js|mjs)\b'|[^"'\s]*/[^"'\s]*\.(?:py|js|mjs)\b)""",
    re.IGNORECASE,
)


def _iter_script_tokens(command: str) -> Iterable[str]:
    for match in _SCRIPT_TOKEN_RE.finditer(command):
        token = match.group(0).strip()
        if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
            token = token[1:-1]
        yield token


def match_skills_in_command(command: str, known_skills: Iterable[str]) -> list[str]:
    """Return every distinct known skill referenced by a script-path token.

    命令归一化（反斜杠→斜杠）后，提取所有「含路径分隔符且以
    .py/.js/.mjs 结尾」的 token（引号包裹的含空格路径亦可），对每个
    token 的路径**逐段**扫描，任一段精确命中已知技能名即算引用（整段
    边界，mx-a-x 不会误匹配 mx-a；大小写不敏感）。覆盖
    ``<skill>/scripts/x.py`` 子目录与 ``<skill>/x.py`` 根目录两种布局，
    以及带引号绝对路径解释器的真实调用形态。按命令中出现顺序去重返回。

    注意不能用「/skill/ 后缀到脚本」的单个捕获组正则：finditer 非重叠
    匹配会让最靠前的无关段（如 /Object/、/relay-claw/）吞掉整个匹配，
    真正的技能段轮不到检查。
    """
    if not command or not known_skills:
        return []
    canonical: dict[str, str] = {}
    for name in known_skills:
        text = _nonempty_str(name)
        if text:
            canonical.setdefault(text.lower(), text)
    if not canonical:
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for token in _iter_script_tokens(command.replace("\\", "/")):
        for segment in re.split(r"/+", token):
            name = canonical.get(segment.strip().lower())
            if name is not None and name not in seen:
                seen.add(name)
                matched.append(name)
    return matched


def match_skill_in_command(command: str, known_skills: Iterable[str]) -> str | None:
    """Return the first known skill referenced by the command, else None."""
    matches = match_skills_in_command(command, known_skills)
    return matches[0] if matches else None


def build_skill_gate_message(skill: str, language: str = "cn") -> str:
    template = GATE_MESSAGE_EN if str(language).lower() == "en" else GATE_MESSAGE_CN
    return template.format(skill=skill)


def _write_gate_tool_result(ctx: AgentCallbackContext, message: str) -> None:
    """Skip 后给模型/UI 的错误结果（照抄 read_file_validation 的写入模式）。"""
    from openjiuwen.core.foundation.llm import ToolMessage

    if not isinstance(ctx.inputs, ToolCallInputs):
        return
    tool_call = ctx.inputs.tool_call
    tool_call_id = getattr(tool_call, "id", "") if tool_call else ""
    text = str(message or "")
    ctx.inputs.tool_result = text
    tool_msg = getattr(ctx.inputs, "tool_msg", None)
    if tool_msg is not None:
        tool_msg.content = text
        if tool_call_id and getattr(tool_msg, "tool_call_id", None) in (None, ""):
            tool_msg.tool_call_id = tool_call_id
    else:
        ctx.inputs.tool_msg = ToolMessage(content=text, tool_call_id=tool_call_id)


def _should_strip_powershell_amp_prefix(
    tool_name: str, command: Any, shell_type: Any
) -> bool:
    """True when bash command uses PowerShell '&' prefix under a non-PowerShell shell."""
    if tool_name != "bash":
        return False
    if not isinstance(command, str) or not command.lstrip().startswith("& "):
        return False
    return shell_type in _DEFAULT_SHELL_TYPES


def coalesce_skill_envs(
    incoming: dict[str, dict[str, str]] | None,
    current: dict[str, dict[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Prefer incoming skill envs; keep current when incoming is empty."""
    incoming_map = incoming if isinstance(incoming, dict) else {}
    current_map = current if isinstance(current, dict) else {}
    if incoming_map:
        return incoming_map
    return current_map or incoming_map


def coalesce_config_skill_envs(config: Any, previous: Any) -> Any:
    """Keep previous react.skill_envs when config only has the YAML placeholder."""
    if not isinstance(config, dict):
        return config
    react = config.get("react")
    incoming = react.get("skill_envs") if isinstance(react, dict) else None
    previous_envs = None
    if isinstance(previous, dict):
        prev_react = previous.get("react")
        if isinstance(prev_react, dict):
            previous_envs = prev_react.get("skill_envs")
    resolved = coalesce_skill_envs(
        incoming if isinstance(incoming, dict) else None,
        previous_envs if isinstance(previous_envs, dict) else None,
    )
    if resolved == incoming or (not resolved and not incoming):
        return config
    merged = dict(config)
    merged_react = dict(react) if isinstance(react, dict) else {}
    merged_react["skill_envs"] = resolved
    merged["react"] = merged_react
    return merged


class SkillCredentialInjectionRail(DeepAgentRail):
    """Inject per-skill credentials into shell tool calls.

    三分支：
    1. 命令可解析为「正在跑当前激活技能的脚本」→ 注入该技能凭据；
    2. 命令在跑**另一个**有凭据技能的脚本 → 闸门拒绝并指引激活（逃生口：
       tool_args["env"] 显式携带该技能凭据 key 时放行，且不再注入 active
       技能的凭据，避免跨技能泄漏）；
    3. 命令静态不可解析（cd+相对路径 / 裸文件名 / 无凭据技能 / 非脚本
       形态）→ 回退按 active 注入，与既有行为一致（零回归）。
    """

    priority = 5

    def __init__(
        self,
        skill_envs: dict[str, dict[str, str]] | None = None,
        preset_session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._skill_envs: dict[str, dict[str, str]] = skill_envs or {}
        self._preset_session_id = _nonempty_str(preset_session_id)

    def update_skill_envs(self, new_skill_envs: dict[str, dict[str, str]]) -> None:
        self._skill_envs = new_skill_envs or {}
        logger.info(
            "[SkillCredentialInjectionRail] skill_envs updated: skills=[%s]",
            ", ".join(self._skill_envs.keys()),
        )

    def get_skill_envs(self) -> dict[str, dict[str, str]]:
        return self._skill_envs

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return

        tool_name: str = ctx.inputs.tool_name
        if tool_name not in SHELL_PERMISSION_TOOLS:
            return

        session_id = self._resolve_session_id(ctx)
        # Read-only lookup: do not adopt default orphans here (cross-session
        # credential leak). Migration runs in SkillActiveStateRail.before_invoke.
        active_skill = get_session_active_skill(session_id)

        tool_args = self._coerce_tool_args_dict(ctx.inputs)
        command = (
            str(tool_args.get("command") or "") if isinstance(tool_args, dict) else ""
        )
        matched_skills = match_skills_in_command(command, self._skill_envs.keys())
        other_skills = [s for s in matched_skills if s != active_skill]

        if other_skills:
            gate_skill = other_skills[0]
            if self._has_explicit_skill_env(tool_args, gate_skill):
                # 逃生口：模型显式传了该技能的凭据 key，按原样执行；
                # 不再注入 active 技能凭据（防跨技能泄漏）。
                logger.info(
                    "[SkillCredentialInjectionRail] explicit env for skill=%s, "
                    "skip active-skill injection (active=%s) session=%s",
                    gate_skill,
                    active_skill,
                    session_id,
                )
                return
            logger.info(
                "[SkillCredentialInjectionRail] gate: blocked direct run of "
                "skill=%s (active=%s) session=%s tool=%s",
                gate_skill,
                active_skill,
                session_id,
                tool_name,
            )
            self._reject_with_guidance(ctx, gate_skill)
            return

        # matched 与 active 一致，或命令不可解析（cd+相对路径/裸文件名/
        # 无凭据技能/非脚本形态）：按 active 注入，与既有行为一致。
        if not active_skill:
            return

        credentials = self._skill_envs.get(active_skill, {})
        if not credentials:
            return

        # 注入凭据到 tool_args["env"]，BashTool 会转发给子进程。
        # 同时处理 PowerShell '&' 语法（去前缀保持 auto shell）。
        self._inject_credentials(ctx.inputs, credentials, tool_name)
        logger.debug(
            "[SkillCredentialInjectionRail] injected env keys=%s for skill=%s tool=%s",
            list(credentials.keys()),
            active_skill,
            tool_name,
        )

    def _coerce_tool_args_dict(
        self, inputs: ToolCallInputs
    ) -> dict[str, Any] | None:
        """JSON 字符串 tool_args 原地解析为 dict（与 _inject_credentials 共享）。"""
        tool_args = inputs.tool_args
        if isinstance(tool_args, dict):
            return tool_args
        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, dict):
                inputs.tool_args = parsed
                return parsed
        return None

    def _has_explicit_skill_env(self, tool_args: Any, skill: str) -> bool:
        """逃生口判定：tool_args["env"] 显式携带了该技能的任一凭据 key。"""
        if not isinstance(tool_args, dict):
            return False
        env = tool_args.get("env")
        if not isinstance(env, dict):
            return False
        credential_keys = set(self._skill_envs.get(skill, {}))
        return bool(credential_keys & set(env))

    def _reject_with_guidance(self, ctx: AgentCallbackContext, skill: str) -> None:
        """拒绝执行（_skip_tool 短路）并写入激活指引，模型可据此自愈。"""
        language = resolve_language_from_context(ctx)
        message = build_skill_gate_message(skill, language)
        ctx.extra["_skip_tool"] = True
        _write_gate_tool_result(ctx, message)

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return resolve_skill_session_id(ctx, self._preset_session_id)

    def _inject_credentials(
        self, inputs: ToolCallInputs, credentials: dict[str, str], tool_name: str
    ) -> None:
        tool_args: Any = inputs.tool_args
        if tool_args is None:
            return

        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(parsed, dict):
                return
            tool_args = parsed
            inputs.tool_args = tool_args

        if isinstance(tool_args, dict):
            command = tool_args.get("command", "")

            # 当 bash 工具的命令以 PowerShell 调用操作符 '&' 开头时，
            # 去掉 '&' 前缀让命令在默认 shell(auto=Git Bash/cmd)下直接执行，
            # 避免 cmd 报 "& was unexpected" 及 Git Bash 的后台执行语义。
            # 若 LLM 显式指定了 powershell 则保留原样（尊重其意图）。
            shell_type = tool_args.get("shell_type", "auto")
            if _should_strip_powershell_amp_prefix(tool_name, command, shell_type):
                tool_args["command"] = command.lstrip()[2:].lstrip()

            env = tool_args.get("env")
            if not isinstance(env, dict):
                env = {}
            for key, value in credentials.items():
                if key not in env and value not in (None, ""):
                    env[key] = value
            tool_args["env"] = env


__all__ = [
    "GATE_MESSAGE_CN",
    "GATE_MESSAGE_EN",
    "SHELL_PERMISSION_TOOLS",
    "SKILL_GATE_PREFIX",
    "SkillCredentialInjectionRail",
    "build_skill_gate_message",
    "coalesce_config_skill_envs",
    "coalesce_skill_envs",
    "match_skill_in_command",
    "match_skills_in_command",
]
