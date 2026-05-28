import json
import uuid
from datetime import datetime
from pathlib import Path
from claude_chat.config import CONVERSATIONS_DIR


def read_text_file(file_path):
    """
    尝试以不同的编码格式读取文本文件。
    优先采用 UTF-8 编码，失败时尝试 GBK，最后使用 UTF-8（对无法解码的字符进行替换）。
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
    """
    【已弃用/仅作备份】历史对话 JSON 文件管理器。
    当前系统已升级为 SQLite 数据库存储架构（由 db.py 驱动），
    该类和相关 JSON 读取功能保留作为遗留系统支持和兼容性参考。
    """
    def __init__(self):
        # 确保遗留对话目录存在
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._conversations = []
        # 文件修改时间缓存，防止重复读取未改动的 JSON 文件以提升性能
        # 缓存结构: { 文件路径: { "mtime": 修改时间戳, "metadata": 元数据字典 } }
        self._cache = {}

    @property
    def conversations(self):
        return self._conversations

    def refresh(self):
        """
        扫描 conversations 目录下的所有 json 文件，并根据最后修改时间倒序排列刷新缓存列表。
        """
        self._conversations = []
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            file_path = str(f)
            try:
                stat = f.stat()
                mtime = stat.st_mtime
            except Exception:
                continue

            # 如果文件未被修改，直接使用内存缓存
            if file_path in self._cache and self._cache[file_path]["mtime"] == mtime:
                self._conversations.append(self._cache[file_path]["metadata"])
                continue

            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, Exception):
                continue

            # 封装基础元数据以显示在侧边栏
            metadata = {
                "id": data.get("id", f.stem),
                "title": data.get("title", "未命名对话"),
                "model": data.get("model", ""),
                "updated_at": data.get("updated_at", ""),
                "messages": data.get("messages", []),
                "input_tokens": data.get("input_tokens"),
                "output_tokens": data.get("output_tokens"),
            }
            
            # 记录在缓存中
            self._cache[file_path] = {
                "mtime": mtime,
                "metadata": metadata
            }
            self._conversations.append(metadata)
        return self._conversations

    def load_conversation(self, conv_id):
        """
        加载指定 ID 的对话详情内容
        """
        path = CONVERSATIONS_DIR / f"{conv_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save_conversation(self, conv_data):
        """
        保存单个对话详细数据，并让对应路径的缓存失效
        """
        conv_data["updated_at"] = datetime.now().isoformat()
        path = CONVERSATIONS_DIR / f"{conv_data['id']}.json"
        
        # 写入前使缓存失效
        self._cache.pop(str(path), None)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conv_data, f, indent=2, ensure_ascii=False)

    def delete_conversation(self, conv_id):
        """
        删除指定的对话 JSON 文件
        """
        path = CONVERSATIONS_DIR / f"{conv_id}.json"
        self._cache.pop(str(path), None)
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
        self.refresh()

    def new_conversation(self):
        """
        初始化一个新对话字典，保存并返回
        """
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
        """
        自动清理对话，只保留最近更新的 `keep` 个对话以释放磁盘空间
        """
        files = sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime)
        if len(files) > keep:
            for f in files[:-keep]:
                self._cache.pop(str(f), None)
                try:
                    f.unlink()
                except Exception:
                    pass
