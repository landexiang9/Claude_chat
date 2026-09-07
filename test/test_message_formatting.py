import tempfile
import threading
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_chat.db as db_module
from claude_chat.app import ClaudeChatApp
from claude_chat.services.conversation_service import messages_for_api


class MessageFormattingPersistenceTests(unittest.TestCase):
    def test_markdown_choice_round_trips_and_old_messages_default_to_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(db_module, "DB_PATH", root / "chat.db"),
                patch.object(db_module, "CONVERSATIONS_DIR", root / "conversations"),
                patch.object(db_module, "BACKUP_DIR", root / "conversations_backup"),
            ):
                manager = db_module.DatabaseManager()
                conversation = manager.new_conversation()
                conversation["messages"] = [
                    {"role": "user", "content": "**plain**", "render_markdown": False},
                    {"role": "user", "content": "**legacy default**"},
                ]
                manager.save_conversation(conversation)

                loaded = manager.load_conversation(conversation["id"])
                self.assertFalse(loaded["messages"][0]["render_markdown"])
                self.assertTrue(loaded["messages"][1]["render_markdown"])

                with manager.get_connection() as connection:
                    columns = {
                        row["name"] for row in connection.execute("PRAGMA table_info(messages)").fetchall()
                    }
                self.assertIn("render_markdown", columns)

                app = ClaudeChatApp.__new__(ClaudeChatApp)
                app.conv_manager = manager
                app.current_conv = loaded
                app.lock = threading.RLock()
                with self.assertLogs("claude_chat", level="ERROR") as captured_logs:
                    app._persist_stream_failure(conversation["id"], "temperature must be 1")
                self.assertIn("temperature must be 1", "\n".join(captured_logs.output))
                loaded = manager.load_conversation(conversation["id"])
                error_message = loaded["messages"][-1]
                self.assertEqual(error_message["role"], "assistant")
                self.assertTrue(error_message["content"][0]["_stream_error"])
                self.assertIn("temperature must be 1", error_message["content"][0]["text"])
                self.assertFalse(error_message["render_markdown"])
                api_messages = messages_for_api(loaded["messages"])
                self.assertEqual(len(api_messages), len(loaded["messages"]) - 1)
                self.assertNotIn("temperature must be 1", str(api_messages))


if __name__ == "__main__":
    unittest.main()
