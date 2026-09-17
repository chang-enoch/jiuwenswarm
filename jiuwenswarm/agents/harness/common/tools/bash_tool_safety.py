# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Apply jiuwenswarm shell safety rules to openjiuwen BashTool / PowerShellTool.

The agent's primary shell tool is ``bash`` (openjiuwen ``BashTool``), not
``mcp_exec_command``.  Safety checks in ``command_tools`` only affect the latter
unless we hook the harness tools here.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

_installed = False

_RUN_IN_BACKGROUND_PARAM = {
    "cn": "是否后台运行，默认 false；设为 true 时立即返回 PID",
    "en": "Run in background (default false); returns PID immediately when true",
}
_RUN_IN_BACKGROUND_USAGE = {
    "cn": (
        "\n - 可将 `run_in_background` 设为 true 后台运行命令；"
        "仅用于不会自行退出的命令；会结束的长任务用 `timeout`，不要后台。"
        "不要用 `nohup` 或 `&` 代替该参数。"
        "起前先查端口是否已有监听；起后核对监听是这次子进程且内容是这次目录，"
        "不要只看 HTTP 200；失败换端口，不要杀占用进程"
    ),
    "en": (
        "\n - You can set `run_in_background` to true to run the command "
        "in the background, only when the command will not exit on its own; "
        "use `timeout` for finite long jobs, not background. "
        "Do not substitute `nohup` or `&`. "
        "Before start, check whether the port already has a listener; "
        "after start, confirm the listener is a child of this PID and the "
        "content is this directory — do not rely on HTTP 200 alone; "
        "on failure switch ports and do not kill the occupying process"
    ),
}
_POWERSHELL_BACKGROUND_USAGE = {
    "cn": (
        "\n - 后台任务优先设 `background=true`（立刻回 PID）；"
        "仅用于不会自行退出的命令；会结束的长任务用 `timeout`，不要后台。"
        "也可用 `Start-Process` 把进程拆出去，但不要 `-Wait`、"
        "不要 `RedirectStandardOutput`/`Error`；探测请另开，"
        "起前先查端口是否已有监听；起后核对监听是这次子进程且内容是这次目录，"
        "不要只看 HTTP 200；失败换端口，不要杀占用进程。"
        "打开 URL 用前台 `Start-Process` 即可"
    ),
    "en": (
        "\n - Prefer `background=true` for background jobs "
        "(returns PID immediately), only when the command will not exit "
        "on its own; use `timeout` for finite long jobs, not background. "
        "You may detach with `Start-Process`, "
        "but do not use `-Wait` or `RedirectStandardOutput`/`Error`; "
        "probe in a separate call; before start check whether the port "
        "already has a listener; after start confirm the listener is a "
        "child of this PID and the content is this directory — do not "
        "rely on HTTP 200 alone; on failure switch ports and do not kill "
        "the occupying process. "
        "Opening a URL with `Start-Process` is a finite foreground job"
    ),
}


def _pre_execute_shell_command(command: str) -> str | None:
    """Return an error string when *command* must not run; else None."""
    from openjiuwen.core.sys_operation.shell_process_registry import (
        resolve_shell_session_id,
    )

    from jiuwenswarm.agents.harness.common.tools.command_tools import (
        _check_command_safety,
        _check_worktree_path_safety,
        _enforce_tui_spawn_budget,
    )

    blocked = _check_command_safety(command)
    if blocked:
        return f"[ERROR]: command rejected for safety ({blocked})."
    worktree_block = _check_worktree_path_safety(command)
    if worktree_block:
        return f"[ERROR]: {worktree_block}"
    spawn_block = _enforce_tui_spawn_budget(command, resolve_shell_session_id() or "")
    if spawn_block:
        return f"[ERROR]: {spawn_block}"
    return None


