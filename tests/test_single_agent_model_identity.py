from types import SimpleNamespace

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.model_identity import build_model_identity_reference
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def _adapter(*models):
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._model = models[0] if models else None
    adapter._model_cache = {f"glm-5.3#{i}": model for i, model in enumerate(models)}
    adapter._model_name_to_keys = {"glm-5.3": [f"glm-5.3#{i}" for i in range(len(models))]}
    adapter._model_identity_to_keys = {}
    for i, model in enumerate(models):
        config = model.model_client_config
        ref = build_model_identity_reference(model.model_config.model_name, vars(config))
        adapter._model_identity_to_keys.setdefault(ref, []).append(f"glm-5.3#{i}")
    return adapter


def _model(base, name="glm-5.3"):
    return SimpleNamespace(
        model_client_config=SimpleNamespace(api_base=base, client_provider="OpenAI"),
        model_config=SimpleNamespace(model_name=name),
    )


def _request(**params):
    return AgentRequest(request_id="r1", params=params)


def test_single_request_resolves_same_name_by_identity():
    first, second = _model("https://one.example"), _model("https://two.example")
    adapter = _adapter(first, second)
    ref = build_model_identity_reference("glm-5.3", vars(second.model_client_config))
    assert adapter._resolve_model_for_request(_request(model_name="glm-5.3", model_ref=ref)) is second


def test_missing_owner_reference_fails_closed():
    adapter = _adapter(_model("https://one.example"))
    with pytest.raises(ValueError, match="not found"):
        adapter._resolve_model_for_request(_request(model_name="glm-5.3", model_ref="model-identity-v1:" + "0" * 64))


def test_malformed_reference_fails_closed():
    adapter = _adapter(_model("https://one.example"))
    with pytest.raises(ValueError, match="invalid"):
        adapter._resolve_model_for_request(_request(model_name="glm-5.3", model_ref="bad-ref"))


def test_ambiguous_reference_fails_closed():
    model = _model("https://one.example")
    adapter = _adapter(model, model)
    ref = build_model_identity_reference("glm-5.3", vars(model.model_client_config))
    with pytest.raises(ValueError, match="ambiguous"):
        adapter._resolve_model_for_request(_request(model_name="glm-5.3", model_ref=ref))


def test_name_mismatch_fails_closed():
    adapter = _adapter(_model("https://one.example"))
    ref = build_model_identity_reference("glm-5.3", vars(adapter._model.model_client_config))
    with pytest.raises(ValueError, match="does not match"):
        adapter._resolve_model_for_request(_request(model_name="other-model", model_ref=ref))


def test_without_model_ref_preserves_name_lookup():
    first, second = _model("https://one.example"), _model("https://two.example")
    adapter = _adapter(first, second)
    assert adapter._resolve_model_for_request(_request(model_name="glm-5.3")) is first


def test_legacy_cache_build_populates_identity_index(monkeypatch):
    model = _model("https://legacy.example")
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._model_cache = {}
    adapter._model_identity_to_keys = {}
    monkeypatch.setattr(adapter, "_build_model_from_entry", lambda mcc, mco: model)

    adapter._build_model_cache_legacy(
        {
            "models": {
                "default": {
                    "model_client_config": {
                        "api_base": "https://legacy.example",
                        "model_name": "glm-5.3",
                        "client_provider": "OpenAI",
                    },
                    "model_config_obj": {},
                }
            }
        }
    )

    ref = build_model_identity_reference(
        "glm-5.3",
        {
            "api_base": "https://legacy.example",
            "model_name": "glm-5.3",
            "client_provider": "OpenAI",
        },
    )
    assert adapter._resolve_model_for_request(_request(model_name="glm-5.3", model_ref=ref)) is model
