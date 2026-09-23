# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime compatibility shims for older / mismatched openjiuwen SDK surfaces."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_COMPAT_FLAG = "_jiuwenswarm_kwargs_compat"
_UNKNOWN_RAIL_SKIP_FLAG = "_jiuwenswarm_unknown_rail_skip"


def filter_unsupported_kwargs(func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop kwargs that ``func`` cannot accept unless it already takes **kwargs."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return kwargs
    if any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    ):
        return kwargs
    allowed = set(signature.parameters)
    return {key: value for key, value in kwargs.items() if key in allowed}


def _wrap_init_for_extra_kwargs(cls: type) -> None:
    """Allow newer trajectory/signal kwargs against older rail constructors."""
    original = cls.__init__
    if getattr(original, _COMPAT_FLAG, False):
        return

    def _compat(self, *args, **kwargs):
        return original(self, *args, **filter_unsupported_kwargs(original, kwargs))

    setattr(_compat, _COMPAT_FLAG, True)
    cls.__init__ = _compat  # type: ignore[method-assign]


def install_evolution_rail_kwargs_compat() -> None:
    """Older rails may lack signal_trigger / trajectory_span_processor parameters."""
    try:
        from openjiuwen.harness.rails import (
            SkillCreateRail,
            SkillEvolutionRail,
            TeamSkillCreateRail,
            TeamSkillEvolutionRail,
        )
    except ImportError as exc:
        logger.debug("skip evolution rail kwargs compat: %s", exc)
        return

    for cls in (
        SkillEvolutionRail,
        TeamSkillEvolutionRail,
        SkillCreateRail,
        TeamSkillCreateRail,
    ):
        if isinstance(cls, type):
            _wrap_init_for_extra_kwargs(cls)

    # Keep Team / Spec rebuilds alive when a persisted rail type is newer than
    # the providers registered in this process (temporary forward-compat).
    install_unknown_rail_skip_compat()


def install_unknown_rail_skip_compat() -> None:
    """Skip unknown ``RailSpec`` types instead of aborting Spec materialization.

    Persisted Team / DeepAgent specs may reference rail providers that this
    process has not registered (version skew). ``RailSpec.build`` normally
    raises ``ValueError``; callers already flatten via ``_as_built_list``, which
    drops ``None``. Returning ``None`` therefore omits only the missing rail
    and keeps the rest of the agent build intact.

    Only unknown *types* are skipped. Failures inside a registered provider
    still propagate.
    """
    try:
        from openjiuwen.harness.schema import deep_agent_spec as spec_mod
    except ImportError as exc:
        logger.debug("skip unknown-rail compat: %s", exc)
        return

    rail_spec_cls = getattr(spec_mod, "RailSpec", None)
    if not isinstance(rail_spec_cls, type):
        return
    original = rail_spec_cls.build
    if getattr(original, _UNKNOWN_RAIL_SKIP_FLAG, False):
        return

    registry = getattr(spec_mod, "_RAIL_PROVIDER_REGISTRY", None)
    if registry is None:
        return

    def _build_skip_unknown(self, *args, **kwargs):
        rail_type = str(getattr(self, "type", "") or "").strip()
        if rail_type and rail_type not in registry:
            # Ensure builtins are loaded once before deciding the type is truly
            # unknown (mirrors RailSpec.build's ensure_builtin_elements_registered).
            try:
                from openjiuwen.harness.manifest import ensure_builtin_elements_registered

                ensure_builtin_elements_registered()
            except Exception as exc:  # noqa: BLE001 — best-effort before skip
                logger.debug(
                    "unknown-rail compat: builtin registration failed before skip: %s",
                    exc,
                )
            if rail_type not in registry:
                logger.warning(
                    "[openjiuwen_rail_compat] skipping unknown rail type %r; "
                    "agent build continues without it",
                    rail_type,
                )
                return None
        return original(self, *args, **kwargs)

    setattr(_build_skip_unknown, _UNKNOWN_RAIL_SKIP_FLAG, True)
    rail_spec_cls.build = _build_skip_unknown  # type: ignore[method-assign]
    logger.info(
        "[openjiuwen_rail_compat] unknown RailSpec types will be skipped "
        "(temporary forward-compat)"
    )