def _shell_mismatch(tool_name: str, command: str) -> str | None:
    """Reject commands whose syntax belongs to a different shell tool."""
    if tool_name == "bash":
        from jiuwenswarm.agents.harness.common.tools.command_tools import _is_powershell_command

        if _is_powershell_command(command):
            return (
                "PowerShell syntax was sent to the bash tool; retry with the "
                "powershell tool (or mcp_exec_command with shell_type=\"powershell\")."
            )
    return None


def _bash_schema_exposes_run_in_background() -> bool:
    """True when agent-core's bash tool card schema already has the field."""
    try:
        from openjiuwen.harness.prompts.tools import get_tool_input_params

        params = get_tool_input_params("bash")
    except (KeyError, TypeError, AttributeError):
        return False
    properties = params.get("properties") if isinstance(params, dict) else None
    return isinstance(properties, dict) and "run_in_background" in properties


def _ensure_bash_background_card(tool: Any, language: str) -> None:
    """Expose ``run_in_background`` on a BashTool instance card if missing."""
    card = getattr(tool, "card", None)
    if card is None:
        return
    lang = language if language in _RUN_IN_BACKGROUND_PARAM else "cn"
    params = getattr(card, "input_params", None)
    if isinstance(params, dict):
        properties = params.setdefault("properties", {})
        if isinstance(properties, dict) and "run_in_background" in properties:
            return
        if isinstance(properties, dict):
            properties["run_in_background"] = {
                "type": "boolean",
                "description": _RUN_IN_BACKGROUND_PARAM[lang],
            }
    description = getattr(card, "description", None)
    if isinstance(description, str) and "run_in_background" not in description:
        card.description = description.rstrip() + _RUN_IN_BACKGROUND_USAGE[lang]


def _ensure_powershell_background_card(tool: Any, language: str) -> None:
    """Append Start-Process usage notes on a PowerShellTool instance card if missing."""
    card = getattr(tool, "card", None)
    if card is None:
        return
    description = getattr(card, "description", None)
    if not isinstance(description, str) or "Start-Process" in description:
        return
    lang = language if language in _POWERSHELL_BACKGROUND_USAGE else "cn"
    card.description = description.rstrip() + _POWERSHELL_BACKGROUND_USAGE[lang]


def _wrap_card_init(
    original: Callable[..., None],
    ensure_fn: Callable[[Any, str], None],
) -> Callable[..., None]:
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        language = "cn"
        try:
            bound = inspect.signature(original).bind(self, *args, **kwargs)
            bound.apply_defaults()
            raw_language = bound.arguments.get("language", "cn")
            if isinstance(raw_language, str):
                language = raw_language
        except TypeError:
            raw_language = kwargs.get("language")
            if isinstance(raw_language, str):
                language = raw_language
        ensure_fn(self, language)

    init.jiuwenswarm_card_wrapped = True  # type: ignore[attr-defined]
    return init


def _wrap_invoke(
    original: Callable[..., Awaitable[Any]],
    tool_name: str,
) -> Callable[..., Awaitable[Any]]:
    from openjiuwen.harness.tools.base_tool import ToolOutput

    async def invoke(self: Any, inputs: dict[str, Any], **kwargs: Any) -> Any:
        parsed = getattr(self, "_parse_inputs")(inputs)
        if parsed.command:
            mismatch = _shell_mismatch(tool_name, parsed.command)
            if mismatch:
                return ToolOutput(success=False, error=mismatch)
            err = _pre_execute_shell_command(parsed.command)
            if err:
                return ToolOutput(success=False, error=err)
        routed_inputs = dict(inputs)
        routed_inputs["shell_type"] = tool_name
        return await original(self, routed_inputs, **kwargs)

    invoke.jiuwenswarm_safety_wrapped = True
    return invoke


