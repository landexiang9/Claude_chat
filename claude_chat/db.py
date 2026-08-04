import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path
import logging

from claude_chat.config import DEFAULT_MAX_TOKENS

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


class AutoCloseConnection(sqlite3.Connection):
    """
    自定义 SQLite 连接子类，用于在 __exit__ 上下文退出时自动关闭连接，
    防止高并发或频繁读写下的数据库句柄泄露。
    """
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


class DatabaseManager:
    """
    SQLite 数据库管理器，负责应用程序对话和消息在本地数据库的读写、结构初始化及历史 JSON 数据迁移
    """
    def __init__(self):
        self.init_db()
        self.migrate_json_files()

    def get_connection(self):
        """
        获取一个 SQLite 数据库连接，并设置 row_factory 为 sqlite3.Row 以便通过字段名访问数据。
        timeout 参数设置 busy_timeout=15s，配合 WAL 模式大幅减少 "database is locked" 错误。
        """
        conn = sqlite3.connect(str(DB_PATH), factory=AutoCloseConnection, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout=15000;")
        return conn


    def init_db(self):
        """
        初始化 SQLite 数据库表结构，创建 conversations (对话表) 和 messages (消息表)
        """
        logger.info("正在初始化 SQLite 数据库表结构...")
        with self.get_connection() as conn:
            # M3: 启用 WAL 模式，允许读写并发，大幅减少 "database is locked" 错误
            conn.execute("PRAGMA journal_mode=WAL;")
            # 创建对话主表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    model TEXT,
                    platform TEXT,
                    temperature REAL,
                    max_tokens INTEGER,
                    thinking TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER
                )
            """)
            # 兼容旧库：若 conversations 表已存在但缺少 platform 列，则补列（已存在则忽略）
            try:
                conn.execute("ALTER TABLE conversations ADD COLUMN platform TEXT")
            except sqlite3.OperationalError:
                pass
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
                platform = data.get("platform", "claude")
                temperature = data.get("temperature", 0.7)
                max_tokens = data.get("max_tokens", DEFAULT_MAX_TOKENS)
                thinking = json.dumps(data.get("thinking")) if data.get("thinking") is not None else None
                created_at = data.get("created_at", datetime.now().isoformat())
                updated_at = data.get("updated_at", datetime.now().isoformat())
                input_tokens = data.get("input_tokens", 0)
                output_tokens = data.get("output_tokens", 0)
                
                with self.get_connection() as conn:
                    # 插入或替换对话记录
                    conn.execute("""
                        INSERT OR REPLACE INTO conversations 
                        (id, title, model, platform, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (conv_id, title, model, platform, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens))
                    
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
                SELECT id, title, model, platform, updated_at, input_tokens, output_tokens 
                FROM conversations 
                ORDER BY datetime(updated_at) DESC
            """)
            for row in cursor.fetchall():
                result.append({
                    "id": row["id"],
                    "title": row["title"],
                    "model": row["model"],
                    "platform": row["platform"],
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
                "platform": row["platform"],
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
        platform = conv_data.get("platform", "")
        temperature = conv_data.get("temperature", 0.7)
        max_tokens = conv_data.get("max_tokens", DEFAULT_MAX_TOKENS)
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
                (id, title, model, platform, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (conv_id, title, model, platform, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens))
            
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
            "platform": "",
            "temperature": 0.7,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "thinking": None,
            "messages": [],
        }
        self.save_conversation(data)
        return data

    def auto_clean(self, keep=50, active_conv_id=None):
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
            
            # 如果活跃对话不在 kept_ids 中，强制将其加入以避免被意外清理
            if active_conv_id and active_conv_id not in kept_ids:
                kept_ids.append(active_conv_id)
                
            # M7: 空列表守卫 — keep=0 且无活跃对话时直接返回，避免 NOT IN () 非法 SQL
            if not kept_ids:
                return
            if len(kept_ids) < keep and not active_conv_id:
                return
                
            placeholders = ",".join("?" for _ in kept_ids)
            
            # 手动执行级联删除以确保向下兼容（SQLite 虽启用了 foreign_keys，但显式清理更为安全）
            conn.execute("DELETE FROM messages WHERE conversation_id NOT IN (" + placeholders + ")", kept_ids)
            conn.execute("DELETE FROM conversations WHERE id NOT IN (" + placeholders + ")", kept_ids)
            
            conn.commit()


    def add_message(self, conv_id, role, content, thinking=None, aborted=False):
        """
        [增量更新] 向指定对话中追加一条新消息，并更新对话的更新时间，
        彻底规避多线程并发加载-修改-保存造成的“旧快照覆盖抹除新数据”的竞争风险。
        包含锁冲突重试机制（最多 3 次，每次间隔 0.5s），避免并发写入时消息丢失。
        """
        import time
        now = datetime.now().isoformat()
        aborted_val = 1 if aborted else 0
        serialized_content = serialize_content(content)
        
        for attempt in range(3):
            try:
                with self.get_connection() as conn:
                    conn.execute("BEGIN EXCLUSIVE")
                    try:
                        conn.execute("""
                            INSERT INTO messages (conversation_id, role, content, thinking, aborted, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (conv_id, role, serialized_content, thinking, aborted_val, now))
                        
                        conn.execute("""
                            UPDATE conversations 
                            SET updated_at = ? 
                            WHERE id = ?
                        """, (now, conv_id))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                return
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < 2:
                    logger.warning(f"数据库锁冲突，第 {attempt + 1} 次重试...")
                    time.sleep(0.5)
                    continue
                raise

    def add_assistant_message_and_update_tokens(self, conv_id, content, thinking=None, input_tokens=0, output_tokens=0, aborted=False):
        """
        [增量更新] 将 AI 响应内容及其 Token 消耗原子化写入数据库，
        在首轮对话结束时自动生成标题，并更新对话的更新时间。
        Token 计数采用累加语义，侧边栏显示对话总用量。
        包含锁冲突重试机制（最多 3 次，每次间隔 0.5s），避免并发写入时消息丢失。
        """
        import time
        now = datetime.now().isoformat()
        aborted_val = 1 if aborted else 0
        serialized_content = serialize_content(content)

        for attempt in range(3):
            try:
                with self.get_connection() as conn:
                    conn.execute("BEGIN EXCLUSIVE")
                    try:
                        # 1. 插入 assistant 消息
                        conn.execute("""
                            INSERT INTO messages (conversation_id, role, content, thinking, aborted, created_at)
                            VALUES (?, 'assistant', ?, ?, ?, ?)
                        """, (conv_id, serialized_content, thinking, aborted_val, now))

                        # 2. 查询当前对话状态以判断是否触发自动标题生成
                        cursor = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv_id,))
                        msg_count = cursor.fetchone()[0]
                        cursor2 = conn.execute("SELECT title FROM conversations WHERE id = ?", (conv_id,))
                        row = cursor2.fetchone()
                        current_title = row["title"] if row else ""
                        # M2: 仅当对话尚无自定义标题（仍为默认"新对话"或为空）且这是第一条 assistant 回复时生成标题
                        # 修复：带联网搜索的首轮对话因中间消息使 msg_count>2，旧条件 msg_count==2 会导致永不生成标题
                        should_generate_title = (
                            msg_count >= 2
                            and (not current_title or current_title.strip() == "新对话")
                        )

                        if should_generate_title:
                            # 自动截取前 30 个字符作为对话标题
                            title_source = ""
                            if isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        title_source = block.get("text", "")
                                        if title_source:
                                            break
                            else:
                                title_source = str(content)

                            title = title_source[:30].replace("\n", " ")
                            if not title.strip():
                                title = "新对话"
                            # M1: Token 累加（input_tokens + ?）而非覆盖
                            conn.execute("""
                                UPDATE conversations 
                                SET title = ?, input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, updated_at = ? 
                                WHERE id = ?
                            """, (title, input_tokens, output_tokens, now, conv_id))
                        else:
                            # M1: Token 累加（input_tokens + ?）而非覆盖
                            conn.execute("""
                                UPDATE conversations 
                                SET input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, updated_at = ? 
                                WHERE id = ?
                            """, (input_tokens, output_tokens, now, conv_id))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                return
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < 2:
                    logger.warning(f"数据库锁冲突，第 {attempt + 1} 次重试...")
                    time.sleep(0.5)
                    continue
                raise

    def save_conversation_metadata(self, conv_data):
        """
        [增量更新] 仅保存对话表属性，不触碰消息表，
        规避用户保存设置时与流式生成并发冲突导致的消息抹除。
        H4 修复：使用 UPDATE 而非 INSERT OR REPLACE，避免触发 ON DELETE CASCADE 级联删除消息。
        """
        conv_id = conv_data["id"]
        title = conv_data.get("title", "新对话")
        model = conv_data.get("model", "")
        platform = conv_data.get("platform")
        temperature = conv_data.get("temperature", 0.7)
        max_tokens = conv_data.get("max_tokens", DEFAULT_MAX_TOKENS)
        thinking = json.dumps(conv_data.get("thinking")) if conv_data.get("thinking") is not None else None
        updated_at = datetime.now().isoformat()
        conv_data["updated_at"] = updated_at
        
        input_tokens = conv_data.get("input_tokens", 0)
        output_tokens = conv_data.get("output_tokens", 0)
        
        with self.get_connection() as conn:
            conn.execute("""
                UPDATE conversations 
                SET title=?, model=?, platform=?, temperature=?, max_tokens=?, 
                    thinking=?, updated_at=?, input_tokens=?, output_tokens=?
                WHERE id=?
            """, (title, model, platform, temperature, max_tokens, thinking, updated_at, input_tokens, output_tokens, conv_id))
            conn.commit()
