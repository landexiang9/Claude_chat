import json
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
CONVERSATIONS_DIR = BASE_DIR / "conversations"

# Fallback Models if API listing fails
FALLBACK_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]

# Supported file extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".md", ".json",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".c", ".cpp",
    ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sh", ".bat",
    ".ps1", ".sql", ".r", ".swift", ".kt", ".scala", ".lua", ".csv"
}


class ConfigManager:
    def __init__(self):
        self.data = {
            "api_key": "",
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.7,
            "max_tokens": 4096,
            "thinking_enabled": False,
            "thinking_type": "adaptive",
            "thinking_budget": 16000,
            "proxy_mode": "system",
            "proxy_url": "",
        }
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.data.update(loaded)
            except (json.JSONDecodeError, Exception):
                pass

    def save(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()
