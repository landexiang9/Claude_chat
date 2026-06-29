import os
import sys
import tempfile
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.app import ClaudeChatApp
from claude_chat.api_bridge import WebAPI
from claude_chat.db import DatabaseManager
import claude_chat.db

db_fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(db_fd)

class DummyWindow:
    def evaluate_js(self, js):
        pass

# Dynamically patch the DB_PATH to use our temp file
claude_chat.db.DB_PATH = db_path

app = ClaudeChatApp()
app.conv_manager = DatabaseManager()
app.window = DummyWindow()

app_api = WebAPI(app)

app.config.set("model", "claude-3-7-sonnet-latest")
conv = app.conv_manager.new_conversation()
conv["model"] = "claude-3-7-sonnet-latest"
app.conv_manager.save_conversation(conv)
app.current_conv = conv

print("Before save_config model in DB:", app.conv_manager.load_conversation(conv["id"])["model"])

# Call save_config (mimicking the UI sending new model)
app_api.save_config({"model": "gemini-3.1-pro-preview"})

print("After save_config model in DB:", app.conv_manager.load_conversation(conv["id"])["model"])
print("After save_config current_conv:", app.current_conv["model"])

os.remove(db_path)

