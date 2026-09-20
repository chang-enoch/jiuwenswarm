from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

from jiuwenswarm.common.config_provider import (
    AgentConfigContext,
    AgentConfigResult,
    ConfigProvider,
)
from jiuwenswarm.common.path_provider import (
    PathCategory,
    PathContext,
    PathProvider,
)
from jiuwenswarm.extensions.sdk.config_provider import ConfigProviderExtension
from jiuwenswarm.extensions.sdk.path_provider import PathProviderExtension


logger = logging.getLogger("jiuwenswarm.provider_probe")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _probe_enabled() -> bool:
    # 企业版默认打开，便于随镜像启动后直接验证；其他版本默认关闭。
    edition_default = os.getenv("JIUWENSWARM_EDITION", "").strip().lower() == "enterprise"
    return _env_flag("PROVIDER_PROBE_ENABLED", edition_default)


def _is_agentserver_process() -> bool:
    # 企业版 Gateway 也会扫描默认扩展目录，Provider 只应注册到 AgentServer。
    role = os.getenv("ROLE", "").strip().lower()
    return not role or role == "agentserver"


def _probe_root() -> Path:
    configured = os.getenv("PROVIDER_PROBE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".jiuwenswarm" / "provider_probe_data"


class ProbePathProvider(PathProvider):
    name = "provider-probe-paths"

    def resolve_path(
        self,
        category: PathCategory,
        ctx: PathContext,
        *,
        node: str | None = None,
        session_id: str | None = None,
    ) -> Path | None:
        logger.info(
            "zqh1 provider_probe event=resolve_path category=%s node=%s session_id=%s "
            "service_id=%s agent_id=%s workspace_key=%s",
            category.value,
            node,
            session_id,
            ctx.service_id,
            ctx.agent_id,
            ctx.workspace_key,
        )

        if os.getenv("PROVIDER_PROBE_THROW_PATH") == "1":
            raise RuntimeError("provider probe path failure")

        root = _probe_root()

        if category is PathCategory.LOGS:
            return root / "logs-zqh" / "logs"

        if category is PathCategory.CHECKPOINT:
            return root / "checkpoint-zqh" / "checkpoint"

        return None

    def resolve_path_list(
        self,
        category: PathCategory,
        ctx: PathContext,
    ) -> list[Path] | None:
        logger.info(
            "zqh1 provider_probe event=resolve_path_list category=%s session_id=%s",
            category.value,
            ctx.session_id,
        )
        return None

    def build_workspace_directories(
        self,
        ctx: PathContext,
    ) -> list[dict] | None:
        logger.info(
            "zqh1 provider_probe event=build_workspace_directories session_id=%s",
            ctx.session_id,
        )

        edition_default = (
            os.getenv("JIUWENSWARM_EDITION", "").strip().lower() == "enterprise"
        )
        if not _env_flag("PROVIDER_PROBE_WORKSPACE_NODE", edition_default):
            return None

        return [
            {
                "name": "provider_probe",
                "description": "Provider probe workspace node",
                "path": "provider_probe",
                "children": [],
            }
        ]


class ProbeConfigProvider(ConfigProvider):
    name = "provider-probe-config"

    def get_process_config(self) -> dict | None:
        mode = os.getenv("PROVIDER_PROBE_PROCESS_MODE", "dict")

        logger.info(
            "zqh1 provider_probe event=get_process_config mode=%s",
            mode,
        )

        if mode == "none":
            return None

        if mode == "error":
            raise RuntimeError("provider probe process config failure")

        from jiuwenswarm.common.utils import get_config_file, load_yaml_dict

        config_path = get_config_file()
        config = load_yaml_dict(config_path)
        if not config:
            logger.warning(
                "zqh1 provider_probe event=process_config_empty path=%s",
                config_path,
            )
            return None

        config["_provider_probe_process_marker"] = "process-provider-used"
        provider_probe = config.get("provider_probe")
        if not isinstance(provider_probe, dict):
            provider_probe = {}
            config["provider_probe"] = provider_probe
        provider_probe["enabled"] = True

        logger.info(
            "zqh1 provider_probe event=process_config_loaded path=%s",
            config_path,
        )
        return config

    async def load_process_config(self) -> dict | None:
        logger.info("zqh1 provider_probe event=load_process_config")
        return self.get_process_config()

    async def load_agent_config(
        self,
        ctx: AgentConfigContext,
        *,
        base: dict,
    ) -> AgentConfigResult | None:
        mode = os.getenv("PROVIDER_PROBE_AGENT_MODE", "result")

        logger.info(
            "zqh1 provider_probe event=load_agent_config mode=%s "
            "base_has_process_marker=%s service_id=%s agent_id=%s "
            "workspace_key=%s",
            mode,
            "_provider_probe_process_marker" in base,
            ctx.service_id,
            ctx.agent_id,
            ctx.workspace_key,
        )

        if mode == "none":
            return None

        if mode == "error":
            raise RuntimeError("provider probe agent config failure")

        result_config = copy.deepcopy(base)
        result_config["_provider_probe_agent_marker"] = "agent-provider-used"

        return AgentConfigResult(
            config=result_config,
            policy=None,
        )

    async def refresh(self) -> None:
        logger.info("zqh1 provider_probe event=refresh")


class ProbePathExtension(PathProviderExtension):
    def __init__(self) -> None:
        self._provider = ProbePathProvider()

    def get_path_provider(self) -> PathProvider:
        return self._provider


class ProbeConfigExtension(ConfigProviderExtension):
    def __init__(self) -> None:
        self._provider = ProbeConfigProvider()

    def get_config_provider(self) -> ConfigProvider:
        return self._provider


async def register_extensions(registry: Any) -> list[Any]:
    if not _probe_enabled():
        logger.info(
            "zqh1 provider_probe event=disabled edition=%s role=%s",
            os.getenv("JIUWENSWARM_EDITION", ""),
            os.getenv("ROLE", ""),
        )
        return []

    if not _is_agentserver_process():
        logger.info(
            "zqh1 provider_probe event=skipped_non_agentserver role=%s",
            os.getenv("ROLE", ""),
        )
        return []

    path_extension = ProbePathExtension()
    config_extension = ProbeConfigExtension()

    logger.info(
        "zqh1 provider_probe event=register_extensions root=%s",
        _probe_root(),
    )

    await path_extension.initialize(None)
    await config_extension.initialize(None)

    logger.info(
        "zqh1 provider_probe event=providers_registered config=%s path=%s",
        ProbeConfigProvider.name,
        ProbePathProvider.name,
    )

    return [path_extension, config_extension]
