# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Batch E: content-fill density slim + custom content-template path."""

from __future__ import annotations

import pytest

from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen import (
    _CONTENT_FILL_DENSITY_CHECKLIST,
    _build_content_template_fill_prompt,
    _build_content_template_fill_system_prompt,
    _chart_activation_incomplete,
    _count_filled_chart_options,
    _count_null_chart_options,
    _extract_designer_section,
    _fix_chart_scaffold_activation,
    _layout_patch_regressed_chart_options,
    _layout_patch_still_unfilled_chart_options,
    _uses_content_template_fill,
    _validate_custom_content_template_fill_output,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.slide_designer_worker import (
    SlideDesignerWorker,
)

# _is_valid_html requires len >= 200
_MINIMAL_SEED_HTML = """<!DOCTYPE html>
<html><head><title>{{PAGE_TITLE}}</title>
<script>tailwind.config={theme:{extend:{colors:{brand:'#c00'}}}}</script>
<style>@layer utilities{.content-safe{width:1220px}}</style>
</head>
<body>
<div class="ppt-slide w-[1280px] h-[720px]">
  <div class="content-safe">
    <header class="flex-shrink-0 page-header"><h1 class="page-title">{{PAGE_TITLE}}</h1></header>
    <main class="flex-1 min-h-0 page-main">{{PAGE_CONTENT}}</main>
    <div class="flex-shrink-0 page-footer"><p class="page-footer-note">{{PAGE_FOOTER}}</p></div>
  </div>
</div>
</body></html>
"""

_OUTLINE_CONTENT = """### P3: 市场趋势
- **类型**: content
- **研究需求**: ✅ 需要调研
- **标题**: 市场趋势
- **内容概要**: 概要足够长用于校验
"""


_DESIGNER_31_SLICE = """
#### 3.1 选材与定型（选内容 + 定表达形式）

**C. 内容分块（强制）**

把素材切成 3–6 个内容块。MARKER_C_BLOCKS

**D. 表达形式选择（强制）**

| 素材特征 | 首选形式 | 可选 | 不得使用 |
| --- | --- | --- | --- |
| 时间序列、趋势、同比环比 | 折线图 / 柱状图（ECharts） | 带升降标记的指标卡 | 纯文字罗列数值 |
| 多对象 × 多维度对比 | 表格 / 对比矩阵 | 分组柱状图 | 并列大段文字 |

MARKER_D_FORM_TABLE

**D-2. 承载骨架配方（强制，选完形式紧接着定）**

| 形式 | 稳定骨架 |
| --- | --- |
| 表格 / 对比矩阵 | 默认 `<table class="w-full">` 自然行高 |
| 统计图表 | 见 charts.md 固定配方 |

选定即冻结：承载骨架属于本步的决定。MARKER_D2_SKELETON

**E. 写前版面意图（生成 HTML 前必须完成）**

写前确认 role/form。MARKER_E_INTENT

#### 3.2 版面落地（简化规则）

3.2 不应进入 content-fill 切片 MARKER_32_LEAK

##### 按页面类型的最低信息结构（下限）

| 页面类型 | 最低语义结构 | 证据要求 |
| --- | --- | --- |
| `technology` 架构页 | ≥3 个层级或模块 + 职责 + 关系 | 含总结或关键设计原则 |
| `case` 案例页 | 背景、问题、方案、结果四段 | 结果含至少 1 项可验证指标 |

MARKER_DENSITY_FLOOR

##### 信息表达转换规则

转换规则不应进入下限切片 MARKER_CONVERT_LEAK
""".strip()


def test_extract_designer_for_fill_includes_31_form_not_appendix():
    long_designer = (
        "## 用户显式要求优先\n必须遵守用户色板\n\n"
        f"{_DESIGNER_31_SLICE}\n\n"
        "## 关键原则\n禁止空卡片\n"
    )
    out = _extract_designer_section(
        long_designer,
        for_content_template_fill=True,
        appendix_text="## 弹性布局模式\n很多附录文字 MARKER_FLEX_LEAK\n",
    )
    assert _CONTENT_FILL_DENSITY_CHECKLIST in out
    assert "MARKER_D_FORM_TABLE" in out
    assert "MARKER_D2_SKELETON" in out
    assert "MARKER_C_BLOCKS" in out
    assert "选定即冻结" in out
    assert "MARKER_DENSITY_FLOOR" in out
    assert "按页面类型的最低信息结构" in out
    assert "MARKER_32_LEAK" not in out
    assert "MARKER_CONVERT_LEAK" not in out
    assert "MARKER_FLEX_LEAK" not in out
    assert "弹性布局模式" not in out


def test_extract_designer_for_fill_appends_charts_when_requested():
    charts = "## 图表与数据可视化\n优先激活 CHART_SCAFFOLD\n"
    out = _extract_designer_section(
        "",
        for_content_template_fill=True,
        include_charts=True,
        charts_text=charts,
    )
    assert _CONTENT_FILL_DENSITY_CHECKLIST in out
    assert "CHART_SCAFFOLD" in out


def test_content_fill_prompt_omits_homogeneous_layout_shell_and_uses_31():
    prompt = _build_content_template_fill_prompt(
        page_number=3,
        style_id="custom",
        style_text="style body",
        outline_page=_OUTLINE_CONTENT,
        research_page="趋势数据与对比矩阵",
        outline_full="full outline unused",
        seed_html=_MINIMAL_SEED_HTML,
        designer_md_text=_DESIGNER_31_SLICE,
    )
    assert "MARKER_D_FORM_TABLE" in prompt
    assert "MARKER_D2_SKELETON" in prompt
    assert "选定即冻结" in prompt
    assert "MARKER_DENSITY_FLOOR" in prompt
    assert "本页定型已冻结" not in prompt
    assert "primary_form" not in prompt
    assert "禁止跨页复用同一套" not in prompt
    assert "4-6 个关键数字卡片" not in prompt
    assert "6 个核心论点卡片" not in prompt
    assert "参考布局（data 类型" not in prompt
    assert "参考布局（trend 类型" not in prompt


def test_custom_content_fill_system_prompt_requires_31_form_not_anti_designer():
    text = _build_content_template_fill_system_prompt(
        style_id="custom",
        page_type="content",
        outline_page=_OUTLINE_CONTENT,
        research_page="research",
    )
    assert "不是设计师" not in text
    assert "§3.1" in text or "designer §3.1" in text
    assert "页型最低信息结构" in text
    assert "PAGE_CONTENT" in text
    assert "chrome" in text.lower() or "框架" in text
    assert "primary_form" not in text


def test_content_fill_prompt_has_no_turbo_form_freeze():
    outline = """### P5: 模型
- **类型**: technology
- **标题**: 分工模型
"""
    for style_id in ("custom", "business-classic"):
        prompt = _build_content_template_fill_prompt(
            page_number=5,
            style_id=style_id,
            style_text="style",
            outline_page=outline,
            research_page="research",
            outline_full="",
            seed_html=_MINIMAL_SEED_HTML,
            designer_md_text=_DESIGNER_31_SLICE,
        )
        assert "本页定型已冻结" not in prompt
        assert "layout_recipe_not_realized" not in prompt
        assert "MARKER_DENSITY_FLOOR" in prompt


def test_fix_chart_scaffold_activation_strips_html_comment_markers():
    html = (
        '<div id="chart-1"></div>\n'
        "<!-- CHART_SCAFFOLD_BEGIN\n"
        "<script>\n"
        "  /* CHART_SCAFFOLD_BEGIN stays in JS block comment */\n"
        '  const el = document.getElementById("chart-1");\n'
        "  const option = { series: [{ type: 'bar', data: [1, 2] }] };\n"
        "  echarts.init(el).setOption(option);\n"
        "</script>\n"
        "CHART_SCAFFOLD_END -->\n"
    )
    fixed = _fix_chart_scaffold_activation(html)
    assert "<!-- CHART_SCAFFOLD_BEGIN" not in fixed
    assert "CHART_SCAFFOLD_END -->" not in fixed
    assert "<script>" in fixed
    assert "CHART_SCAFFOLD_BEGIN stays in JS block comment" in fixed
    assert fixed == _fix_chart_scaffold_activation(fixed)


def test_fix_chart_scaffold_activation_noop_without_markers():
    html = "<div id='chart-1'></div><script>echarts.init(el)</script>"
    assert _fix_chart_scaffold_activation(html) is html


def test_count_filled_chart_options_ignores_comment_and_null():
    filled = (
        "<script>\n"
        "  /* 把下方 const option = null 替换为对象 */\n"
        "  const option = { series: [{ type: 'bar', data: [1] }] };\n"
        "</script>\n"
    )
    empty = (
        "<script>\n"
        "  /* 把下方 const option = null 替换为对象 */\n"
        "  const option = null;\n"
        "  if (!option) return;\n"
        "</script>\n"
    )
    assert _count_filled_chart_options(filled) == 1
    assert _count_filled_chart_options(empty) == 0
    assert _count_null_chart_options(empty) == 1
    assert _count_null_chart_options(filled) == 0


def test_chart_activation_incomplete_dormant_scaffold_with_chart_shell():
    """有 chart 壳 + 注释内 option=null：须判未激活（不能靠 null 计数）。"""
    html = (
        '<div id="chart-1"></div>\n'
        "<!-- CHART_SCAFFOLD_BEGIN\n"
        "<script>\n"
        "  const option = null;\n"
        "  if (!option) return;\n"
        "</script>\n"
        "CHART_SCAFFOLD_END -->\n"
    )
    assert _count_null_chart_options(html) == 0  # 注释内不可见
    assert _chart_activation_incomplete(html) is True


def test_chart_activation_incomplete_activated_ok():
    html = (
        '<div id="chart-1"></div>\n'
        "<script>\n"
        "  const option = { series: [{ type: 'bar', data: [1] }] };\n"
        "  echarts.init(el).setOption(option);\n"
        "</script>\n"
    )
    assert _chart_activation_incomplete(html) is False


def test_chart_activation_incomplete_no_chart_container_keeps_dormant():
    html = (
        "<div id='kpi-1'></div>\n"
        "<!-- CHART_SCAFFOLD_BEGIN\n"
        "<script>const option = null;</script>\n"
        "CHART_SCAFFOLD_END -->\n"
    )
    assert _chart_activation_incomplete(html) is False


def test_chart_activation_incomplete_peeled_but_null():
    html = (
        '<div id="chart-funnel"></div>\n'
        "<script>\n"
        "  const option = null;\n"
        "  if (!option) return;\n"
        "</script>\n"
    )
    assert _chart_activation_incomplete(html) is True


def test_rewrite_hint_chart_scaffold_not_activated():
    hint = SlideDesignerWorker._build_rewrite_hint("chart_scaffold_not_activated")
    assert "CHART_SCAFFOLD" in hint
    assert "const option" in hint


def test_system_prompt_mentions_multi_chart_scaffold():
    text = _build_content_template_fill_system_prompt(
        style_id="business-classic",
        page_type="data",
        outline_page="趋势对比数据",
        research_page="指标与基准测试",
    )
    assert "CHART_SCAFFOLD_*" in text


def test_layout_patch_regressed_chart_options_detects_null_rollback():
    before = (
        '<div id="chart-1"></div>\n'
        "<script>\n"
        "  const option = { series: [{ type: 'bar', data: [1, 2] }] };\n"
        "  echarts.init(el).setOption(option);\n"
        "</script>\n"
    )
    after = (
        '<div id="chart-1"></div>\n'
        "<script>\n"
        "  const option = null;\n"
        "  if (!option) return;\n"
        "  echarts.init(el).setOption(option);\n"
        "</script>\n"
    )
    assert _layout_patch_regressed_chart_options(before, after)
    assert not _layout_patch_regressed_chart_options(before, before)
    assert not _layout_patch_regressed_chart_options(after, after)


def test_layout_patch_still_unfilled_chart_options():
    empty = (
        "<script>\n"
        "  const option = null;\n"
        "  if (!option) return;\n"
        "</script>\n"
    )
    filled = (
        "<script>\n"
        "  const option = { series: [{ type: 'bar', data: [1] }] };\n"
        "</script>\n"
    )
    assert _layout_patch_still_unfilled_chart_options(empty, empty)
    assert not _layout_patch_still_unfilled_chart_options(empty, filled)
    assert not _layout_patch_still_unfilled_chart_options(filled, filled)


def test_content_fill_prompt_omits_full_outline():
    prompt = _build_content_template_fill_prompt(
        page_number=3,
        style_id="business-classic",
        style_text="style body",
        outline_page=_OUTLINE_CONTENT,
        research_page="research notes",
        outline_full="### P1: 封面\n### P2: 目录\n### P99: 不应出现的全文页",
        seed_html=_MINIMAL_SEED_HTML,
    )
    assert "大纲 — 本页规划" in prompt
    assert "市场趋势" in prompt
    assert "P99" not in prompt
    assert "不应出现的全文页" not in prompt
    assert "完整 outline" not in prompt.lower()


def test_uses_content_template_fill_includes_custom():
    assert _uses_content_template_fill("custom", "content", _OUTLINE_CONTENT)
    assert not _uses_content_template_fill("custom", "cover", _OUTLINE_CONTENT)


def test_validate_custom_rejects_single_root_block():
    filled = (
        _MINIMAL_SEED_HTML.replace("{{PAGE_TITLE}}", "真实标题足够长")
        .replace(
            "{{PAGE_CONTENT}}",
            '<div class="w-full flex-1 min-h-0"><p>唯一根容器包住全部正文内容</p></div>',
        )
        .replace("{{PAGE_FOOTER}}", "来源：测试报告")
    )
    ok, reason = _validate_custom_content_template_fill_output(_MINIMAL_SEED_HTML, filled)
    assert not ok
    assert reason == "custom_page_content_blocks"


def test_validate_custom_accepts_two_direct_children():
    filled = (
        _MINIMAL_SEED_HTML.replace("{{PAGE_TITLE}}", "真实标题足够长")
        .replace(
            "{{PAGE_CONTENT}}",
            (
                '<div class="flex-shrink-0"><p>结论条要点说明文字</p></div>'
                '<div class="flex-1 min-h-0"><p>主体分栏正文内容</p></div>'
            ),
        )
        .replace("{{PAGE_FOOTER}}", "来源：测试报告")
    )
    ok, reason = _validate_custom_content_template_fill_output(_MINIMAL_SEED_HTML, filled)
    assert ok, reason


def test_custom_fill_prompt_branch_rules():
    prompt = _build_content_template_fill_prompt(
        page_number=3,
        style_id="custom",
        style_text="custom style",
        outline_page=_OUTLINE_CONTENT,
        research_page="research",
        outline_full="FULL_OUTLINE_SHOULD_NOT_APPEAR",
        seed_html=_MINIMAL_SEED_HTML,
    )
    assert "自定义风格" in prompt
    assert "至少两个直接子块" in prompt
    assert "THEME_CSS_*" in prompt or "THEME" in prompt
    assert "FULL_OUTLINE_SHOULD_NOT_APPEAR" not in prompt


_DORMANT_CHART_HTML = (
    '<div class="ppt-slide"><div id="chart-1"></div>'
    "<!-- CHART_SCAFFOLD_BEGIN\n"
    "<script>const option = null; if (!option) return;</script>\n"
    "CHART_SCAFFOLD_END --></div>"
)


def _make_page_ctx(*, style_id: str, style_mode: str, pages_dir: str):
    from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen import (
        PageGenContext,
    )

    return PageGenContext(
        page_num=1,
        style_id=style_id,
        style_text="style",
        outline_page=_OUTLINE_CONTENT,
        research_page="research",
        outline_is_full=False,
        image_map_page="",
        designer_md_text="",
        style_mode=style_mode,
        pages_dir=pages_dir,
        pages_seeded=True,
        page_path=f"{pages_dir}/page-1.pptx.html",
        pptx_root=pages_dir,
        total_pages=1,
    )


@pytest.mark.asyncio
async def test_custom_chart_exhausted_tries_free_gen_then_soft_accept(tmp_path):
    """custom：填槽耗尽后先 free_gen；失败则 soft 交付 last_raw（不进 missing）。"""
    from unittest.mock import AsyncMock, MagicMock

    from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.template_fill import (
        PageGenPolicy,
    )

    pages_dir = str(tmp_path)
    host = MagicMock()
    host.read_file_for_worker = AsyncMock(return_value=_MINIMAL_SEED_HTML)
    host.write_file_for_worker = AsyncMock(return_value=True)
    worker = SlideDesignerWorker(
        host, PageGenPolicy(allow_free_gen_fallback=True, max_fill_attempts=1)
    )
    worker._fill_template_once = AsyncMock(
        return_value=("", _DORMANT_CHART_HTML, "chart_scaffold_not_activated")
    )
    worker._run_free_generate = AsyncMock(return_value=("", "free_generate_failed"))
    worker._layout_loop = AsyncMock(return_value=(True, "", False))

    result = await worker.run(
        _make_page_ctx(style_id="custom", style_mode="custom", pages_dir=pages_dir)
    )

    worker._run_free_generate.assert_awaited_once()
    assert result.ok is True
    assert result.layout_warning is True
    assert result.html == _DORMANT_CHART_HTML
    assert result.fail_reason == "chart_scaffold_not_activated"


@pytest.mark.asyncio
async def test_custom_chart_exhausted_delivers_free_gen_when_ok(tmp_path):
    """custom：free_gen 成功则交付 free_gen 页，不再 soft last_raw。"""
    from unittest.mock import AsyncMock, MagicMock

    from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.template_fill import (
        PageGenPolicy,
    )

    pages_dir = str(tmp_path)
    free_html = '<div class="ppt-slide"><div id="chart-1"></div></div>'
    host = MagicMock()
    host.read_file_for_worker = AsyncMock(return_value=_MINIMAL_SEED_HTML)
    host.write_file_for_worker = AsyncMock(return_value=True)
    worker = SlideDesignerWorker(
        host, PageGenPolicy(allow_free_gen_fallback=True, max_fill_attempts=1)
    )
    worker._fill_template_once = AsyncMock(
        return_value=("", _DORMANT_CHART_HTML, "chart_scaffold_not_activated")
    )
    worker._run_free_generate = AsyncMock(return_value=(free_html, ""))

    result = await worker.run(
        _make_page_ctx(style_id="custom", style_mode="custom", pages_dir=pages_dir)
    )

    worker._run_free_generate.assert_awaited_once()
    assert result.ok is True
    assert result.html == free_html
    assert result.layout_warning is False
    host.write_file_for_worker.assert_awaited()


@pytest.mark.asyncio
async def test_preset_chart_exhausted_soft_accept_skips_free_gen(tmp_path):
    """preset：无 free_gen，图表未激活直接 soft 交付。"""
    from unittest.mock import AsyncMock, MagicMock

    from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.template_fill import (
        PageGenPolicy,
    )

    pages_dir = str(tmp_path)
    host = MagicMock()
    host.read_file_for_worker = AsyncMock(return_value=_MINIMAL_SEED_HTML)
    host.write_file_for_worker = AsyncMock(return_value=True)
    worker = SlideDesignerWorker(
        host, PageGenPolicy(allow_free_gen_fallback=False, max_fill_attempts=1)
    )
    worker._fill_template_once = AsyncMock(
        return_value=("", _DORMANT_CHART_HTML, "chart_scaffold_not_activated")
    )
    worker._run_free_generate = AsyncMock(return_value=("", "should_not_run"))
    worker._layout_loop = AsyncMock(return_value=(True, "", False))

    result = await worker.run(
        _make_page_ctx(
            style_id="business-classic",
            style_mode="preset",
            pages_dir=pages_dir,
        )
    )

    worker._run_free_generate.assert_not_awaited()
    assert result.ok is True
    assert result.layout_warning is True
    assert result.html == _DORMANT_CHART_HTML
