# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Config-gated lifecycle for single-agent / coding-agent observability.

The non-team counterpart of ``sync_team_observability`` /
``shutdown_team_observability`` in
``jiuwenswarm.agents.harness.team.team_manager``, and symmetric with it: this
module only reads this platform's config and toggles the runtime, while the
tracing mechanics — run root span, the session-keyed fallback that keeps it
reachable from supervisor tasks, agent-tier rail wiring and the sub-agent
dispatch hook — live in the SDK under ``openjiuwen.harness.observability``.

It is kept in a **separate file with its own state and config section** on
purpose, so the existing team scenario is not affected.

Shared-provider caveat (important):
    OpenTelemetry allows exactly ONE global ``TracerProvider`` per process, and
    initialization is a no-op if one already exists. In a process where BOTH
    team and agent observability are enabled, whichever runs first wins; the
    other silently reuses it (its exporter/endpoint/service_name are ignored).
    Provider demands are coordinated inside the SDK, so agent shutdown never
    tears down a provider the team subsystem depends on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from openjiuwen.harness.observability import (
    acquire_observability,
    release_observability,
)

from jiuwenswarm.agents.harness.observability_runtime import build_observability_config
from jiuwenswarm.common.config import (
    get_config,
    get_skill_evolution_enabled,
)
from jiuwenswarm.common.utils import get_user_workspace_dir
from jiuwenswarm.observability.config import load_trajectory_store_settings
from jiuwenswarm.observability.runtime import (
    shutdown_trajectory_runtime,
    sync_trajectory_runtime,
)

logger = logging.getLogger(__name__)

# Tracks whether observability is currently active so we can detect config
# toggles (enabled -> disabled or vice-versa) and init / shutdown accordingly
# on each single-agent request.
_agent_observability_active: bool = False

# Sticky flag: once any single-agent request has force-enabled observability
# (e.g. a ``/debug`` run with ``debug_trace.<mode>.otel_enabled``), we never
# auto-teardown the provider for the rest of the process. OTel allows only one
# global TracerProvider and re-init after shutdown is fragile, so a /debug
# toggle must not churn init/shutdown across alternating requests. The normal
# config-gated path (agent_observability.enabled hot-reload) is unaffected
# unless force was ever used.
_force_ever_enabled: bool = False


def sync_agent_observability(*, force: bool = False) -> None:
    """Synchronize single-agent observability state with current config.

    Called before each ``Runner.run_agent_streaming`` / ``Runner.run_agent`` so
    that hot-reloading the ``agent_observability.enabled`` flag takes effect
    immediately:

    * disabled -> enabled : acquire the provider (or reuse if already up)
    * enabled -> disabled : ``shutdown_agent_observability()``
    * unchanged           : no-op

    Evolution also requests the provider when the explicit switch is disabled.

    ``force=True`` (set by a ``/debug`` run when ``debug_trace.<mode>.otel_enabled``
    is true) treats ``want_enabled`` as true regardless of config, so a debug
    request can pull up OTel even when ``agent_observability.enabled`` is false.
    Once force is ever used, the provider stays up for the process (sticky — see
    ``_force_ever_enabled``) to avoid init/shutdown churn across alternating
    requests; the normal config hot-reload teardown is unchanged when evolution
    is disabled.
    """
    global _agent_observability_active, _force_ever_enabled

    config = get_config()
    cfg = config.get("agent_observability", {}) or {}
    trajectory_settings = load_trajectory_store_settings(config)
    evolution_requested = get_skill_evolution_enabled(config)
    want_enabled = (
        bool(cfg.get("enabled", False))
        or trajectory_settings.enabled
        or evolution_requested
        or force
        or _force_ever_enabled
    )
    if force:
        _force_ever_enabled = True

    if not want_enabled:
        try:
            sync_trajectory_runtime(trajectory_settings, demand="agent")
        except Exception as exc:
            logger.warning("[AgentObservability] trajectory runtime stop failed: %s", exc)
        if _agent_observability_active:
            shutdown_agent_observability()
        return

    try:
        traces_dir = str(cfg.get("traces_dir") or get_user_workspace_dir() / ".trace")
        obs_cfg = build_observability_config(
            cfg,
            service_name="jiuwenswarm-agent",
            default_backend="otlp",
            traces_dir=traces_dir,
        )
        provider_existed = acquire_observability(obs_cfg)
        was_active = _agent_observability_active
        _agent_observability_active = True
        try:
            sync_trajectory_runtime(trajectory_settings, demand="agent")
        except Exception as exc:
            # The trajectory read store is an optional fan-out. Existing file,
            # OTLP and Langfuse exporters must keep the Agent path available.
            logger.warning("[AgentObservability] trajectory runtime init failed: %s", exc)
        if not was_active:
            if provider_existed:
                logger.info(
                    "[AgentObservability] reusing existing observability provider "
                    "(owned by another subsystem)"
                )
            elif cfg.get("exporter", "otlp_grpc") == "file":
                logger.info(
                    "[AgentObservability] enabled: exporter=%s traces_dir=%s",
                    cfg.get("exporter", "otlp_grpc"),
                    traces_dir,
                )
            else:
                logger.info(
                    "[AgentObservability] enabled: exporter=%s endpoint=%s",
                    cfg.get("exporter", "otlp_grpc"),
                    cfg.get("endpoint", "http://localhost:4317"),
                )
    except Exception as exc:
        _agent_observability_active = False
        if evolution_requested:
            raise RuntimeError(
                "Agent evolution observability initialization failed"
            ) from exc
        logger.warning("[AgentObservability] init failed: %s", exc)


