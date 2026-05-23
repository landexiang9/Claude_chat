import json
from pathlib import Path
import platform
import base64
import logging

# Paths
BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
CONVERSATIONS_DIR = BASE_DIR / "conversations"

# Setup standard logging
LOG_PATH = BASE_DIR / "claude_chat.log"

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        # File Handler
        try:
            file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Failed to create file handler for logging: {e}")
        
        # Console Handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

setup_logging()
logger = logging.getLogger("claude_chat")


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


def xor_crypt(data: str) -> str:
    key = (platform.node() + "_ClaudeChatFallbackSalt").encode('utf-8')
    data_bytes = data.encode('utf-8')
    obfuscated = bytearray(len(data_bytes))
    for i in range(len(data_bytes)):
        obfuscated[i] = data_bytes[i] ^ key[i % len(key)]
    return base64.b64encode(obfuscated).decode('utf-8')


def xor_decrypt(obfuscated_b64: str) -> str:
    try:
        key = (platform.node() + "_ClaudeChatFallbackSalt").encode('utf-8')
        obfuscated_bytes = base64.b64decode(obfuscated_b64.encode('utf-8'))
        decrypted = bytearray(len(obfuscated_bytes))
        for i in range(len(obfuscated_bytes)):
            decrypted[i] = obfuscated_bytes[i] ^ key[i % len(key)]
        return decrypted.decode('utf-8')
    except Exception:
        return ""


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
            "system_prompts": [
                {"id": "default_helper", "name": "AI 助手", "content": "You are a helpful, respectful and honest assistant."},
                {"id": "translator", "name": "专业翻译官", "content": "你是一个专业的翻译官，请将我输入的所有内容翻译成地道的英文，如果本身就是英文则翻译成中文。无需解释。"},
                {"id": "programmer", "name": "高级程序员", "content": "你是一位拥有20年开发经验的资深软件架构师。请以严谨、结构化、注重性能与安全性的视角回答编程问题，并提供符合最佳实践的完整代码段。"}
            ],
            "selected_system_prompt_id": ""
        }
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                
                # Update settings except api_key
                self.data.update(loaded)
                
                storage = loaded.get("api_key_storage", "none")
                api_key_val = ""
                
                if storage == "keyring":
                    try:
                        import keyring
                        val = keyring.get_password("ClaudeChat", "api_key")
                        if val:
                            api_key_val = val
                        else:
                            # Fallback if keyring returned empty
                            obf = loaded.get("api_key_obfuscated", "")
                            if obf:
                                api_key_val = xor_decrypt(obf)
                    except Exception as e:
                        logger.warning(f"Failed to read from keyring: {e}")
                        obf = loaded.get("api_key_obfuscated", "")
                        if obf:
                            api_key_val = xor_decrypt(obf)
                elif storage == "xor":
                    obf = loaded.get("api_key_obfuscated", "")
                    if obf:
                        api_key_val = xor_decrypt(obf)
                elif loaded.get("api_key"):
                    # Migrate raw key from old config
                    api_key_val = loaded["api_key"]
                
                self.data["api_key"] = api_key_val
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"Error loading config: {e}")

    def save(self):
        to_save = dict(self.data)
        api_key_val = to_save.get("api_key", "").strip()
        
        # Clean api_key fields for saving
        to_save["api_key"] = ""
        to_save["api_key_storage"] = "none"
        to_save["api_key_obfuscated"] = ""
        
        if api_key_val:
            # Try keyring first
            try:
                import keyring
                keyring.set_password("ClaudeChat", "api_key", api_key_val)
                to_save["api_key_storage"] = "keyring"
                # Write an obfuscated copy as fallback backup in case keyring becomes inaccessible
                to_save["api_key_obfuscated"] = xor_crypt(api_key_val)
            except Exception as e:
                logger.warning(f"Keyring save failed, using XOR fallback: {e}")
                to_save["api_key_storage"] = "xor"
                to_save["api_key_obfuscated"] = xor_crypt(api_key_val)
                
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving config file: {e}")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

