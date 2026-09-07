"""Offline contract tests for provider-native Files API request preparation."""

from pathlib import Path
from types import SimpleNamespace

from claude_chat.clients.deepseek import convert_messages_to_openai
from claude_chat.clients.file_uploads import (
    prepare_anthropic_files,
    prepare_gemini_files,
    prepare_openai_compatible_files,
)
from claude_chat.clients.gemini import convert_messages_to_gemini


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
