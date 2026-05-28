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

# 日志文件路径
LOG_PATH = BASE_DIR / "claude_chat.log"

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
setup_logging()
logger = logging.getLogger("claude_chat")


# ==========================================
# 默认/备用模型列表 (如果从 API 获取在线模型失败时使用)
# ==========================================
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

# 支持上传/解析的文件扩展名分类
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {
    ".txt", ".py", ".js", ".ts", ".html", ".css", ".md", ".json",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".c", ".cpp",
    ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sh", ".bat",
    ".ps1", ".sql", ".r", ".swift", ".kt", ".scala", ".lua", ".csv"
}


# ==========================================
# 加密混淆工具函数 (用于安全保存 API Key 备用副本)
# ==========================================
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
        # 默认系统配置数据结构
        self.data = {
            "api_key": "",
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.7,
            "max_tokens": 4096,
            "thinking_enabled": False,
            "thinking_type": "adaptive",
            "thinking_budget": 16000,
            "thinking_level": "high",
            "auto_run_code": False,
            "font_mode": "custom",
            "enable_server": True,
            "server_port": 8000,
            "sync_config_to_web": True,
            "only_server": False,
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
        """
        从本地 config.json 加载保存的系统配置，并自动从 Keyring 或混淆备份中读取 API Key
        """
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                
                # 用读取到的字段覆盖更新默认配置字典（排除 api_key 字段防覆盖）
                self.data.update(loaded)
                
                storage = loaded.get("api_key_storage", "none")
                api_key_val = ""
                
                if storage == "keyring":
                    # 尝试从系统保险箱读取
                    try:
                        import keyring
                        val = keyring.get_password("ClaudeChat", "api_key")
                        if val:
                            api_key_val = val
                        else:
                            # 如果系统保险箱读取为空，降级采用 XOR 混淆副本
                            obf = loaded.get("api_key_obfuscated", "")
                            if obf:
                                api_key_val = xor_decrypt(obf)
                    except Exception as e:
                        logger.warning(f"从 keyring 中读取密码失败，降级采用 XOR 副本: {e}")
                        obf = loaded.get("api_key_obfuscated", "")
                        if obf:
                            api_key_val = xor_decrypt(obf)
                elif storage == "xor":
                    # 使用 XOR 加密文件存储
                    obf = loaded.get("api_key_obfuscated", "")
                    if obf:
                        api_key_val = xor_decrypt(obf)
                elif loaded.get("api_key"):
                    # 兼容老版本明文 key 字段迁移
                    api_key_val = loaded["api_key"]
                
                self.data["api_key"] = api_key_val
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"加载配置文件出错: {e}")

    def save(self):
        """
        保存当前配置到本地文件。出于安全考虑，api_key 不会以明文写入磁盘文件。
        会优先存入 keyring 并保存一份基于机器指纹的 XOR 混淆混淆副本到配置文件。
        在 Linux 命令行或 Android Termux 下因无桌面而调用 Keyring 报错时，会自动降级为 XOR 方式。
        """
        to_save = dict(self.data)
        api_key_val = to_save.get("api_key", "").strip()
        
        # 清除要写入磁盘明文字段中的敏感信息
        to_save["api_key"] = ""
        to_save["api_key_storage"] = "none"
        to_save["api_key_obfuscated"] = ""
        
        if api_key_val:
            # 优先尝试存入系统钥匙串 (Windows Credential Manager / macOS Keychain)
            try:
                import keyring
                keyring.set_password("ClaudeChat", "api_key", api_key_val)
                to_save["api_key_storage"] = "keyring"
                # 同时写入一个 XOR 混淆混淆副本作为备份，以便以后转移或在 keyring 不可用时降级恢复
                to_save["api_key_obfuscated"] = xor_crypt(api_key_val)
            except Exception as e:
                logger.warning(f"保存至 keyring 失败，将自动降级为本地混淆存储 (XOR): {e}")
                to_save["api_key_storage"] = "xor"
                to_save["api_key_obfuscated"] = xor_crypt(api_key_val)
                
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"写入配置文件失败: {e}")

    def get(self, key, default=None):
        """
        获取配置参数值，如果键不存在则返回 default 默认值
        """
        return self.data.get(key, default)

    def set(self, key, value):
        """
        更新指定的配置项并立即持久化写入本地配置文件中
        """
        self.data[key] = value
        self.save()
