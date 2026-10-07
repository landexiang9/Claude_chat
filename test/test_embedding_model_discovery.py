"""Embedding catalogs use isolated metadata requests, never paid generation or personal config."""

import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from claude_chat.api_bridge import WebAPI
from claude_chat.memory_embeddings import _embedding_model, fetch_embedding_models


def provider_config(url="https://openrouter.ai/api/v1", catalog=""):
    return {
        "active_platform": "claude",
        "model": "keep-chat-model",
        "custom_providers": [{"id": "catalog", "api_url": url, "models_api_url": catalog}],
        "custom_catalog_api_key": "private-test-key",
        "proxy_mode": "none",
    }


def catalog_client(payload):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value = httpx.Response(200, json=payload)
    return client


@pytest.mark.parametrize(
    "address",
    [
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/chat/completions",
        "https://openrouter.ai/api/v1/embeddings",
        "https://openrouter.ai/api/v1/models",
        "https://openrouter.ai/api/v1/embeddings/models",
        "https://openrouter.ai",
    ],
)
def test_openrouter_dedicated_catalog_url_and_no_paid_requests(address):
    client = catalog_client(
        {
            "data": [
                {"id": "qwen/qwen3-embedding-8b", "name": "Qwen Embed"},
                {"id": "vendor/opaque-vector", "architecture": {"output_modalities": ["embeddings"]}},
                {"id": "vendor/embedding-image", "architecture": {"input_modalities": ["image"]}},
                {"id": "chat", "architecture": {"output_modalities": ["text"]}},
                {"id": "qwen/qwen3-embedding-8b"},
            ]
        }
    )
    config = provider_config(address)
    before = deepcopy(config)
    with patch("claude_chat.clients.base.build_http_client", return_value=client) as build:
        models = fetch_embedding_models("custom:catalog", config)
    assert [model["id"] for model in models] == ["qwen/qwen3-embedding-8b", "vendor/opaque-vector"]
    assert client.get.call_args.args == ("https://openrouter.ai/api/v1/embeddings/models",)
    assert client.get.call_args.kwargs["headers"] == {"Authorization": "Bearer private-test-key"}
    build.assert_called_once_with("none", "")
    client.post.assert_not_called()
    client.__exit__.assert_called_once()
    assert config == before


@pytest.mark.parametrize(
    "catalog, expected",
    [
        ("", "https://compatible.test/v1/models"),
        ("https://catalog.test/v2", "https://catalog.test/v2/models"),
        ("https://catalog.test/v2/models?tenant=qa", "https://catalog.test/v2/models?tenant=qa"),
        ("https://catalog.test/v2/embeddings/models", "https://catalog.test/v2/embeddings/models"),
    ],
)
def test_custom_catalog_override_proxy_and_capability_filter(catalog, expected):
    config = provider_config("https://compatible.test/v1/responses", catalog)
    config.update(proxy_mode="custom", proxy_url="http://127.0.0.1:9999")
    client = catalog_client(
        {
            "data": [
                {"id": "text-embedding-3-small"},
                {"id": "bge-m3"},
                {"id": "chat-model"},
                {"id": "bge-reranker-v2-m3"},
                {"id": "opaque", "type": "embedding"},
            ]
        }
    )
    with patch("claude_chat.clients.base.build_http_client", return_value=client) as build:
        result = fetch_embedding_models("custom:catalog", config)
    assert client.get.call_args.args == (expected,)
    build.assert_called_once_with("custom", "http://127.0.0.1:9999")
    ids = [model["id"] for model in result]
    assert "bge-m3" in ids and "text-embedding-3-small" in ids and "opaque" in ids
    if not expected.endswith("/embeddings/models"):
        assert "chat-model" not in ids and "bge-reranker-v2-m3" not in ids


