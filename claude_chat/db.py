import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger("claude_chat")

# ==========================================
# 路径与环境配置
# ==========================================
import sys
if getattr(sys, 'frozen', False):
    # 如果是打包环境，基准路径设为可执行文件所在目录，防止临时文件夹释放导致数据库丢失
    BASE_DIR = Path(sys.executable).parent
else:
    # 正常 Python 运行环境
    BASE_DIR = Path(__file__).parent.parent

DB_PATH = BASE_DIR / "claude_chat.db"
CONVERSATIONS_DIR = BASE_DIR / "conversations"
BACKUP_DIR = BASE_DIR / "conversations_backup"


def serialize_content(content):
    """
    序列化消息内容：如果是复杂结构（如含附件的列表或字典），将其转换为 JSON 字符串保存到 SQLite 中
    """
    if isinstance(content, (list, dict)):
        return json.dumps(content, ensure_ascii=False)
    return content


def deserialize_content(content_str):
    """
    反序列化消息内容：如果是一个 JSON 字符串列表或字典，将其解析回 Python 对象
    """
    if not content_str:
        return ""
    content_str_stripped = content_str.strip()
    if content_str_stripped.startswith('[') or content_str_stripped.startswith('{'):
        try:
            return json.loads(content_str)
        except Exception:
            pass
    return content_str


