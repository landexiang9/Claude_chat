"""An unavailable credential must not become a deleted credential on settings save."""

import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from claude_chat.config import ConfigManager


class CredentialPreservationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = root / "config.json"
        self.stack.enter_context(patch("claude_chat.config.CONFIG_PATH", self.path))
        self.stack.enter_context(patch("claude_chat.config.setup_logging"))
        self.stack.enter_context(patch("keyring.get_password", side_effect=RuntimeError("unavailable")))
        self.stack.enter_context(patch("keyring.set_password"))
        self.stack.enter_context(patch("claude_chat.config.aes_decrypt", return_value=""))
        self.stack.enter_context(patch("claude_chat.config.xor_decrypt", return_value=""))

    def load_unavailable(self, storage="keyring", backup="nonce:encrypted", name="api_key"):
        original = {
            "security_token": "isolated-test-token",
            "model": "old-model",
            name: "",
            name + "_storage": storage,
            name + "_obfuscated": backup,
            "custom_providers": [{"id": "example"}],
        }
        self.path.write_text(json.dumps(original), encoding="utf-8")
        return ConfigManager()

    def saved(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_failed_read_preserves_backups_on_all_ordinary_save_paths(self):
        for storage, backup in (
            ("keyring", "nonce:encrypted"),
            ("keyring", ""),
            ("aes", "nonce:encrypted"),
            ("xor", "encoded"),
        ):
            for name in ("api_key", "deepseek_api_key", "custom_example_api_key"):
                with self.subTest(storage=storage, name=name):
                    manager = self.load_unavailable(storage, backup, name)
                    self.assertEqual(manager.get(name), "")
                    self.assertTrue(manager.set("model", "new-model"))
                    self.assertTrue(manager.set_many({"deepseek_use_responses": True}))
                    self.assertTrue(manager.set_model_config("test", {"max_tokens": 100}))
                    self.assertTrue(manager.save())
                    saved = self.saved()
                    self.assertEqual(saved[name + "_storage"], storage)
                    self.assertEqual(saved[name + "_obfuscated"], backup)
                    self.assertEqual(saved[name], "")

    def test_explicit_disconnect_and_replacement_still_work(self):
        for method in ("set", "set_many"):
            with self.subTest(method=method):
                manager = self.load_unavailable()
                if method == "set":
                    self.assertTrue(manager.set("api_key", ""))
                else:
                    self.assertTrue(manager.set_many({"api_key": ""}))
                self.assertEqual(self.saved()["api_key_storage"], "none")
                self.assertEqual(self.saved()["api_key_obfuscated"], "")
        manager = self.load_unavailable()
        with patch.object(manager, "_encrypt_key_backup", return_value=("aes", "new:encrypted")):
            self.assertTrue(manager.set_many({"api_key": "fake-replacement"}))
        self.assertEqual(self.saved()["api_key_obfuscated"], "new:encrypted")
        self.assertNotIn("fake-replacement", self.path.read_text(encoding="utf-8"))

    def test_failed_write_restores_unresolved_backup_for_retry(self):
        manager = self.load_unavailable()
        with patch.object(manager, "save", return_value=False):
            self.assertFalse(manager.set_many({"api_key": ""}))
            self.assertFalse(manager.set("api_key", ""))
        self.assertTrue(manager.set("model", "retry"))
        self.assertEqual(self.saved()["api_key_obfuscated"], "nonce:encrypted")

    def test_restored_keyring_value_loads_normally(self):
        self.load_unavailable()
        with patch("keyring.get_password", return_value="fake-restored-key"):
            manager = ConfigManager()
        self.assertEqual(manager.get("api_key"), "fake-restored-key")
        self.assertFalse(manager._unresolved_api_keys)


if __name__ == "__main__":
    unittest.main()
