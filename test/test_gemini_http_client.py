"""Keep real Google SDK option validation while mocking outbound operations."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google import genai

from claude_chat.clients.gemini_http import build_gemini_http_options
from claude_chat.clients.models import fetch_available_models


@pytest.mark.parametrize("mode,url", [("system", ""), ("none", ""), ("custom", "http://127.0.0.1:7890")])
def test_real_sdk_discovery_with_proxy_and_endpoint(mode, url):
    created = []
    actual_client = genai.Client

    def make_client(**kwargs):
        client = actual_client(**kwargs)
        created.append(client)
        return client

    rows = [SimpleNamespace(name="models/gemini-test", display_name="Test", supported_actions=["generateContent"])]
    with (
        patch("google.genai.Client", side_effect=make_client),
        patch("google.genai.models.Models.list", return_value=iter(rows)),
        patch("claude_chat.clients.models.enrich_with_registry", side_effect=lambda models, _: models),
    ):
        result = fetch_available_models("offline-key", mode, url, "gemini", "https://gateway.example")
    assert result[0]["id"] == "gemini-test"
    assert created[0]._api_client._http_options.base_url == "https://gateway.example"


def test_options_timeout_and_proxy_validation():
    opts = build_gemini_http_options("custom", "http://localhost:7890", timeout_ms=30000)
    assert opts.timeout == 30000
    assert opts.client_args == {"proxy": "http://localhost:7890", "trust_env": False}
    with pytest.raises(ValueError):
        build_gemini_http_options("custom", "")


def test_discovery_closes_client_on_list_failure():
    from unittest.mock import Mock

    client = Mock()
    client.models.list.side_effect = RuntimeError("offline failure")
    with (
        patch("google.genai.Client", return_value=client),
        patch("claude_chat.clients.models.enrich_with_registry", side_effect=lambda models, _: models),
    ):
        assert fetch_available_models("offline-key", "none", "", "gemini")
    client.close.assert_called_once()


def test_real_sdk_chat_stream_with_custom_proxy():
    import queue

    from claude_chat.clients.gemini import stream_gemini_response

    events = queue.Queue()
    with patch("google.genai.models.Models.generate_content_stream", return_value=iter([])) as generate:
        stream_gemini_response(
            "offline-key",
            "https://gateway.example",
            "custom",
            "http://localhost:7890",
            [{"role": "user", "content": "hello"}],
            "gemini-2.5-flash",
            100,
            0.2,
            False,
            1024,
            "low",
            events,
            file_upload_enabled=False,
        )
    generate.assert_called_once()
    assert all(kind != "error" for kind, _ in list(events.queue))
    assert any(kind == "done" for kind, _ in list(events.queue))


def test_real_sdk_cloud_ocr_uses_shared_proxy_options(tmp_path):
    from claude_chat.attachment_parser import ocr_image_local_or_cloud

    image = tmp_path / "sample.png"
    image.write_bytes(b"offline image fixture")
    with patch("google.genai.models.Models.generate_content", return_value=SimpleNamespace(text="result")) as generate:
        assert (
            ocr_image_local_or_cloud(str(image), "cloud", "gemini", "offline-key", "custom", "http://localhost:7890")
            == "result"
        )
    assert generate.call_args.kwargs["model"] == "gemini-2.5-flash"
