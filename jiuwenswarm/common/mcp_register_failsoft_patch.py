# coding: utf-8
"""Make openjiuwen's ``DeepAgent._register_pending_mcps`` fail-soft.

Why this exists: team members carry ``DeepAgentSpec.mcps`` straight into
``DeepAgent._register_pending_mcps`` (openjiuwen 0.1.16
``harness/deep_agent.py:957-995``), which **raises on the first failing
server** — no preflight, no skip semantics. In team mode that single raise
has outsized blast radius:

- leader: the whole team round fails (``coordination.start`` propagates
  before ``finalize_round`` ever runs);
- subprocess teammate: child process exits → health check → up to 3
  exponential-backoff restarts of a deterministically-broken spawn;
- in-process teammate: the task dies un-awaited, the member silently goes
  missing and is even mislabeled READY by ``finalize_member``.

The team-spec preflight filter (``common/mcp_config.filter_unreachable_mcp_servers``)
already strips unreachable HTTP servers before spec build, but it cannot
catch non-network failures: stdio spawn errors, ``list_tools`` failures,
server_id conflicts, etc. This patch is the principled second line: every
spec.mcps consumer (team leader, in-process teammates, wiki sub-agents,
future paths) degrades to "that server's tools absent + warning" instead of
crashing member initialization.

Per-server semantics of the patched loop (vs. the stock fail-fast one):

- ``add_mcp_server`` error / unexpected exception → warning, skip server;
- server_id already registered with a *different* config → warning, keep the
  existing registration, skip (stock behavior raised);
- resource tagging failures → warning, skip server;
- ``ability_manager.add`` failure → warning, skip (server stays registered,
  only the ability wiring is dropped).

Coverage caveat: teammate **subprocesses** run pure openjiuwen
(``core/runner/spawn/child_process.py`` as ``__main__``) and never import
jiuwenswarm, so this patch does not reach them — their protection is the
preflight filter on the spec they receive. A proper upstream fix in
agent-core (per-server degrade + failure list) remains the long-term answer.

Idempotent per process (module-level ``_PATCHED`` guard), applied from
``JiuWenSwarmDeepAdapter.__init__`` next to ``apply_mcp_call_timeout_patch``.
"""
from __future__ import annotations

from typing import Any

from openjiuwen.core.common.logging import logger

_PATCHED = False

__all__ = ["apply_mcp_register_failsoft_patch"]


def _describe(mcp_config: Any) -> str:
    return (
        f"server_id={getattr(mcp_config, 'server_id', '?')} "
        f"server_name={getattr(mcp_config, 'server_name', '?')} "
        f"server_path={getattr(mcp_config, 'server_path', '?')}"
    )


def apply_mcp_register_failsoft_patch() -> None:
    """Patch ``DeepAgent._register_pending_mcps`` to degrade per server."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    from openjiuwen.core.runner.runner import Runner
    from openjiuwen.harness.deep_agent import DeepAgent

    async def _failsoft_register_pending_mcps(self: Any) -> None:
        """Register configured MCP servers, skipping ones that fail."""
        cfg = self._deep_config
        if cfg is None or not cfg.mcps:
            return

        agent_id = getattr(getattr(self, "card", None), "id", "?")
        for mcp_config in cfg.mcps:
            try:
                await _register_one(self, Runner, mcp_config, agent_id)
            except Exception as exc:
                logger.warning(
                    "[mcp-register-failsoft] MCP server registration raised, "
                    "skipping (%s): %r",
                    _describe(mcp_config),
                    exc,
                )

    async def _register_one(self: Any, runner: Any, mcp_config: Any, agent_id: str) -> None:
        server_id = getattr(mcp_config, "server_id", None)
        existing_config = runner.resource_mgr.get_mcp_server_config(server_id)
        if existing_config is None:
            result = await runner.resource_mgr.add_mcp_server(mcp_config, tag=agent_id)
            if result.is_err():
                logger.warning(
                    "[mcp-register-failsoft] MCP server registration failed, "
                    "skipping (%s): %s",
                    _describe(mcp_config),
                    result.msg(),
                )
                return
        else:
            if existing_config.model_dump() != mcp_config.model_dump():
                # Stock behavior raised here. Degrade to keeping the existing
                # registration so one conflicting member spec cannot sink the
                # whole team round.
                logger.warning(
                    "[mcp-register-failsoft] MCP server_id conflict, keeping "
                    "existing registration and skipping (%s)",
                    _describe(mcp_config),
                )
                return

            tag_result = runner.resource_mgr.add_resource_tag(server_id, agent_id)
            if tag_result.is_err():
                logger.warning(
                    "[mcp-register-failsoft] MCP resource tagging failed, "
                    "skipping (%s): %s",
                    _describe(mcp_config),
                    tag_result.msg(),
                )
                return

            for tool_id in runner.resource_mgr.get_mcp_tool_ids(server_id):
                tag_result = runner.resource_mgr.add_resource_tag(tool_id, agent_id)
                if tag_result.is_err():
                    logger.warning(
                        "[mcp-register-failsoft] MCP tool tagging failed, "
                        "skipping (%s): tool_id=%s: %s",
                        _describe(mcp_config),
                        tool_id,
                        tag_result.msg(),
                    )
                    return

        try:
            self.ability_manager.add(mcp_config)
        except Exception as exc:
            logger.warning(
                "[mcp-register-failsoft] MCP ability wiring failed, server stays "
                "registered but tools unavailable (%s): %r",
                _describe(mcp_config),
                exc,
            )

    DeepAgent._register_pending_mcps = _failsoft_register_pending_mcps

    logger.info(
        "[mcp-register-failsoft] patch applied (DeepAgent._register_pending_mcps "
        "now degrades per-server instead of raising)"
    )
