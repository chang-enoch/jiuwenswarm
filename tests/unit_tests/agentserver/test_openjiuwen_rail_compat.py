# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compatibility for older openjiuwen evolution-rail constructors."""

from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec, RailSpec
from openjiuwen.core.single_agent import AgentCard

from jiuwenswarm.common.openjiuwen_rail_compat import (
    _wrap_init_for_extra_kwargs,
    filter_unsupported_kwargs,
    install_evolution_rail_kwargs_compat,
    install_unknown_rail_skip_compat,
)


def test_filter_drops_signal_trigger_when_constructor_lacks_it():
    def _init(self, *, review_trigger=None):
        del self
        return review_trigger

    filtered = filter_unsupported_kwargs(
        _init,
        {"review_trigger": False, "signal_trigger": True},
    )
    assert filtered == {"review_trigger": False}


def test_wrapped_rail_init_ignores_unknown_kwargs():
    class DummyRail:
        def __init__(self, *, review_trigger=None):
            self.review_trigger = review_trigger

    _wrap_init_for_extra_kwargs(DummyRail)
    rail = DummyRail(review_trigger=False, signal_trigger=True)
    assert rail.review_trigger is False


def test_install_evolution_rail_kwargs_compat_is_idempotent():
    install_evolution_rail_kwargs_compat()
    install_evolution_rail_kwargs_compat()
    from openjiuwen.harness.rails import SkillEvolutionRail

    assert getattr(SkillEvolutionRail.__init__, "_jiuwenswarm_kwargs_compat", False)
    assert getattr(RailSpec.build, "_jiuwenswarm_unknown_rail_skip", False)


def test_unknown_rail_type_is_skipped_not_raised():
    install_unknown_rail_skip_compat()
    assert RailSpec(type="swarm.__does_not_exist__").build(language="cn") is None


def test_deep_agent_resolve_parts_survives_unknown_rail():
    """Persisted Spec with a future rail type must still materialize parts."""
    install_unknown_rail_skip_compat()
    parts = DeepAgentSpec(
        card=AgentCard(id="compat-agent", name="compat-agent"),
        rails=[
            RailSpec(type="swarm.__does_not_exist__"),
        ],
        enable_task_loop=False,
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_tool_resilience_rail=False,
        auto_create_workspace=False,
    ).resolve_parts()
    # Unknown rail omitted; build must not raise.
    assert parts is not None
