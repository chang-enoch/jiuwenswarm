# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""[PERF 实验] symphony 关闭时短路 evolution rail 轨迹采集。

实测(py-spy, N=1 连续轮次): EvolutionRail.after_model_call 占回合时间 ~40%
(drain/enrich/merge 轨迹管道),而 symphony.enabled=false 时这些数据无处可去。
本补丁在 symphony 主开关关闭时将 after_model_call / after_tool_call /
after_task_iteration 短路为 no-op,用于量化验证;生产化修复应在核心侧
"未启用即不订阅"。
"""

import logging

logger = logging.getLogger(__name__)

_APPLIED = False


def apply_evolution_rail_short_circuit() -> None:
    global _APPLIED
    if _APPLIED:
        return
    try:
        from openjiuwen.harness.rails.evolution.evolution_rail import EvolutionRail
    except Exception:  # noqa: BLE001 - 核心无该 rail 时静默跳过
        logger.debug("[evolution_short_circuit] EvolutionRail 不可用,跳过")
        return

    def _make_short_circuited(name: str, orig):
        async def _short_circuited(self, ctx):  # noqa: ANN001, ANN202
            try:
                from jiuwenswarm.symphony.config import load_symphony_config

                cfg = load_symphony_config()
                if not cfg.enabled:
                    return None  # symphony 未启用:轨迹采集短路
            except Exception:  # noqa: BLE001 - 判定失败走原路径
                pass
            return await orig(self, ctx)

        _short_circuited.__name__ = name
        return _short_circuited

    for _name in (
        "before_invoke",  # 订阅轨迹 span:每事件序列化(to_json_compatible/_copy_json)的源头
        "after_model_call",
        "after_tool_call",
        "after_task_iteration",
    ):
        _orig = getattr(EvolutionRail, _name, None)
        if _orig is not None:
            setattr(EvolutionRail, _name, _make_short_circuited(_name, _orig))
    _APPLIED = True
    logger.info("[evolution_short_circuit] 已应用: symphony 关闭时跳过轨迹采集")
