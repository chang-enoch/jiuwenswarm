# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from unittest.mock import MagicMock

from jiuwenswarm.agents.harness.common.tools.bash_tool_safety import (
    _bash_schema_exposes_run_in_background,
    _ensure_bash_background_card,
    _ensure_powershell_background_card,
    _patch_tool_class,
    _shell_mismatch,
    _wrap_invoke,
    _pre_execute_shell_command,
    install_shell_tool_safety_hooks,
    reset_installed_flag,
)


@pytest.fixture(autouse=True)
def _reset_install_flag():
    reset_installed_flag()
    yield
    reset_installed_flag()


def test_pre_execute_blocks_pkill_on_jiuwenswarm_tui() -> None:
    err = _pre_execute_shell_command('pkill -f "jiuwenswarm-tui" 2>/dev/null')
    assert err is not None
    assert "rejected for safety" in err


def test_pre_execute_allows_unrelated_ps() -> None:
    err = _pre_execute_shell_command("ps aux | grep node | head -5")
    assert err is None


def test_bash_rejects_powershell_cmdlet() -> None:
    err = _shell_mismatch("bash", 'Move-Item "a" "b"')
    assert err is not None
    assert "powershell" in err


def test_bash_rejects_chained_and_piped_cmdlets() -> None:
    assert _shell_mismatch("bash", 'cd "C:/x" && Move-Item -Path a -Destination b') is not None
    assert _shell_mismatch("bash", "Get-ChildItem | Move-Item a b") is not None
    assert _shell_mismatch("bash", "ls; Remove-Item b") is not None


def test_bash_rejects_powershell_specific_variables_and_herestrings() -> None:
    assert _shell_mismatch("bash", "Get-Content $env:APPDATA\\x") is not None
    assert _shell_mismatch("bash", "echo $null") is not None
    assert _shell_mismatch("bash", "@'\nraw text\n'@ | Set-Content x") is not None


def test_bash_allows_generic_bash_variables() -> None:
    # A bare $var is legal bash; only $env:/$null/$true/$false are PowerShell.
    for command in (
        "echo $HOME",
        "for f in *.txt; do echo $f; done",
        "export VAR=1 && echo $VAR",
        "grep $pattern file.txt",
        "echo $nothing",
        "ls $dir",
    ):
        assert _shell_mismatch("bash", command) is None, command


def test_bash_allows_cmdlet_name_substrings_in_filenames() -> None:
    # Cmdlet detection matches the first word of each segment, never substrings.
    for command in (
        "cat out-file.txt",
        "bash move-item.sh",
        "grep start-process run.log",
    ):
        assert _shell_mismatch("bash", command) is None, command


def test_bash_allows_posix_loops_and_plain_commands() -> None:
    for command in (
        "cd /d/xiaoyi_work && ls -la",
        "mkdir -p build && touch build/a.txt",
        "ls; echo done",
        "git log --format=%H",
    ):
        assert _shell_mismatch("bash", command) is None, command


@pytest.mark.asyncio
async def test_bash_wrapper_forces_bash_shell_type() -> None:
    calls = []

    class Parsed:
        command = "echo hello"

    class Tool:
        def _parse_inputs(self, inputs):
            return Parsed()

    async def original(self, inputs, **kwargs):
        calls.append(dict(inputs))
        return "ok"

    wrapped = _wrap_invoke(original, "bash")
    result = await wrapped(Tool(), {"command": "echo hello"})

    assert result == "ok"
    assert calls == [{"command": "echo hello", "shell_type": "bash"}]


@pytest.mark.asyncio
async def test_bash_wrapper_passes_run_in_background_through() -> None:
    calls = []

    class Parsed:
        command = "python -m http.server 8000"

    class Tool:
        def _parse_inputs(self, inputs):
            return Parsed()

    async def original(self, inputs, **kwargs):
        calls.append(dict(inputs))
        return "ok"

    wrapped = _wrap_invoke(original, "bash")
    result = await wrapped(
        Tool(),
        {
            "command": Parsed.command,
            "run_in_background": True,
            "timeout": 1800,
        },
    )

    assert result == "ok"
    assert calls[0]["shell_type"] == "bash"
    assert calls[0]["run_in_background"] is True
    assert calls[0]["timeout"] == 1800
    assert "background" not in calls[0]


def test_install_patches_bash_card_with_run_in_background() -> None:
    from openjiuwen.harness.tools.shell.bash._tool import BashTool

    install_shell_tool_safety_hooks()
    tool = BashTool(MagicMock(), language="cn")
    properties = tool.card.input_params["properties"]
    assert "run_in_background" in properties
    assert properties["run_in_background"]["type"] == "boolean"
    assert "run_in_background" in tool.card.description
    assert "nohup" in tool.card.description
    assert "仅用于不会自行退出" in tool.card.description
    assert "会结束的长任务用 `timeout`" in tool.card.description
    assert "起前" in tool.card.description
    assert "子进程" in tool.card.description
    assert "失败换端口" in tool.card.description
    assert "不要杀占用进程" in tool.card.description