class DatabaseManager:
    """
    SQLite 数据库管理器，负责应用程序对话和消息在本地数据库的读写、结构初始化及历史 JSON 数据迁移
    """
    def __init__(self):
        self.init_db()
        self.migrate_json_files()

    def get_connection(self):
        """
        获取一个 SQLite 数据库连接，并设置 row_factory 为 sqlite3.Row 以便通过字段名访问数据
        """
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """
        初始化 SQLite 数据库表结构，创建 conversations (对话表) 和 messages (消息表)
        """
        logger.info("正在初始化 SQLite 数据库表结构...")
        with self.get_connection() as conn:
            # 创建对话主表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    model TEXT,
                    temperature REAL,
                    max_tokens INTEGER,
                    thinking TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER
                )
            """)
            # 创建消息表，并建立对外键约束
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT,
                    role TEXT,
                    content TEXT,
                    thinking TEXT,
                    aborted INTEGER DEFAULT 0,
                    created_at TEXT,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
            """)
            conn.commit()

    def migrate_json_files(self):
        """
        [自动迁移机制] 扫描历史保存路径 conversations/*.json，将其内容转换为 SQLite 记录并备份归档原 JSON 文件
        """
        if not CONVERSATIONS_DIR.exists():
            return
        
        json_files = list(CONVERSATIONS_DIR.glob("*.json"))
        if not json_files:
            return

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"检测到有遗留的对话 JSON 文件共计 {len(json_files)} 个，正在开始迁移至数据库...")
        
        for f in json_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                
                conv_id = data.get("id", f.stem)
                title = data.get("title", "未命名对话")
                model = data.get("model", "")
                temperature = data.get("temperature", 0.7)
                max_tokens = data.get("max_tokens", 4096)
                thinking = json.dumps(data.get("thinking")) if data.get("thinking") is not None else None
                created_at = data.get("created_at", datetime.now().isoformat())
                updated_at = data.get("updated_at", datetime.now().isoformat())
                input_tokens = data.get("input_tokens", 0)
                output_tokens = data.get("output_tokens", 0)
                
                with self.get_connection() as conn:
                    # 插入或替换对话记录
                    conn.execute("""
                        INSERT OR REPLACE INTO conversations 
                        (id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (conv_id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens))
                    
                    # 删除该对话已有的旧消息数据，防止重复迁移导致冗余
                    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
                    
                    # 遍历并写入对话内的所有消息
                    messages = data.get("messages", [])
                    for msg in messages:
                        m_role = msg.get("role", "user")
                        m_content = serialize_content(msg.get("content", ""))
                        m_thinking = msg.get("thinking", None)
                        m_aborted = 1 if msg.get("aborted", False) else 0
                        m_created = updated_at # 默认采用对话更新时间排序
                        
                        conn.execute("""
                            INSERT INTO messages (conversation_id, role, content, thinking, aborted, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (conv_id, m_role, m_content, m_thinking, m_aborted, m_created))
                    conn.commit()
                
                # 迁移完毕后，将原文件重命名移入备份归档文件夹，避免文件名冲突
                dest = BACKUP_DIR / f.name
                counter = 1
                while dest.exists():
                    dest = BACKUP_DIR / f"{f.stem}_{counter}{f.suffix}"
                    counter += 1
                f.rename(dest)
            except Exception as e:
                logger.error(f"迁移 JSON 对话文件 {f.name} 时出错: {e}")

    def refresh(self):
        """
        获取全部对话的元数据列表（用于侧边栏卡片展示），按 updated_at 倒序排列
        """
        result = []
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, title, model, updated_at, input_tokens, output_tokens 
                FROM conversations 
                ORDER BY datetime(updated_at) DESC
            """)
            for row in cursor.fetchall():
                result.append({
                    "id": row["id"],
                    "title": row["title"],
                    "model": row["model"],
                    "updated_at": row["updated_at"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"]
                })
        return result

    def load_conversation(self, conv_id):
        """
        加载指定 ID 对话的完整结构，包括其关联的所有消息数据（按 ID 递增排序，即时间序列）
        """
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
            if not row:
                return None
            
            conv_data = {
                "id": row["id"],
                "title": row["title"],
                "model": row["model"],
                "temperature": row["temperature"],
                "max_tokens": row["max_tokens"],
                "thinking": json.loads(row["thinking"]) if row["thinking"] else None,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "messages": []
            }
            
            # 读取消息列表，以数据库主键自增 ID 保证绝对时间顺序
            msg_cursor = conn.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,))
            for m_row in msg_cursor.fetchall():
                conv_data["messages"].append({
                    "role": m_row["role"],
                    "content": deserialize_content(m_row["content"]),
                    "thinking": m_row["thinking"],
                    "aborted": bool(m_row["aborted"])
                })
            
            return conv_data

    def save_conversation(self, conv_data):
        """
        保存对话记录并同步消息表内容：更新对话属性，删除并重建所有消息记录
        """
        conv_id = conv_data["id"]
        title = conv_data.get("title", "新对话")
        model = conv_data.get("model", "")
        temperature = conv_data.get("temperature", 0.7)
        max_tokens = conv_data.get("max_tokens", 4096)
        thinking = json.dumps(conv_data.get("thinking")) if conv_data.get("thinking") is not None else None
        created_at = conv_data.get("created_at") or datetime.now().isoformat()
        updated_at = datetime.now().isoformat()
        conv_data["updated_at"] = updated_at
        
        input_tokens = conv_data.get("input_tokens", 0)
        output_tokens = conv_data.get("output_tokens", 0)
        
        with self.get_connection() as conn:
            # 写入对话表
            conn.execute("""
                INSERT OR REPLACE INTO conversations 
                (id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (conv_id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens))
            
            # 重建消息表中的内容：先删除该对话历史消息，然后一次性全部写入最新消息
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            
            messages = conv_data.get("messages", [])
            for msg in messages:
                m_role = msg.get("role", "user")
                m_content = serialize_content(msg.get("content", ""))
                m_thinking = msg.get("thinking", None)
                m_aborted = 1 if msg.get("aborted", False) else 0
                m_created = updated_at
                
                conn.execute("""
                    INSERT INTO messages (conversation_id, role, content, thinking, aborted, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (conv_id, m_role, m_content, m_thinking, m_aborted, m_created))
            
            conn.commit()

    def delete_conversation(self, conv_id):
        """
        删除指定的对话记录，连带删除其对应的所有消息
        """
        with self.get_connection() as conn:
            # 由于 SQLite 对外键级联删除的支持依赖编译 Pragma 标志，因此这里我们显式做手动删除
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            conn.commit()

    def new_conversation(self):
        """
        创建一个空白的新对话记录并写入数据库
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
        return data

    def auto_clean(self, keep=50):
        """
        根据最后的更新时间，限制数据库中只保留前 keep 个对话，超出的历史记录会被清理以节省空间
        """
        with self.get_connection() as conn:
            # 查询应保留的对话 ID 列表
            cursor = conn.execute("""
                SELECT id FROM conversations 
                ORDER BY datetime(updated_at) DESC 
                LIMIT ?
            """, (keep,))
            kept_ids = [row["id"] for row in cursor.fetchall()]
            
            if len(kept_ids) < keep:
                return
                
            placeholders = ",".join("?" for _ in kept_ids)
            
            # 手动执行删除级联
            conn.execute(f"""
                DELETE FROM messages 
                WHERE conversation_id NOT IN ({placeholders})
            """, kept_ids)
            
            conn.execute(f"""
                DELETE FROM conversations 
                WHERE id NOT IN ({placeholders})
            """, kept_ids)
            
            conn.commit()
