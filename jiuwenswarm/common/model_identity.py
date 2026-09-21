"""Credential-free model identity references shared by relay and team routing."""

from __future__ import annotations

import hashlib
import json
from typing import Any

MODEL_IDENTITY_REFERENCE_PREFIX = "model-identity-v1:"


def _normalized(value: Any) -> str:
    return str(value or "").strip()


def build_model_identity_reference(model_name: Any, client_config: dict[str, Any]) -> str:
    identity = {
        "api_base": _normalized(client_config.get("api_base")).rstrip("/"),
        "model_name": _normalized(model_name),
        "provider": _normalized(client_config.get("client_provider")).lower(),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"{MODEL_IDENTITY_REFERENCE_PREFIX}{digest}"


def normalize_model_identity_reference(value: Any) -> str:
    normalized = _normalized(value).lower()
    digest = normalized.removeprefix(MODEL_IDENTITY_REFERENCE_PREFIX)
    if (
        not normalized.startswith(MODEL_IDENTITY_REFERENCE_PREFIX)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError(f"invalid model reference: {normalized!r}")
    return normalized