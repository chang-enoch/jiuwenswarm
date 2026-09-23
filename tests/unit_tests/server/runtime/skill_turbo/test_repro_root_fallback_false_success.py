# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""编排根节点 fallback 虚假 success=true 必须被交付物门禁拒绝。

回放 BUG20260917365344（session officeclaw_a6bded7bcac8aece7c4f4065，
2026-09-17 15:17-15:18）根因：ppt_gen_root 的 fallback 子代理仅修复
p4_3_outline_gen 大纲即声明 ``success=true``，契约校验只认 JSON 自证便放行，
P5-P10（含 PPTX 导出与交付）被整体跳过，HITL resume 以成功收尾，用户收到
"PPT 任务已结束，但未能确认文件已生成并发送"。

期望：根节点声明的 ``validate_fallback_success``（PPTX 真实存在且
delivery_status 完成）在 fallback 契约通过后必须被强制执行，虚假成功以
FallbackContractError 终止性降级。本分支无 ppt 技能源码，编排根节点按
PPTGenRootNode 语义本地合成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.server.runtime.skill_turbo.executor import SkillTurboExecutor
from jiuwenswarm.server.runtime.skill_turbo.fallback_handler import (
    DeepAgentFallbackHandler,
    FallbackContractError,
)
from jiuwenswarm.server.runtime.skill_turbo.plan_node import PlanNode

# 根级兜底子代理仅完成 P4 阶段的输出形态（事故原文结构）：
# 校验正文 + 末尾单行 JSON 契约，result 仅含 P4 级字段，无 pptx_path / delivery_status。
_INCIDENT_OUTPUT = (
    "校验全部通过：✅ 无 chapter/section/agenda 结构页；✅ 研究需求=13 页（P2–P14），"
    "与 page_count=13 完全一致；✅ P1=cover、P15=ending；✅ 每页字段齐全。\n"
    "\n"
    "**本轮 fallback 完成情况总结**\n"
    "\n"
    "本次替代失败的 p4_content_plan / p4_3_outline_gen 链路，已修复根因并完成 P4 产物。\n"
    "以下字段可直接回写 inputs 供下游（P5 页面内容生成）消费：\n"
    "\n"
    '`{"success": true, "result": {"outline_path": '
    '"C:/ws/20260917151247/output/20260917_151258_000/outline.md", '
    '"p4_outline_gen_status": "completed", "p4_validate_status": "passed", '
    '"content_plan_status": "completed", "total_pages": 15, "content_pages": 13}}`'
)

_INCIDENT_INPUTS = {
    "output_dir": "C:/ws/20260917151247/output/20260917_151258_000",
    "outline_path": "C:/ws/20260917151247/output/20260917_151258_000/outline.md",
    "topic": "糖尿病防治指南",
    "p4_validate_status": "failed",
}

_STAGE_FAILURE = "fallback 未达成节点 p4_content_plan 契约: fallback 输出未包含 JSON 契约声明"


def _make_executor(spawn_output: str) -> SkillTurboExecutor:
    adapter = MagicMock()
    adapter.spawn_fallback = AsyncMock(return_value=spawn_output)
    env = MagicMock()
    env.config = {}
    env.skill_code_import_prefixes = (
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes",
    )
    env.fallback_handler = DeepAgentFallbackHandler(
        adapter, request_id="req-ut", channel_id="officeclaw", session_id="sess-ut"
    )
    return SkillTurboExecutor(environment=env)


class _PPTOrchestratorRootNode(PlanNode):
    """PPT 生成任务流根节点（本地合成，校验语义对齐 PPTGenRootNode）。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="ppt_gen_root",
            instruction="PPT生成任务流根节点，串联P0-P10全流程",
            sub_plans=[],
            depth=0,
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(_STAGE_FAILURE)

    async def _execute_stream(self, inputs: dict[str, Any]):
        raise RuntimeError(_STAGE_FAILURE)
        yield  # 使本方法成为异步生成器

    def validate_fallback_success(
        self,
        inputs: dict[str, Any],
        contract_result: dict[str, Any],
    ) -> str | None:
        merged = {**inputs, **(contract_result or {})}
        pptx_path = str(merged.get("pptx_path") or "").strip()
        if not pptx_path or not Path(pptx_path).is_file():
            return (
                "root fallback 未产出可交付的 PPTX（pptx_path 缺失或文件不存在），"
                "仅完成部分阶段不能视为全流程成功"
            )
        if str(merged.get("delivery_status") or "").strip() not in ("ok", "partial"):
            return "root fallback 未完成 P10 交付（delivery_status 缺失或为 failed）"
        return None


@pytest.mark.asyncio
async def test_root_fallback_false_success_rejected():
    """验证编排根节点 fallback 仅修复部分阶段即声明 success=true 时按 FallbackContractError 拒绝。"""
    ex = _make_executor(_INCIDENT_OUTPUT)
    root = _PPTOrchestratorRootNode()
    ex._bind_node_callbacks(root)
    inputs = dict(_INCIDENT_INPUTS)

    with pytest.raises(FallbackContractError):
        async for _ in root.run_stream(inputs):
            pass