def test_ensure_bash_background_card_appends_en() -> None:
    class Card:
        input_params = {"properties": {}}
        description = "bash tool"

    class Tool:
        card = Card()

    _ensure_bash_background_card(Tool(), "en")
    assert "run_in_background" in Tool.card.input_params["properties"]
    assert "run_in_background" in Tool.card.description
    assert "will not exit on its own" in Tool.card.description
    assert "finite long jobs" in Tool.card.description
    assert "Before start" in Tool.card.description
    assert "child of this PID" in Tool.card.description
    assert "switch ports" in Tool.card.description
    assert "do not kill the occupying process" in Tool.card.description


def test_install_patches_powershell_card_against_start_process() -> None:
    from openjiuwen.harness.tools.shell.powershell._tool import PowerShellTool

    install_shell_tool_safety_hooks()
    tool = PowerShellTool(MagicMock(), language="cn")
    assert "background" in tool.card.input_params["properties"]
    assert "Start-Process" in tool.card.description
    assert "background=true" in tool.card.description
    assert "优先" in tool.card.description
    assert "仅用于不会自行退出" in tool.card.description
    assert "会结束的长任务用 `timeout`" in tool.card.description
    assert "-Wait" in tool.card.description
    assert "RedirectStandardOutput" in tool.card.description
    assert "起前" in tool.card.description
    assert "子进程" in tool.card.description
    assert "失败换端口" in tool.card.description
    assert "不要杀占用进程" in tool.card.description
    original = tool.card.description
    _ensure_powershell_background_card(tool, "cn")
    assert tool.card.description == original


def test_ensure_powershell_background_card_skips_existing_start_process() -> None:
    class Card:
        description = "already documents Start-Process as a URL opener"

    class Tool:
        card = Card()

    _ensure_powershell_background_card(Tool(), "cn")
    assert Tool.card.description == "already documents Start-Process as a URL opener"


def test_ensure_powershell_background_card_appends_en() -> None:
    class Card:
        description = "powershell tool"

    class Tool:
        card = Card()

    _ensure_powershell_background_card(Tool(), "en")
    assert "Start-Process" in Tool.card.description
    assert "background=true" in Tool.card.description
    assert "Prefer" in Tool.card.description
    assert "will not exit" in Tool.card.description
    assert "finite long jobs" in Tool.card.description
    assert "-Wait" in Tool.card.description
    assert "RedirectStandardOutput" in Tool.card.description
    assert "finite foreground" in Tool.card.description
    assert "before start" in Tool.card.description
    assert "child of this PID" in Tool.card.description
    assert "switch ports" in Tool.card.description
    assert "do not kill the occupying process" in Tool.card.description


def test_ensure_bash_background_card_skips_existing_property() -> None:
    existing = {
        "type": "boolean",
        "description": "upstream already exposes this",
    }

    class Card:
        input_params = {"properties": {"run_in_background": existing}}
        description = "bash tool with run_in_background already documented"

    class Tool:
        card = Card()

    _ensure_bash_background_card(Tool(), "cn")
    assert Tool.card.input_params["properties"]["run_in_background"] is existing
    assert Tool.card.description == "bash tool with run_in_background already documented"


def test_ensure_bash_background_card_skips_description_when_property_exists() -> None:
    existing = {
        "type": "boolean",
        "description": "upstream already exposes this",
    }

    class Card:
        input_params = {"properties": {"run_in_background": existing}}
        description = "bash tool without the parameter name in text"

    class Tool:
        card = Card()

    original = Tool.card.description
    _ensure_bash_background_card(Tool(), "cn")
    assert Tool.card.input_params["properties"]["run_in_background"] is existing
    assert Tool.card.description == original


def test_bash_schema_exposes_run_in_background_when_present(monkeypatch) -> None:
    monkeypatch.setattr(
        "openjiuwen.harness.prompts.tools.get_tool_input_params",
        lambda name, language="cn": {
            "properties": {"run_in_background": {"type": "boolean"}},
        },
    )
    assert _bash_schema_exposes_run_in_background() is True


def test_bash_schema_exposes_run_in_background_when_absent(monkeypatch) -> None:
    monkeypatch.setattr(
        "openjiuwen.harness.prompts.tools.get_tool_input_params",
        lambda name, language="cn": {
            "properties": {"command": {"type": "string"}},
        },
    )
    assert _bash_schema_exposes_run_in_background() is False