def shutdown_agent_observability() -> None:
    """Shutdown single-agent observability (on disable or process exit)."""
    global _agent_observability_active
    try:
        if not shutdown_trajectory_runtime(demand="agent"):
            logger.warning("[AgentObservability] trajectory runtime did not drain cleanly")
    except Exception as exc:
        logger.warning("[AgentObservability] trajectory runtime shutdown failed: %s", exc)
    if not _agent_observability_active:
        return
    try:
        release_observability()
        _agent_observability_active = False
        logger.info("[AgentObservability] disabled")
    except Exception as exc:
        logger.warning("[AgentObservability] shutdown failed: %s", exc)


# The dev-stable telemetry integration still exposes this legacy run-root API.
# New Trace paths import open/close_agent_run_span from the AgentCore SDK instead.
def _get_unified_runtime() -> Any:
    from jiuwenswarm.telemetry import get_telemetry_runtime

    return get_telemetry_runtime()


@dataclass
class _LegacyRunSpanHandle:
    root_span: Any
    binding: Any
    trace_bindings: Any
    _lock: Lock = field(default_factory=Lock, repr=False)
    _closed: bool = False

    def get_span_context(self) -> Any:
        return self.root_span.get_span_context()

    def claim_close(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
            return True


def open_agent_run_span(
    *, session_id: str = "", request_id: str = "", channel_id: str = "", mode: str = ""
) -> _LegacyRunSpanHandle | None:
    """Open a run root for the retained dev-stable telemetry integration."""
    from opentelemetry.trace import SpanKind

    from openjiuwen.agent_teams.observability.span_context import set_team_span
    from openjiuwen.extensions.observability.setup import get_tracer, is_initialized
    from openjiuwen.extensions.observability.semconv import LANGFUSE_SESSION_ID
    from openjiuwen.extensions.observability.span_context import (
        set_current_session_id,
        set_root_span,
    )

    from jiuwenswarm.extensions.identity_provider import IdentityStore
    from jiuwenswarm.telemetry.attributes import (
        APP_ID,
        DOMAIN_ID,
        GEN_AI_CONVERSATION_ID,
        JIUWENCLAW_APP_ID,
        JIUWENCLAW_CHANNEL_ID,
        JIUWENCLAW_DOMAIN_ID,
        JIUWENCLAW_REQUEST_ID,
        JIUWENCLAW_SESSION_ID,
        JIUWENCLAW_USER_ID,
        USER_ID,
    )

    runtime = _get_unified_runtime()
    unified = bool(runtime.is_unified_active())
    if unified:
        if runtime.tracer_provider is None:
            return None
        tracer = runtime.tracer_provider.get_tracer("jiuwenswarm.agent")
    else:
        if not is_initialized() or not _agent_observability_active:
            return None
        tracer = get_tracer("jiuwenswarm.agent")
    normalized_mode = (mode or "").strip()
    normalized_session = (session_id or "").strip()
    name = (
        f"agent.{normalized_mode}.{normalized_session}"
        if normalized_mode and normalized_session
        else f"agent.{normalized_mode}.run" if normalized_mode
        else f"agent.run.{normalized_session}" if normalized_session
        else "agent.run"
    )
    span = tracer.start_span(name=name, kind=SpanKind.SERVER)
    try:
        identity = IdentityStore.get_identity()
    except Exception:
        identity = None
    attributes = {
        LANGFUSE_SESSION_ID: session_id or "",
        GEN_AI_CONVERSATION_ID: session_id or "",
        JIUWENCLAW_SESSION_ID: session_id or "",
        JIUWENCLAW_REQUEST_ID: request_id or "",
        JIUWENCLAW_CHANNEL_ID: channel_id or "",
        "jiuwenswarm.mode": mode or "",
    }
    for primary, alias, value in (
        (USER_ID, JIUWENCLAW_USER_ID, getattr(identity, "user_id", None)),
        (DOMAIN_ID, JIUWENCLAW_DOMAIN_ID, getattr(identity, "domain_id", None)),
        (APP_ID, JIUWENCLAW_APP_ID, getattr(identity, "app_id", None)),
    ):
        if value not in (None, ""):
            attributes[primary] = value
            attributes[alias] = value
    for key, value in attributes.items():
        span.set_attribute(key, value)
    set_team_span(span, team_name="single-agent")
    set_root_span(span, session_id=session_id)
    set_current_session_id(session_id)
    binding = runtime.trace_bindings.bind(session_id, request_id, span)
    if unified and runtime.span_registry is not None:
        runtime.span_registry.bind_trace_attributes(span.get_span_context().trace_id, attributes)
    return _LegacyRunSpanHandle(span, binding, runtime.trace_bindings)


def close_agent_run_span(handle: Any, *, session_id: str = "") -> None:
    """Close only the legacy root owned by this handle."""
    if handle is None or not handle.claim_close():
        return
    from openjiuwen.agent_teams.observability.span_context import (
        cascade_close_children,
        clear_team_span,
        flush_child_spans,
        get_team_span,
    )
    from openjiuwen.extensions.observability.span_context import (
        clear_current_session_id,
        clear_root_span,
    )

    handle.trace_bindings.remove(handle.binding)
    root_span = handle.root_span
    owns_context = get_team_span() is root_span
    if owns_context:
        cascade_close_children()
    flush_child_spans(trace_id=root_span.get_span_context().trace_id)
    root_span.end()
    if owns_context:
        clear_team_span()
    clear_root_span(session_id=session_id, expected_span=root_span)
    clear_current_session_id()