def test_gemini_embed_actions_pagination_and_context_cleanup():
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.models.list.return_value = [
        SimpleNamespace(name="models/gemini-flash", supported_actions=["generateContent"]),
        SimpleNamespace(
            name="models/gemini-embedding-2", display_name="Embedding 2", supported_actions=["embedContent"]
        ),
        SimpleNamespace(name="models/unknown", supported_actions=[]),
    ]
    with patch("google.genai.Client", return_value=client) as constructor:
        result = fetch_embedding_models("gemini", {"gemini_api_key": "fake", "proxy_mode": "none"})
    assert result == [{"id": "gemini-embedding-2", "display_name": "Embedding 2"}]
    client.models.list.assert_called_once_with(config={"page_size": 100})
    client.models.embed_content.assert_not_called()
    client.__exit__.assert_called_once()
    options = constructor.call_args.kwargs["http_options"]
    assert options.timeout == 15000 and options.client_args["trust_env"] is False


def test_discovery_filters_malformed_catalog_rows_and_explicit_chat_capability():
    for row in [
        None,
        {},
        {"id": []},
        {"id": "x\nsecret"},
        {"id": "x" * 201},
        {"id": "embedding-chat", "output_modalities": ["text"]},
        {"id": "odd", "type": []},
    ]:
        assert _embedding_model(row) is None
    assert _embedding_model({"id": "<script>-embedding", "name": "<img src=x>"})["id"] == "<script>-embedding"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("secret-key in private proxy URL"),
        ValueError("供应商返回的模型列表格式无效 secret-key"),
    ],
)
def test_upstream_exception_content_does_not_reach_ui(failure):
    client = catalog_client({})
    client.get.side_effect = failure
    with patch("claude_chat.clients.base.build_http_client", return_value=client):
        with pytest.raises(ValueError, match="仍可手动填写") as raised:
            fetch_embedding_models("custom:catalog", provider_config())
    assert "secret-key" not in str(raised.value)


def test_empty_http_error_and_invalid_payload_are_distinguished():
    client = catalog_client({"data": []})
    with patch("claude_chat.clients.base.build_http_client", return_value=client):
        assert fetch_embedding_models("custom:catalog", provider_config()) == []
        client.get.return_value = httpx.Response(401, json={"error": "secret-key"})
        with pytest.raises(ValueError, match="HTTP 401") as raised:
            fetch_embedding_models("custom:catalog", provider_config())
        assert "secret-key" not in str(raised.value)
        client.get.return_value = httpx.Response(200, json={"data": {}})
        with pytest.raises(ValueError, match="列表格式无效"):
            fetch_embedding_models("custom:catalog", provider_config())


def test_service_dispatch_releases_app_lock_and_does_not_initialize_memory():
    app = SimpleNamespace(lock=threading.RLock(), config=SimpleNamespace(data=provider_config()))
    api = WebAPI(app)

    def discover(platform, config):
        assert not app.lock._is_owned()
        config["model"] = "changed-snapshot"
        return [{"id": "bge-m3", "display_name": "bge-m3"}]

    with patch("claude_chat.memory_embeddings.fetch_embedding_models", side_effect=discover):
        result = api.memory_operation("embedding_models", {"platform": "custom:catalog"})
    assert result["success"] and result["platform"] == "custom:catalog"
    assert app.config.data["model"] == "keep-chat-model" and not hasattr(app, "_memory_store")
    assert not api.memory_operation("embedding_models", {"platform": []})["success"]


def test_unconfigured_unsupported_local_and_invalid_urls_do_not_call_remote():
    with patch("claude_chat.clients.base.build_http_client") as build:
        assert fetch_embedding_models("local", {}) == []
        for platform in ["claude", "custom:missing"]:
            with pytest.raises(ValueError, match="请选择"):
                fetch_embedding_models(platform, provider_config())
        config = provider_config()
        config["custom_catalog_api_key"] = ""
        with pytest.raises(ValueError, match="API Key"):
            fetch_embedding_models("custom:catalog", config)
        with pytest.raises(ValueError, match="地址无效"):
            fetch_embedding_models("custom:catalog", provider_config("https://name:password@example.test/v1"))
        build.assert_not_called()