def _wrap_stream(
    original: Callable[..., Any],
    tool_name: str,
) -> Callable[..., Any]:
    from openjiuwen.harness.tools.base_tool import ToolOutput

    async def stream(self: Any, inputs: dict[str, Any], **kwargs: Any):
        parsed = getattr(self, "_parse_inputs")(inputs)
        if parsed.command:
            mismatch = _shell_mismatch(tool_name, parsed.command)
            if mismatch:
                yield ToolOutput(success=False, error=mismatch)
                return
            err = _pre_execute_shell_command(parsed.command)
            if err:
                yield ToolOutput(success=False, error=err)
                return
        routed_inputs = dict(inputs)
        routed_inputs["shell_type"] = tool_name
        async for item in original(self, routed_inputs, **kwargs):
            yield item

    stream.jiuwenswarm_safety_wrapped = True
    return stream


def _patch_tool_class(tool_cls: type, tool_name: str) -> None:
    if not getattr(tool_cls.invoke, "jiuwenswarm_safety_wrapped", False):
        tool_cls.invoke = _wrap_invoke(tool_cls.invoke, tool_name)
    if not getattr(tool_cls.stream, "jiuwenswarm_safety_wrapped", False):
        tool_cls.stream = _wrap_stream(tool_cls.stream, tool_name)
    already_card_wrapped = getattr(tool_cls.__init__, "jiuwenswarm_card_wrapped", False)
    if (
        tool_name == "bash"
        and not already_card_wrapped
        and not _bash_schema_exposes_run_in_background()
    ):
        tool_cls.__init__ = _wrap_card_init(tool_cls.__init__, _ensure_bash_background_card)
    elif tool_name == "powershell" and not already_card_wrapped:
        tool_cls.__init__ = _wrap_card_init(
            tool_cls.__init__, _ensure_powershell_background_card
        )


def _contains_unquoted_semicolon(command: str) -> bool:
    quote: str | None = None
    for char in command:
        if char in {'"', "'"}:
            quote = None if quote == char else char if quote is None else quote
        elif char == ";" and quote is None:
            return True
    return False


def _patch_shell_execution_plan() -> None:
    import openjiuwen.core.sys_operation.local.shell_operation as shell_module

    operation_cls = shell_module.ShellOperation
    original = operation_cls._resolve_execution_plan
    if getattr(original, "jiuwenswarm_semicolon_routing_wrapped", False):
        return

    def resolve_execution_plan(command: str, shell_type: Any) -> tuple[list[str] | str, bool, str]:
        plan, use_shell, resolved_shell = original(command, shell_type)
        if resolved_shell != "cmd" or getattr(shell_type, "value", shell_type) != "auto":
            return plan, use_shell, resolved_shell

        if not _contains_unquoted_semicolon(command):
            return plan, use_shell, resolved_shell

        exe = shell_module._available_bash(allow_wsl=False)
        if exe:
            normalized = shell_module._normalize_windows_paths_for_bash(command)
            return [exe, "-lc", normalized], False, "bash"

        ps_exe = shell_module._available_powershell()
        return [ps_exe, "-NoProfile", "-NonInteractive", "-Command", command], False, "powershell"

    resolve_execution_plan.jiuwenswarm_semicolon_routing_wrapped = True
    operation_cls._resolve_execution_plan = staticmethod(resolve_execution_plan)


def install_shell_tool_safety_hooks() -> None:
    """Idempotently wire safety checks into harness shell tools."""
    global _installed
    if _installed:
        return

    from openjiuwen.harness.tools.shell.bash._tool import BashTool

    _patch_tool_class(BashTool, "bash")
    _patch_shell_execution_plan()

    try:
        from openjiuwen.harness.tools.shell.powershell._tool import PowerShellTool

        _patch_tool_class(PowerShellTool, "powershell")
    except ImportError:
        pass

    _installed = True


def reset_installed_flag() -> None:
    """Reset the installed flag so hooks can be re-applied (for testing)."""
    global _installed
    _installed = False


__all__ = [
    "_bash_schema_exposes_run_in_background",
    "_ensure_bash_background_card",
    "_ensure_powershell_background_card",
    "_patch_tool_class",
    "_pre_execute_shell_command",
    "_shell_mismatch",
    "_wrap_invoke",
    "install_shell_tool_safety_hooks",
    "reset_installed_flag",
]
