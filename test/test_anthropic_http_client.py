"""Exercise Claude entry points with real SDK client validation and no network."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anthropic import Anthropic

from claude_chat.attachment_parser import ocr_image_local_or_cloud
from claude_chat.clients.models import fetch_available_models


class AnthropicHttpClientTests(unittest.TestCase):
    def make_client(self, **kwargs):
        # Keep the actual SDK type check; only replace the outbound API calls.
        self.addCleanup(kwargs["http_client"].close)
        client = Anthropic(**kwargs)
        self.addCleanup(client.close)
        return client

    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.list_models = self.context.enter_context(patch("anthropic.resources.models.Models.list"))
        self.list_models.return_value = SimpleNamespace(
            data=[
                SimpleNamespace(
                    id="claude-sonnet-4-6",
                    display_name="Claude Sonnet 4.6",
                    type="model",
                )
            ]
        )
        self.create_message = self.context.enter_context(patch("anthropic.resources.messages.Messages.create"))
        self.create_message.return_value = SimpleNamespace(content=[SimpleNamespace(type="text", text="OCR result")])

    def test_model_discovery_accepts_http_client(self):
        with (
            patch("claude_chat.clients.models.Anthropic", side_effect=self.make_client),
            patch("claude_chat.clients.models.enrich_with_registry", side_effect=lambda models, _: models),
        ):
            models = fetch_available_models("test-key", "none", "")
        self.assertEqual([model["id"] for model in models], ["claude-sonnet-4-6"])
        self.list_models.assert_called_once()

    def test_cloud_ocr_accepts_http_client(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.png"
            image.write_bytes(b"offline image fixture")
            with patch("anthropic.Anthropic", side_effect=self.make_client):
                result = ocr_image_local_or_cloud(
                    image, ocr_mode="cloud", cloud_provider="claude", api_key="test-key", proxy_mode="none"
                )
        self.assertEqual(result, "OCR result")
        self.create_message.assert_called_once()