def test_patch_skips_init_wrap_when_schema_already_exposes(monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.bash_tool_safety._bash_schema_exposes_run_in_background",
        lambda: True,
    )

    class Dummy:
        def __init__(self) -> None:
            pass

        async def invoke(self, inputs):
            return None

        async def stream(self, inputs):
            if False:
                yield None

    original_init = Dummy.__init__
    _patch_tool_class(Dummy, "bash")
    assert Dummy.__init__ is original_init
    assert getattr(Dummy.invoke, "jiuwenswarm_safety_wrapped", False)


def test_patch_wraps_init_when_schema_missing_field(monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.bash_tool_safety._bash_schema_exposes_run_in_background",
        lambda: False,
    )

    class Dummy:
        def __init__(self) -> None:
            pass

        async def invoke(self, inputs):
            return None

        async def stream(self, inputs):
            if False:
                yield None

    _patch_tool_class(Dummy, "bash")
    assert getattr(Dummy.__init__, "jiuwenswarm_card_wrapped", False)


def test_patch_always_wraps_powershell_init() -> None:
    class Dummy:
        def __init__(self) -> None:
            pass

        async def invoke(self, inputs):
            return None

        async def stream(self, inputs):
            if False:
                yield None

    _patch_tool_class(Dummy, "powershell")
    assert getattr(Dummy.__init__, "jiuwenswarm_card_wrapped", False)


def test_install_routes_generic_semicolon_command_to_bash(monkeypatch) -> None:
    import openjiuwen.core.sys_operation.local.shell_operation as shell_module
    from openjiuwen.core.sys_operation.shell import ShellType

    monkeypatch.setattr(shell_module, "_available_bash", lambda **_kwargs: r"C:\Program Files\Git\bin\bash.exe")
    command = (
        'python --version; python -c "import reportlab; print(\'reportlab ok\')" 2>&1; '
        'python -c "import fpdf; print(\'fpdf ok\')" 2>&1'
    )

    install_shell_tool_safety_hooks()
    plan, use_shell, resolved_shell = shell_module.ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert plan == [r"C:\Program Files\Git\bin\bash.exe", "-lc", command]
    assert use_shell is False
    assert resolved_shell == "bash"


def test_install_routes_semicolon_command_to_powershell_without_bash(monkeypatch) -> None:
    import openjiuwen.core.sys_operation.local.shell_operation as shell_module
    from openjiuwen.core.sys_operation.shell import ShellType

    monkeypatch.setattr(shell_module, "_available_bash", lambda **_kwargs: None)
    monkeypatch.setattr(shell_module, "_available_powershell", lambda: r"C:\Windows\powershell.exe")
    command = "python --version; python -c \"print('ok')\""

    install_shell_tool_safety_hooks()
    plan, use_shell, resolved_shell = shell_module.ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert plan == [r"C:\Windows\powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    assert use_shell is False
    assert resolved_shell == "powershell"


def test_install_keeps_quoted_semicolon_on_cmd(monkeypatch) -> None:
    import openjiuwen.core.sys_operation.local.shell_operation as shell_module
    from openjiuwen.core.sys_operation.shell import ShellType

    monkeypatch.setattr(shell_module, "_available_bash", lambda **_kwargs: r"C:\Program Files\Git\bin\bash.exe")
    command = 'python -c "import reportlab; print(\'reportlab ok\')"'

    install_shell_tool_safety_hooks()
    plan, use_shell, resolved_shell = shell_module.ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert plan == command
    assert use_shell is True
    assert resolved_shell == "cmd"


def test_install_keeps_cmd_supported_and_separator_on_cmd(monkeypatch) -> None:
    import openjiuwen.core.sys_operation.local.shell_operation as shell_module
    from openjiuwen.core.sys_operation.shell import ShellType

    monkeypatch.setattr(shell_module, "_available_bash", lambda **_kwargs: r"C:\Program Files\Git\bin\bash.exe")
    command = "python --version && python -c \"print('ok')\""

    install_shell_tool_safety_hooks()
    plan, use_shell, resolved_shell = shell_module.ShellOperation._resolve_execution_plan(command, ShellType.AUTO)

    assert plan == command
    assert use_shell is True
    assert resolved_shell == "cmd"


def test_install_wraps_bash_tool_invoke() -> None:
    from openjiuwen.harness.tools.shell.bash._tool import BashTool

    install_shell_tool_safety_hooks()
    assert getattr(BashTool.invoke, "jiuwenswarm_safety_wrapped", False)
    assert getattr(BashTool.__init__, "jiuwenswarm_card_wrapped", False)
    install_shell_tool_safety_hooks()
    assert getattr(BashTool.invoke, "jiuwenswarm_safety_wrapped", False)
    assert getattr(BashTool.__init__, "jiuwenswarm_card_wrapped", False)
