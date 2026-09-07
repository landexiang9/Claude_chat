import json
from pathlib import Path
import platform
import base64
import logging

# ==========================================
# 路径与环境配置
# ==========================================
import sys
if getattr(sys, 'frozen', False):
    # 如果是经 PyInstaller 打包后的可执行文件环境
    # 将基础路径设为 EXE 所在目录，保证配置文件和数据库在外部持久化存储，不随临时文件夹删除而丢失
    BASE_DIR = Path(sys.executable).parent
else:
    # 正常的 Python 脚本开发调试环境，基础路径为项目根目录
    BASE_DIR = Path(__file__).parent.parent

# 配置文件与对话目录路径
CONFIG_PATH = BASE_DIR / "config.json"
CONVERSATIONS_DIR = BASE_DIR / "conversations"
ATTACHMENT_STORE_DIR = BASE_DIR / "attachments"

# 日志文件路径
LOG_PATH = BASE_DIR / "claude_chat.log"

# 内置聊天模型的默认输出上限。4096 对开启高强度推理的模型过小，
# 容易在生成正文前就耗尽；16384 在可用性、延迟和费用之间更均衡。
DEFAULT_MAX_TOKENS = 16384

def setup_logging():
    """
    配置全局日志记录器，同时输出到本地日志文件（UTF-8）和终端控制台
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        # 文件日志处理器 (写入本地 claude_chat.log)
        try:
            file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            file_handler.setLevel(logging.INFO)
            file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"创建文件日志处理器失败: {e}")
        
        # 控制台日志处理器 (输出到命令行终端)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

# 初始化日志设置
# setup_logging()  # Moved to ConfigManager.__init__
logger = logging.getLogger("claude_chat")


def sanitize_provider_id(raw_id):
    """将任意字符串规整为只含 a-z0-9_ 的安全标识符，用作配置键后缀。"""
    import re
    cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', str(raw_id)).lower().strip('_')
    return cleaned or "provider"


def get_sensitive_api_keys(config_data):
    """
    返回需要加密保存的全部 API Key 字段名列表。
    包含 3 个内置平台的固定 Key，以及所有自定义提供商动态生成的 custom_<id>_api_key。
    """
    base_keys = [
        "api_key", "tavily_api_key", "jina_api_key",
        "deepseek_api_key", "gemini_api_key",
        "deepseek_tavily_api_key", "deepseek_jina_api_key"
    ]
    for p in (config_data.get("custom_providers") or []):
        pid = p.get("id")
        if pid:
            base_keys.append(f"custom_{sanitize_provider_id(pid)}_api_key")
    return base_keys


def is_custom_platform(active_platform):
    """判断平台标识是否指向自定义提供商（形如 'custom:<id>'）。"""
    return isinstance(active_platform, str) and active_platform.startswith("custom:")


def custom_platform_id(active_platform):
    """从 'custom:<id>' 平台标识中提取并规整 provider id。"""
    if is_custom_platform(active_platform):
        return sanitize_provider_id(active_platform.split(":", 1)[1])
    return None


def find_custom_provider(config_data, active_platform):
    """在 config_data 的 custom_providers 列表中按平台标识查找对应 provider，找不到返回 None。"""
    pid = custom_platform_id(active_platform)
    if not pid:
        return None
    for p in (config_data.get("custom_providers") or []):
        if sanitize_provider_id(p.get("id", "")) == pid:
            return p
    return None




# ==========================================
# 默认/备用模型列表 (如果从 API 获取在线模型失败时使用)
# ==========================================
FALLBACK_MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-8",
]

FALLBACK_MODELS_DEEPSEEK = [
    "deepseek-chat",
    "deepseek-reasoner",
]

FALLBACK_MODELS_GEMINI = [
    "gemini-2.0-flash",
    "gemini-2.0-pro-exp",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# 支持上传/解析的文件扩展名分类
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
OFFICE_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".md", ".json",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".c", ".cpp",
    ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sh", ".bat",
    ".ps1", ".sql", ".r", ".swift", ".kt", ".scala", ".lua", ".csv"
}
SUPPORTED_ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS | OFFICE_EXTENSIONS | TEXT_EXTENSIONS
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
MAX_ATTACHMENT_PREVIEW_TEXT_BYTES = 256 * 1024
MAX_ATTACHMENT_PREVIEW_IMAGE_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENT_PREVIEW_IMAGE_PIXELS = 40_000_000
MAX_ATTACHMENT_PREVIEW_IMAGE_EDGE = 1600


# ==========================================
# 加密混淆工具函数 (用于安全保存 API Key 备用副本)
# ==========================================
_MACHINE_KEY_CACHE = None

def _get_machine_key() -> bytes:
    global _MACHINE_KEY_CACHE
    if _MACHINE_KEY_CACHE is not None:
        return _MACHINE_KEY_CACHE
        
    import uuid
    import platform
    import os
    import getpass
    import hashlib
    
    try:
        user = os.getlogin()
    except Exception:
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown_user"
            
    node = platform.node() or "unknown_node"
    machine = platform.machine() or "unknown_machine"
    
    fingerprint = f"{node}_{machine}_{user}".encode('utf-8')
    salt = b"ClaudeChatSecretSalt_GCM_2026"
    _MACHINE_KEY_CACHE = hashlib.pbkdf2_hmac('sha256', fingerprint, salt, 100000)
    return _MACHINE_KEY_CACHE


def aes_encrypt(data: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import os
        aesgcm = AESGCM(_get_machine_key())
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, data.encode('utf-8'), None)
        return f"{base64.b64encode(nonce).decode('utf-8')}:{base64.b64encode(ciphertext).decode('utf-8')}"
    except Exception as e:
        logger.error(f"AES-GCM 加密失败: {e}")
        return ""


def aes_decrypt(encrypted_str: str) -> str:
    if not encrypted_str or ":" not in encrypted_str:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce_b64, ciphertext_b64 = encrypted_str.split(":", 1)
        nonce = base64.b64decode(nonce_b64.encode('utf-8'))
        ciphertext = base64.b64decode(ciphertext_b64.encode('utf-8'))
        aesgcm = AESGCM(_get_machine_key())
        return aesgcm.decrypt(nonce, ciphertext, None).decode('utf-8')
    except Exception as e:
        logger.error(f"AES-GCM 解密失败: {e}")
        return ""


def xor_crypt(data: str) -> str:
    """
    使用基于主机名 (platform.node) 的动态盐进行简单的 XOR 混淆加密，并输出 Base64 编码字符串
    """
    key = (platform.node() + "_ClaudeChatFallbackSalt").encode('utf-8')
    data_bytes = data.encode('utf-8')
    obfuscated = bytearray(len(data_bytes))
    for i in range(len(data_bytes)):
        obfuscated[i] = data_bytes[i] ^ key[i % len(key)]
    return base64.b64encode(obfuscated).decode('utf-8')


def xor_decrypt(obfuscated_b64: str) -> str:
    """
    对 Base64 编码的 XOR 混淆字符串进行解密，恢复出明文数据。如果失败则返回空字符串。
    """
    try:
        key = (platform.node() + "_ClaudeChatFallbackSalt").encode('utf-8')
        obfuscated_bytes = base64.b64decode(obfuscated_b64.encode('utf-8'))
        decrypted = bytearray(len(obfuscated_bytes))
        for i in range(len(obfuscated_bytes)):
            decrypted[i] = obfuscated_bytes[i] ^ key[i % len(key)]
        return decrypted.decode('utf-8')
    except Exception:
        return ""



# ==========================================
# 系统配置管理器
# ==========================================
class ConfigManager:
    """
    配置管理器，负责加载、保存和更新本地配置 config.json，
    并支持通过系统 Keyring 安全地加密保存 API 密钥，在 Linux/无图形环境下支持平滑退化为本地 XOR 加密。
    """
    def __init__(self):
        import threading
        self._lock = threading.RLock()
        setup_logging()
        # 默认系统配置数据结构


        self.data = {
            "active_platform": "claude",
            "api_key": "",
            "deepseek_api_key": "",
            "gemini_api_key": "",
            "deepseek_api_url": "https://api.deepseek.com",
            "gemini_api_url": "",
            # Provider-native Files APIs are the default attachment transport.
            "claude_file_upload_enabled": True,
            "claude_file_upload_expires_in_seconds": 172800,
            "deepseek_file_upload_enabled": True,
            "deepseek_file_upload_expires_in_seconds": 172800,
            "gemini_file_upload_enabled": True,
            "ocr_mode": "auto",
            "ocr_cloud_model": "gemini",
            "model": "claude-sonnet-4-6",
            # Claude settings (legacy flat keys)
            "temperature": 0.7,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "thinking_enabled": False,
            "thinking_type": "adaptive",
            "thinking_budget": 16000,
            "thinking_level": "high",
            "enable_web_search": False,
            "enable_web_fetch": True,
            "web_fetch_limit": 15000,
            "web_search_engine": "google",
            "tavily_api_key": "",
            "jina_api_key": "",
            "web_page_parser": "local",
            "enable_code_sandbox": False,
            "auto_run_code": False,
            "code_sandbox_timeout": 30,
            # DeepSeek settings
            "deepseek_temperature": 0.7,
            "deepseek_max_tokens": DEFAULT_MAX_TOKENS,
            "deepseek_enable_web_search": False,
            "deepseek_enable_web_fetch": True,
            "deepseek_web_fetch_limit": 15000,
            "deepseek_web_search_engine": "google",
            "deepseek_tavily_api_key": "",
            "deepseek_jina_api_key": "",
            "deepseek_web_page_parser": "local",
            # Gemini settings
            "gemini_temperature": 0.7,
            "gemini_max_tokens": DEFAULT_MAX_TOKENS,
            "gemini_thinking_enabled": False,
            "gemini_thinking_budget": 1024,
            "gemini_thinking_level": "high",
            "gemini_enable_web_search": False,
            "gemini_enable_code_sandbox": False,
            "gemini_code_sandbox_type": "local",
            # Global settings
            "font_mode": "custom",
            "enable_server": True,
            "server_port": 8000,
            "enable_ssl": False,
            "sync_config_to_web": True,
            "only_server": False,
            "use_cdn_assets": False,
            "proxy_mode": "system",
            "proxy_url": "",
            "security_token": "",
            "system_prompts": [
                {"id": "default_helper", "name": "AI 助手", "content": "You are a helpful, respectful and honest assistant."},
                {"id": "translator", "name": "专业翻译官", "content": "你是一个专业的翻译官，请将我输入的所有内容翻译成地道的英文，如果本身就是英文则翻译成中文。无需解释。"},
                {"id": "programmer", "name": "高级程序员", "content": "你是一位拥有20年开发经验的资深软件架构师。请以严谨、结构化、注重性能与安全性的视角回答编程问题，并提供符合最佳实践的完整代码段。"}
            ],
            "selected_system_prompt_id": "",
            "custom_providers": [],
            "model_configs": {}
        }
        # 保存默认值的深拷贝，供 load() 类型校验时恢复使用
        import copy
        self._defaults = copy.deepcopy(self.data)
        self.load()
        
        # 确保存在一个安全验证 Token 用于跨端与网络鉴权
        if not self.data.get("security_token"):
            import secrets
            self.data["security_token"] = secrets.token_hex(16)
            self.save()

    # M5: 已知配置键的类型规范，用于 load() 时校验和修正 config.json 中的错误类型
    _TYPE_SPEC = {
        "temperature": float,
        "max_tokens": int,
        "thinking_budget": int,
        "deepseek_temperature": float,
        "deepseek_max_tokens": int,
        "gemini_temperature": float,
        "gemini_max_tokens": int,
        "gemini_thinking_budget": int,
        "claude_file_upload_expires_in_seconds": int,
        "deepseek_file_upload_expires_in_seconds": int,
        "server_port": int,
        "web_fetch_limit": int,
        "deepseek_web_fetch_limit": int,
        "code_sandbox_timeout": int,
    }
    _BOOL_KEYS = {
        "thinking_enabled", "enable_web_search", "enable_web_fetch", "enable_code_sandbox",
        "auto_run_code", "deepseek_enable_web_search", "deepseek_enable_web_fetch",
        "gemini_thinking_enabled", "gemini_enable_web_search",
        "gemini_enable_code_sandbox", "enable_server", "enable_ssl",
        "claude_file_upload_enabled", "deepseek_file_upload_enabled", "gemini_file_upload_enabled",
        "sync_config_to_web", "only_server", "use_cdn_assets",
    }

    def _validate_config_types(self):
        """
        校验 self.data 中已知键的类型，将 None 或错误类型修正为正确类型。
        防止 config.json 中的 "temperature": null 或 "max_tokens": "4096" 等问题
        导致后续 int()/float()/bool() 转换崩溃或逻辑反转。
        """
        for k, typ in self._TYPE_SPEC.items():
            v = self.data.get(k)
            if v is None:
                self.data[k] = self._defaults.get(k)
            elif not isinstance(v, typ):
                try:
                    self.data[k] = typ(v)
                except (ValueError, TypeError):
                    self.data[k] = self._defaults.get(k)
        for k in self._BOOL_KEYS:
            v = self.data.get(k)
            if isinstance(v, str):
                self.data[k] = v.strip().lower() in ("true", "1", "yes")
            elif v is None:
                self.data[k] = self._defaults.get(k, False)
            elif not isinstance(v, bool):
                self.data[k] = bool(v)
        self.data["code_sandbox_timeout"] = max(
            1,
            min(600, self.data.get("code_sandbox_timeout", 30)),
        )

    def load(self):
        """
        从本地 config.json 加载保存的系统配置，并自动从 Keyring 或混淆备份中读取所有 API Key
        """
        with self._lock:
            if CONFIG_PATH.exists():
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    
                    # 用读取到的字段覆盖更新默认配置字典
                    self.data.update(loaded)
                    
                    # M5: 类型校验 — 防止 config.json 中的错误类型(None/字符串)导致
                    # 后续 int()/float()/bool() 转换崩溃或逻辑反转(如 bool("false")=True)
                    self._validate_config_types()
                    
                    # 定义所有需要安全加密保存的 API Key 列表（含自定义提供商动态 Key）
                    api_keys = get_sensitive_api_keys(loaded)
                    
                    for key in api_keys:
                        storage = loaded.get(f"{key}_storage", "none")
                        key_val = ""
                        
                        obf = loaded.get(f"{key}_obfuscated", "")
                        if storage == "keyring":
                            # 尝试从系统保险箱读取
                            try:
                                import keyring
                                val = keyring.get_password("ClaudeChat", key)
                                if val:
                                    key_val = val
                                elif obf:
                                    key_val = aes_decrypt(obf) if ":" in obf else xor_decrypt(obf)
                            except Exception as e:
                                logger.warning(f"从 keyring 中读取 {key} 失败，降级采用本地加密副本: {e}")
                                if obf:
                                    key_val = aes_decrypt(obf) if ":" in obf else xor_decrypt(obf)
                        elif storage in ("aes", "xor"):
                            # 使用本地加密文件存储
                            if obf:
                                key_val = aes_decrypt(obf) if ":" in obf else xor_decrypt(obf)
    
                        elif loaded.get(key):
                            # 兼容老版本直接明文保存的字段迁移
                            key_val = loaded[key]
                            
                        self.data[key] = key_val
                        
                except (json.JSONDecodeError, Exception) as e:
                    logger.error(f"加载配置文件出错: {e}")


    def _encrypt_key_backup(self, key_val):
        """
        生成 API Key 的加密备份。实现三级递降策略：AES → XOR → none。
        返回 (storage_type, obfuscated_str)。
        """
        encrypted = aes_encrypt(key_val)
        if encrypted:
            return ("aes", encrypted)
        try:
            obf = xor_crypt(key_val)
            if obf:
                return ("xor", obf)
        except Exception as e:
            logger.error(f"XOR 混淆备份失败: {e}")
        logger.error("AES 与 XOR 加密均失败，Key 备份无法生成")
        return ("none", "")

    def save(self):
        """
        保存当前配置到本地 file。出于安全考虑，敏感 API Key 不会以明文写入磁盘文件。
        存储层级：keyring → AES-GCM-256 → XOR，无论 keyring 是否成功都始终保留加密备份，
        确保 keyring 后续不可用时仍可从备份恢复。
        """
        with self._lock:
            to_save = dict(self.data)

            # 定义所有需要安全加密保存的 API Key 列表（含自定义提供商动态 Key）
            api_keys = get_sensitive_api_keys(to_save)

            for key in api_keys:
                # M1 防御：config.get 可能返回 None，strip() 前做类型检查
                raw = to_save.get(key)
                key_val = raw.strip() if isinstance(raw, str) else ""

                # 清除要写入磁盘的明文字段
                to_save[key] = ""
                to_save[f"{key}_storage"] = "none"
                to_save[f"{key}_obfuscated"] = ""

                if not key_val:
                    continue

                # 第 1 级：尝试 keyring（不依赖实例级永久禁用标志，每次都尝试）
                keyring_ok = False
                try:
                    import keyring
                    keyring.set_password("ClaudeChat", key, key_val)
                    keyring_ok = True
                except Exception as e:
                    logger.warning(f"保存 {key} 至 keyring 失败，降级本地加密: {e}")

                # 第 2 级：无论 keyring 成败，始终生成加密备份（H1 核心修复）
                backup_type, backup_obf = self._encrypt_key_backup(key_val)

                if keyring_ok:
                    to_save[f"{key}_storage"] = "keyring"
                else:
                    to_save[f"{key}_storage"] = backup_type
                to_save[f"{key}_obfuscated"] = backup_obf

            try:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(to_save, f, indent=2, ensure_ascii=False)
                return True
            except Exception as e:
                logger.error(f"写入配置文件失败: {e}")
                return False

    def get(self, key, default=None):
        """
        获取配置参数值，如果键不存在则返回 default 默认值
        """
        with self._lock:
            return self.data.get(key, default)


    def set(self, key, value):
        """
        更新指定的配置项并立即持久化写入本地配置文件中
        """
        with self._lock:
            missing = object()
            previous = self.data.get(key, missing)
            self.data[key] = value
            if self.save():
                return True
            if previous is missing:
                self.data.pop(key, None)
            else:
                self.data[key] = previous
            return False

    def set_many(self, updates):
        """
        批量更新多个配置项并一次性持久化写入。
        用于 save_config 等需要原子性批量写入的场景，避免循环调用 set() 导致重复写盘。
        """
        with self._lock:
            missing = object()
            previous = {k: self.data.get(k, missing) for k in updates}
            for k, v in updates.items():
                self.data[k] = v
            if self.save():
                return True
            for key, value in previous.items():
                if value is missing:
                    self.data.pop(key, None)
                else:
                    self.data[key] = value
            return False

    def get_model_config(self, model_id):
        """
        获取指定 model_id 的专属配置字典。
        """
        with self._lock:
            model_configs = self.data.get("model_configs", {})
            return model_configs.get(model_id, {})

    def set_model_config(self, model_id, config_dict):
        """
        更新指定 model_id 的专属配置，并持久化。
        """
        with self._lock:
            import copy
            previous = copy.deepcopy(self.data.get("model_configs"))
            if "model_configs" not in self.data:
                self.data["model_configs"] = {}
            if model_id not in self.data["model_configs"]:
                self.data["model_configs"][model_id] = {}
            self.data["model_configs"][model_id].update(config_dict)
            if self.save():
                return True
            if previous is None:
                self.data.pop("model_configs", None)
            else:
                self.data["model_configs"] = previous
            return False
