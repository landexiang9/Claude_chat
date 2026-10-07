"""Offline contract tests for provider-native Files API request preparation."""

import queue
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from claude_chat.clients.deepseek import convert_messages_to_openai
from claude_chat.clients.file_uploads import (
    prepare_anthropic_files,
    prepare_custom_provider_files,
    prepare_gemini_files,
    prepare_openai_compatible_files,
)
from claude_chat.clients.gemini import convert_messages_to_gemini
from claude_chat.platform_params import PlatformParamMapper
from claude_chat.provider_adapters import adapter_accepts_attachment, normalize_custom_provider_adapter


def _message(path: Path, kind="image", mime="image/png"):
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": "describe"},
            {
                "type": kind,
                "source": {"file_path": str(path), "media_type": mime},
                "_attachment": {"preview_id": "local-only"},
            },
        ],
    }]


class AnthropicFiles:
    def __init__(self):
        self.calls = []

    def upload(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="file_claude")


class OpenAIFiles:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id="file-api-deepseek")


class GeminiFiles:
    def __init__(self):
        self.calls = []

    def upload(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            name="files/gemini-file",
            uri="https://generativelanguage.googleapis.com/files/gemini-file",
            mime_type="application/pdf",
            state=SimpleNamespace(name="ACTIVE"),
        )


def test_anthropic_uses_file_id_and_cache(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    files = AnthropicFiles()
    client = SimpleNamespace(files=files)
    prepared = prepare_anthropic_files(client, _message(path), "anthropic:test", 7200)
    prepared_again = prepare_anthropic_files(client, _message(path), "anthropic:test", 7200)
    source = prepared[0]["content"][1]["source"]
    assert source == {"type": "file", "file_id": "file_claude"}
    assert "_attachment" not in prepared[0]["content"][1]
    assert prepared_again == prepared
    assert len(files.calls) == 1
    assert files.calls[0]["expires_in_seconds"] == 7200


def test_deepseek_uses_files_api_file_content_part(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    files = OpenAIFiles()
    client = SimpleNamespace(files=files)
    prepared = prepare_openai_compatible_files(
        client,
        _message(path),
        "deepseek:test",
        purpose="user_data",
        image_only=True,
    )
    converted = convert_messages_to_openai(prepared)
    assert files.calls[0]["purpose"] == "user_data"
    assert files.calls[0]["expires_after"] == {"anchor": "created_at", "seconds": 172800}
    assert converted[0]["content"] == [
        {"type": "text", "text": "describe"},
        {"type": "file", "file_id": "file-api-deepseek"},
    ]


def test_gemini_uses_uploaded_file_uri(tmp_path):
    path = tmp_path / "document.pdf"
    path.write_bytes(b"%PDF")
    files = GeminiFiles()
    client = SimpleNamespace(files=files)
    prepared = prepare_gemini_files(
        client,
        _message(path, kind="document", mime="application/pdf"),
        "gemini:test",
    )
    converted = convert_messages_to_gemini(prepared)
    assert files.calls[0]["file"] == str(path)
    assert converted[0]["parts"][1] == {
        "file_data": {
            "file_uri": "https://generativelanguage.googleapis.com/files/gemini-file",
            "mime_type": "application/pdf",
        }
    }


def test_openrouter_adapter_uses_native_inline_parts(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"png")
    pdf = tmp_path / "document.pdf"
    pdf.write_bytes(b"%PDF")
    messages = _message(image) + _message(pdf, kind="document", mime="application/pdf")
    prepared = prepare_custom_provider_files(None, messages, "unused", "openrouter")
    converted = convert_messages_to_openai(prepared)
    assert converted[0]["content"][1]["type"] == "image_url"
    assert converted[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert converted[1]["content"][1] == {
        "type": "file",
        "file": {
            "filename": "document.pdf",
            "file_data": "data:application/pdf;base64,JVBERg==",
        },
    }


def test_custom_adapter_legacy_migration_and_capabilities():
    assert normalize_custom_provider_adapter(None, True) == "openai_files"
    assert normalize_custom_provider_adapter(None, False) == "local"
    assert normalize_custom_provider_adapter("anthropic_files") == "anthropic"
    assert adapter_accepts_attachment("inline_images", ".png")
    assert not adapter_accepts_attachment("inline_images", ".pdf")
    assert adapter_accepts_attachment("openrouter", ".pdf")
    mapped = PlatformParamMapper.map_params(
        "custom:test",
        {
            "custom_providers": [{
                "id": "test",
                "provider_adapter": "anthropic",
                "file_upload_expires_in_seconds": 7776000,
            }]
        },
    )
    assert mapped["provider_adapter"] == "anthropic"
    assert mapped["file_upload_expires_in_seconds"] == 7776000


def test_custom_native_adapters_route_to_matching_clients():
    from claude_chat.clients.dispatcher import stream_claude_response

    common = dict(
        api_key="unused",
        proxy_mode="none",
        proxy_url="",
        messages=[],
        model="test",
        max_tokens=128,
        temperature=0.7,
        thinking_config=None,
        streaming_queue=queue.Queue(),
        active_platform="custom:test",
        custom_api_key="secret",
        custom_api_url="https://gateway.example",
    )
    with patch("claude_chat.clients.dispatcher.stream_claude_response_native") as anthropic:
        stream_claude_response(**common, provider_adapter="anthropic")
        assert anthropic.call_args.kwargs["api_url"] == "https://gateway.example"
        assert anthropic.call_args.kwargs["file_upload_enabled"] is True
    with patch("claude_chat.clients.dispatcher.stream_gemini_response") as gemini:
        stream_claude_response(**common, provider_adapter="gemini")
        assert gemini.call_args.kwargs["api_url"] == "https://gateway.example"
        assert gemini.call_args.kwargs["file_upload_enabled"] is True
    with patch("claude_chat.clients.dispatcher.stream_deepseek_response") as openai:
        stream_claude_response(**common, provider_adapter="openrouter")
        assert openai.call_args.kwargs["file_upload_adapter"] == "openrouter"


def test_anthropic_custom_base_url_does_not_duplicate_v1():
    from claude_chat.clients.claude import _anthropic_base_url

    assert _anthropic_base_url("https://gateway.example/v1/") == "https://gateway.example"
    assert _anthropic_base_url("https://gateway.example/anthropic/v1") == "https://gateway.example/anthropic"
