"""Offline security and regression tests for managed attachment previews."""

import base64
import copy
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from claude_chat.http_router import HttpApiRouter
from claude_chat.services.attachment_store import store_attachment_path
from claude_chat.services.conversation_service import (
    conversation_for_frontend,
    migrate_and_persist_legacy_attachments,
    prepare_attachment_content,
)
from claude_chat.services.file_service import FileService


class MemoryConfig:
    def get(self, _key, default=None):
        return default


class MemoryConversationManager:
    def __init__(self, conversation=None):
        self.conversation = copy.deepcopy(conversation)
        self.saved = False

    def load_conversation(self, _conversation_id):
        return copy.deepcopy(self.conversation)

    def save_conversation(self, conversation):
        self.conversation = copy.deepcopy(conversation)
        self.saved = True

    def get_connection(self):
        conversation = self.conversation or {}
        rows = [
            {"content": json.dumps(message.get("content", ""), ensure_ascii=False)}
            for message in conversation.get("messages", [])
        ]

        class MemoryCursor:
            def fetchall(self):
                return rows

        class MemoryConnection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, _statement):
                return MemoryCursor()

        return MemoryConnection()


class DummyApp:
    def __init__(self, conversation=None):
        self.config = MemoryConfig()
        self.conv_manager = MemoryConversationManager(conversation)
        self.window = None
        self.lock = threading.RLock()


class AttachmentPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store_patch = patch(
            "claude_chat.services.attachment_store.ATTACHMENT_STORE_DIR",
            self.root / "attachments",
        )
        self.store_patch.start()

    def tearDown(self):
        self.store_patch.stop()
        self.temp_dir.cleanup()

    def _image_file(self, name="preview.png", size=(320, 200)):
        path = self.root / name
        Image.new("RGBA", size, (73, 104, 180, 180)).save(path)
        return path

    def test_pending_preview_uses_only_opaque_id(self):
        source = self.root / "notes.txt"
        source.write_text("managed preview text", encoding="utf-8")
        descriptor = store_attachment_path(source)
        self.assertNotIn("path", descriptor)
        self.assertRegex(descriptor["preview_id"], r"^[0-9a-f]{32}$")

        # A new service instance has no in-memory registry; the sidecar metadata is sufficient.
        result = FileService(DummyApp()).get_attachment_preview({
            "scope": "pending",
            "preview_id": descriptor["preview_id"],
        })
        self.assertEqual(result["preview_type"], "text")
        self.assertEqual(result["text"], "managed preview text")
        self.assertNotIn(str(source), json.dumps(result, ensure_ascii=False))

        secret = self.root / "secret.txt"
        secret.write_text("must-not-leak", encoding="utf-8")
        rejected = FileService(DummyApp()).get_attachment_preview({"path": str(secret)})
        self.assertIn("error", rejected)
        self.assertNotIn("must-not-leak", json.dumps(rejected, ensure_ascii=False))

    def test_prepared_message_uses_managed_path_and_public_dto_is_bounded(self):
        source = self.root / "payload.md"
        source.write_text("private attachment payload", encoding="utf-8")
        descriptor = store_attachment_path(source)
        block = prepare_attachment_content(descriptor, "claude", MemoryConfig())
        # Text blocks carry no source path at all; the preview ID remains durable.
        self.assertNotIn("source", block)
        self.assertEqual(block["_attachment"]["preview_id"], descriptor["preview_id"])

        conversation = {
            "id": "conversation-a",
            "messages": [{
                "role": "user",
                "content": [{"type": "text", "text": "question"}, block],
            }],
        }
        service = FileService(DummyApp(conversation))
        preview = service.get_attachment_preview({
            "scope": "message",
            "conv_id": "conversation-a",
            "message_index": 0,
            "block_index": 1,
            "preview_id": descriptor["preview_id"],
        })
        self.assertEqual(preview["text"], "private attachment payload")

        public = conversation_for_frontend(conversation)
        public_json = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("private attachment payload", public_json)
        self.assertNotIn("file_path", public_json)
        public_block = public["messages"][0]["content"][1]
        self.assertEqual(public_block["type"], "attachment")
        self.assertEqual(public_block["_attachment"]["preview_id"], descriptor["preview_id"])

    def test_image_preview_is_reencoded_and_bounded(self):
        descriptor = store_attachment_path(self._image_file())
        result = FileService(DummyApp()).get_attachment_preview({
            "scope": "pending",
            "preview_id": descriptor["preview_id"],
        })
        self.assertEqual(result["preview_type"], "image")
        header, encoded = result["data_url"].split(",", 1)
        self.assertIn(header, {"data:image/png;base64", "data:image/jpeg;base64"})
        payload = base64.b64decode(encoded, validate=True)
        self.assertLessEqual(len(payload), 2 * 1024 * 1024)
        with Image.open(io.BytesIO(payload)) as preview_image:
            self.assertLessEqual(max(preview_image.size), 1600)

    def test_text_and_pixel_limits_are_enforced(self):
        text_path = self.root / "long.txt"
        text_path.write_text("A" * 500, encoding="utf-8")
        text_descriptor = store_attachment_path(text_path)
        with patch("claude_chat.services.attachment_store.MAX_ATTACHMENT_PREVIEW_TEXT_BYTES", 32):
            result = FileService(DummyApp()).get_attachment_preview({
                "scope": "pending",
                "preview_id": text_descriptor["preview_id"],
            })
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["text"].encode("utf-8")), 32)

        image_descriptor = store_attachment_path(self._image_file(size=(20, 20)))
        with patch("claude_chat.services.attachment_store.MAX_ATTACHMENT_PREVIEW_IMAGE_PIXELS", 100):
            rejected = FileService(DummyApp()).get_attachment_preview({
                "scope": "pending",
                "preview_id": image_descriptor["preview_id"],
            })
        self.assertIn("error", rejected)
        self.assertIn("像素", rejected["error"])

    def test_legacy_base64_is_validated_but_legacy_paths_are_never_opened(self):
        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_buffer, format="PNG")
        legacy_image = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(image_buffer.getvalue()).decode("ascii"),
            },
        }
        conversation = {"messages": [{"role": "user", "content": [legacy_image]}]}
        preview = FileService(DummyApp(conversation)).get_attachment_preview({
            "scope": "message",
            "conv_id": "legacy",
            "message_index": 0,
            "block_index": 0,
        })
        self.assertEqual(preview["preview_type"], "image")

        secret = self.root / "legacy-secret.txt"
        secret.write_text("do-not-read", encoding="utf-8")
        path_block = {
            "type": "image",
            "source": {"file_path": str(secret), "media_type": "image/png"},
        }
        path_conversation = {"messages": [{"role": "user", "content": [path_block]}]}
        rejected = FileService(DummyApp(path_conversation)).get_attachment_preview({
            "scope": "message",
            "conv_id": "legacy",
            "message_index": 0,
            "block_index": 0,
        })
        self.assertIn("error", rejected)
        self.assertNotIn("do-not-read", json.dumps(rejected, ensure_ascii=False))

    def test_prepare_rejects_raw_paths(self):
        source = self.root / "forbidden.txt"
        source.write_text("forbidden", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不接受本地路径"):
            prepare_attachment_content({"path": str(source)}, "claude", MemoryConfig())

    def test_legacy_path_is_migrated_and_survives_original_removal(self):
        source = self._image_file(name="legacy-photo.png")
        conversation = {
            "id": "legacy-conversation",
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "image",
                    "source": {"file_path": str(source), "media_type": "image/png"},
                }],
            }],
        }
        manager = MemoryConversationManager(conversation)
        migrated = migrate_and_persist_legacy_attachments(manager, manager.load_conversation("legacy-conversation"))
        self.assertTrue(manager.saved)
        block = migrated["messages"][0]["content"][0]
        preview_id = block["_attachment"]["preview_id"]
        self.assertRegex(preview_id, r"^[0-9a-f]{32}$")
        self.assertNotEqual(block["source"]["file_path"], str(source))

        source.unlink()
        app = DummyApp(manager.conversation)
        result = FileService(app).get_attachment_preview({
            "scope": "message",
            "conv_id": "legacy-conversation",
            "message_index": 0,
            "block_index": 0,
            "preview_id": preview_id,
        })
        self.assertEqual(result["preview_type"], "image")
        public_json = json.dumps(conversation_for_frontend(manager.conversation), ensure_ascii=False)
        self.assertNotIn("file_path", public_json)
        self.assertNotIn(str(source), public_json)

    def test_discard_only_removes_unreferenced_pending_attachment(self):
        pending_source = self.root / "pending.txt"
        pending_source.write_text("pending", encoding="utf-8")
        pending = store_attachment_path(pending_source)
        service = FileService(DummyApp())
        discarded = service.discard_pending_attachment(pending["preview_id"])
        self.assertTrue(discarded["success"])
        self.assertTrue(discarded["deleted"])
        self.assertIn("error", service.get_attachment_preview({
            "scope": "pending",
            "preview_id": pending["preview_id"],
        }))

        referenced_source = self.root / "referenced.txt"
        referenced_source.write_text("referenced", encoding="utf-8")
        referenced = store_attachment_path(referenced_source)
        conversation = {
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "attachment",
                    "_attachment": {"preview_id": referenced["preview_id"]},
                }],
            }],
        }
        referenced_service = FileService(DummyApp(conversation))
        kept = referenced_service.discard_pending_attachment(referenced["preview_id"])
        self.assertFalse(kept["success"])
        self.assertEqual(kept["reason"], "referenced")
        preview = referenced_service.get_attachment_preview({
            "scope": "pending",
            "preview_id": referenced["preview_id"],
        })
        self.assertEqual(preview["preview_type"], "text")

    def test_http_preview_route_delegates_to_unified_api(self):
        class FakeApi:
            def __init__(self):
                self.request = None

            def get_attachment_preview(self, request):
                self.request = request
                return {"preview_type": "text", "text": "ok"}

            def discard_pending_attachment(self, preview_id):
                self.request = {"preview_id": preview_id}
                return {"success": True, "deleted": True}

        class FakeHandler:
            def __init__(self):
                self.server = SimpleNamespace(api=FakeApi())
                self.response = None

            def read_json_body(self):
                return {"scope": "pending", "preview_id": "a" * 32}

            def send_json_response(self, data, status=200):
                self.response = (status, data)

            def send_error(self, status, message=None):
                raise AssertionError((status, message))

        handler = FakeHandler()
        HttpApiRouter(handler).dispatch_post("/api/attachment_preview")
        self.assertEqual(handler.server.api.request["preview_id"], "a" * 32)
        self.assertEqual(handler.response, (200, {"preview_type": "text", "text": "ok"}))

        handler = FakeHandler()
        handler.read_json_body = lambda: {"preview_id": "b" * 32}
        HttpApiRouter(handler).dispatch_post("/api/discard_pending_attachment")
        self.assertEqual(handler.server.api.request["preview_id"], "b" * 32)
        self.assertEqual(handler.response, (200, {"success": True, "deleted": True}))


if __name__ == "__main__":
    unittest.main()
