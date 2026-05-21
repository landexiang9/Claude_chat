import json
import uuid
from datetime import datetime
from pathlib import Path
from claude_chat.config import CONVERSATIONS_DIR


def read_text_file(file_path):
    """
    Reads a text file trying UTF-8 first, then GBK, and falls back to UTF-8 with character replacement.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="gbk") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()


class ConversationManager:
    def __init__(self):
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._conversations = []
        self._cache = {}  # Cache structure: { file_path: { "mtime": float, "metadata": dict } }

    @property
    def conversations(self):
        return self._conversations

    def refresh(self):
        self._conversations = []
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            file_path = str(f)
            try:
                stat = f.stat()
                mtime = stat.st_mtime
            except Exception:
                continue

            # Use cache if file has not been modified
            if file_path in self._cache and self._cache[file_path]["mtime"] == mtime:
                self._conversations.append(self._cache[file_path]["metadata"])
                continue

            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, Exception):
                continue

            metadata = {
                "id": data.get("id", f.stem),
                "title": data.get("title", "未命名对话"),
                "model": data.get("model", ""),
                "updated_at": data.get("updated_at", ""),
                "messages": data.get("messages", []),
                "input_tokens": data.get("input_tokens"),
                "output_tokens": data.get("output_tokens"),
            }
            
            # Store in cache
            self._cache[file_path] = {
                "mtime": mtime,
                "metadata": metadata
            }
            self._conversations.append(metadata)
        return self._conversations

    def load_conversation(self, conv_id):
        path = CONVERSATIONS_DIR / f"{conv_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save_conversation(self, conv_data):
        conv_data["updated_at"] = datetime.now().isoformat()
        path = CONVERSATIONS_DIR / f"{conv_data['id']}.json"
        
        # Invalidate cache for this file before writing
        self._cache.pop(str(path), None)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conv_data, f, indent=2, ensure_ascii=False)

    def delete_conversation(self, conv_id):
        path = CONVERSATIONS_DIR / f"{conv_id}.json"
        self._cache.pop(str(path), None)
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        self.refresh()

    def new_conversation(self):
        conv_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        data = {
            "id": conv_id,
            "title": "新对话",
            "created_at": now,
            "updated_at": now,
            "model": "",
            "temperature": 0.7,
            "max_tokens": 4096,
            "thinking": None,
            "messages": [],
        }
        self.save_conversation(data)
        self.refresh()
        return data

    def auto_clean(self, keep=50):
        files = sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime)
        if len(files) > keep:
            for f in files[:-keep]:
                self._cache.pop(str(f), None)
                try:
                    f.unlink()
                except Exception:
                    pass
