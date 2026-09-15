from types import SimpleNamespace

from jiuwenswarm.agents.harness.common.prompt.skills_goal_override import (
    _SKILLS_PROMPT_MAX_CHARS_ENV,
    _TOOL_USAGE_RULES,
    _build_all_mode_skill_prompt_from_skills,
    _build_auto_list_mode_skill_prompt,
)


def test_all_mode_uses_dynamic_available_skills_xml_without_static_catalogue():
    prompt = _build_all_mode_skill_prompt_from_skills(
        [
            SimpleNamespace(name="custom-pdf", description="Handles user PDFs."),
            SimpleNamespace(name="custom-image", description="Handles user images."),
        ]
    )

    assert "# Skills" in prompt
    assert "Skill Usage Principle" in prompt
    assert "<available_skills>" in prompt
    assert "<name>custom-pdf</name>" in prompt
    assert "<name>custom-image</name>" in prompt
    assert "xiaoyi-ppt-win" not in prompt


def test_all_mode_keeps_distinct_legacy_and_win_skill_names():
    prompt = _build_all_mode_skill_prompt_from_skills(
        [
            SimpleNamespace(name="xiaoyi-ppt", description="Legacy PPT skill."),
            SimpleNamespace(name="xiaoyi-ppt-win", description="Windows PPT skill."),
        ]
    )

    assert "<name>xiaoyi-ppt</name>" in prompt
    assert "<name>xiaoyi-ppt-win</name>" in prompt


def test_auto_list_keeps_only_the_stable_preamble():
    prompt = _build_auto_list_mode_skill_prompt()

    assert "# Skills" in prompt
    assert "Skill Usage Principle" in prompt
    assert "<available_skills>" not in prompt
    assert "xiaoyi-ppt-win" not in prompt


def test_tool_usage_rules_contains_find_skills_inline_and_no_subsection():
    en = _TOOL_USAGE_RULES["en"]
    assert "# Tool Usage Rules" in en
    assert "find-skills-win" in en
    assert "## Skill Discovery and Installation" not in en
    assert "## Task planning (todos)" in en
    assert "## Parallel tool calls" in en
    assert "## Bash usage rules" in en
    assert "### Git Safety Protocol" in en

    cn = _TOOL_USAGE_RULES["cn"]
    assert "# 工具使用规则" in cn
    assert "find-skills-win" in cn
    assert "## 技能发现与安装" not in cn
    assert "## 任务规划（todos）" in cn
    assert "## 并行工具调用" in cn
    assert "## Bash 使用规则" in cn
    assert "### Git 安全协议" in cn
    assert "python app.py & sleep 3 && curl" not in en
    assert "python app.py & sleep 3 && curl" not in cn
    cn_preamble = cn.split("## Bash 使用规则")[0]
    en_preamble = en.split("## Bash usage rules")[0]
    assert "background=true" in cn_preamble
    assert "background=true" in en_preamble
    assert "run_in_background=true" in cn_preamble
    assert "run_in_background=true" in en_preamble
    assert "必须指定" not in cn_preamble
    assert "must set" not in en_preamble
    assert "这次调用必须马上结束" in cn_preamble
    assert "must return immediately" in en_preamble
    assert "优先" in cn_preamble
    assert "Prefer to set" in en_preamble
    assert "结束点" in cn_preamble
    assert "不看跑多久" in cn_preamble
    assert "sleep N" in cn_preamble
    assert "不要设 `run_in_background`" in cn_preamble
    assert "control flow" in en_preamble
    assert "duration" in en_preamble
    assert "sleep N" in en_preamble
    assert "do not set `run_in_background`" in en_preamble
    assert "不会自行退出" in cn_preamble
    assert "will not exit" in en_preamble
    assert "等此类操作" in cn_preamble
    assert "similar operations" in en_preamble
    assert "后台只指" not in cn_preamble
    assert "means only" not in en_preamble
    assert "powershell 与 bash 均有效" not in cn_preamble
    assert "valid for both powershell and bash" not in en_preamble
    assert "bash 没有 `background` 参数" not in cn
    assert "bash tool has no `background` parameter" not in en
    assert "Start-Process" not in cn_preamble
    assert "[Process]::Start" not in cn_preamble
    assert "RedirectStandardOutput" not in cn_preamble
    assert "不能代替该参数" not in cn_preamble
    assert "确认服务是否起来必须另开一次调用" in cn_preamble
    assert "包装进程" in cn_preamble
    assert "起指定端口前" in cn_preamble
    assert "已有监听" in cn_preamble
    assert "子进程" in cn_preamble
    assert "本次目录" in cn_preamble
    assert "不要把身份默认成立" in cn_preamble
    assert "不要只看 HTTP 200" in cn_preamble
    assert "换空闲端口" in cn_preamble
    assert "不要杀占用" in cn_preamble
    assert "netstat" not in cn_preamble
    assert "Get-NetTCPConnection" not in cn_preamble
    assert "长驻" not in cn
    assert "Start-Process" not in en_preamble
    assert "[Process]::Start" not in en_preamble
    assert "RedirectStandardOutput" not in en_preamble
    assert "do not substitute for that parameter" not in en_preamble
    assert "Probe whether the server is up in a separate call" in en_preamble
    assert "wrapper process" in en_preamble
    assert "Before starting on a specified port" in en_preamble
    assert "already has a listener" in en_preamble
    assert "child of this returned PID" in en_preamble
    assert "this directory" in en_preamble
    assert "do not assume identity holds" in en_preamble
    assert "HTTP 200" in en_preamble
    assert "free port" in en_preamble
    assert "do not kill the occupying process" in en_preamble
    assert "netstat" not in en_preamble
    assert "Get-NetTCPConnection" not in en_preamble
    assert "long-lived" not in en
    assert "is not background" not in en
    assert "不算后台" not in cn


def test_budget_preserves_names_after_full_descriptions(monkeypatch):
    skills = [
        SimpleNamespace(name="first", description="first description " * 40),
        SimpleNamespace(name="second", description="second description " * 40),
    ]
    full_first = _build_all_mode_skill_prompt_from_skills([skills[0]])
    monkeypatch.setenv(_SKILLS_PROMPT_MAX_CHARS_ENV, str(len(full_first) + 40))

    prompt = _build_all_mode_skill_prompt_from_skills(skills)

    assert "<name>first</name>" in prompt
    assert "<name>second</name>" in prompt
    assert "<description>second description" not in prompt
