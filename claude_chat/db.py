import sqlite3
import json
import uuid
from datetime import datetime
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "claude_chat.db"
CONVERSATIONS_DIR = BASE_DIR / "conversations"
BACKUP_DIR = BASE_DIR / "conversations_backup"


def serialize_content(content):
    if isinstance(content, (list, dict)):
        return json.dumps(content, ensure_ascii=False)
    return content


def deserialize_content(content_str):
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
    def __init__(self):
        self.init_db()
        self.migrate_json_files()

    def get_connection(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.get_connection() as conn:
            # Create conversations table
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
            # Create messages table
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
        Scans conversations/*.json, imports them into SQLite database, and backups files.
        """
        if not CONVERSATIONS_DIR.exists():
            return
        
        json_files = list(CONVERSATIONS_DIR.glob("*.json"))
        if not json_files:
            return

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        
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
                    # Insert conversation
                    conn.execute("""
                        INSERT OR REPLACE INTO conversations 
                        (id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (conv_id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens))
                    
                    # Delete existing messages for this conversation
                    conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
                    
                    # Insert messages
                    messages = data.get("messages", [])
                    for msg in messages:
                        m_role = msg.get("role", "user")
                        m_content = serialize_content(msg.get("content", ""))
                        m_thinking = msg.get("thinking", None)
                        m_aborted = 1 if msg.get("aborted", False) else 0
                        m_created = updated_at # Default to conversation updated_at for ordering
                        
                        conn.execute("""
                            INSERT INTO messages (conversation_id, role, content, thinking, aborted, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (conv_id, m_role, m_content, m_thinking, m_aborted, m_created))
                    conn.commit()
                
                # Backup the file
                dest = BACKUP_DIR / f.name
                # Handle filename conflict in backup
                counter = 1
                while dest.exists():
                    dest = BACKUP_DIR / f"{f.stem}_{counter}{f.suffix}"
                    counter += 1
                f.rename(dest)
            except Exception as e:
                print(f"Error migrating JSON file {f.name}: {e}")

    def refresh(self):
        """
        Returns all conversations metadata sorted by updated_at desc.
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
        Loads a single conversation and all its messages.
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
            
            # Load messages sorted by autoincrement ID (which ensures chronological order)
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
        Saves conversation and replaces messages.
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
            conn.execute("""
                INSERT OR REPLACE INTO conversations 
                (id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (conv_id, title, model, temperature, max_tokens, thinking, created_at, updated_at, input_tokens, output_tokens))
            
            # Re-sync messages: delete old and write new ones
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
        with self.get_connection() as conn:
            # Foreign key cascading deletes messages
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            conn.commit()

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
        return data

    def auto_clean(self, keep=50):
        """
        Keeps only the top 'keep' recently updated conversations and deletes the rest.
        """
        with self.get_connection() as conn:
            # Find the ID threshold
            cursor = conn.execute("""
                SELECT id FROM conversations 
                ORDER BY datetime(updated_at) DESC 
                LIMIT ?
            """, (keep,))
            kept_ids = [row["id"] for row in cursor.fetchall()]
            
            if len(kept_ids) < keep:
                return
                
            # SQLite does not support DELETE CASCADE unless pragma is enabled, so delete manually
            placeholders = ",".join("?" for _ in kept_ids)
            
            # Delete messages first
            conn.execute(f"""
                DELETE FROM messages 
                WHERE conversation_id NOT IN ({placeholders})
            """, kept_ids)
            
            # Delete conversations
            conn.execute(f"""
                DELETE FROM conversations 
                WHERE id NOT IN ({placeholders})
            """, kept_ids)
            
            conn.commit()
