"""
Claude Chat - pywebview Application App module
Replaces the old CustomTkinter app with Webview2 interface.
"""

import queue
import threading
import json
import mimetypes
import logging
from datetime import datetime
from pathlib import Path
import webview

logger = logging.getLogger("claude_chat")

# Local package imports
from claude_chat.config import (
    FALLBACK_MODELS, IMAGE_EXTENSIONS, PDF_EXTENSIONS, TEXT_EXTENSIONS, ConfigManager
)
from claude_chat.conversation import read_text_file
from claude_chat.db import DatabaseManager, deserialize_content
from claude_chat.client import (
    extract_api_message, stream_claude_response, fetch_available_models
)


def get_mime_type(file_path):
    ext = Path(file_path).suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
        ".txt": "text/plain", ".py": "text/x-python", ".js": "text/javascript",
        ".ts": "text/typescript", ".html": "text/html", ".css": "text/css",
        ".json": "application/json", ".xml": "application/xml",
        ".yaml": "text/yaml", ".yml": "text/yaml", ".md": "text/markdown",
        ".csv": "text/csv", ".sql": "text/x-sql",
    }
    if ext in mime_map:
        return mime_map[ext]
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


class ClaudeChatApp:
    def __init__(self):
        self.config = ConfigManager()
        self.conv_manager = DatabaseManager()
        self.current_conv = None
        self.available_models = list(FALLBACK_MODELS)
        self.streaming_queue = queue.Queue()
        self.is_streaming = False
        self.abort_event = threading.Event()
        self.active_stream = None
        self.window = None

    def abort_generation(self):
        self.abort_event.set()
        if self.active_stream:
            try:
                self.active_stream.close()
            except Exception:
                pass
            self.active_stream = None
        self.is_streaming = False
        return True

    def save_code_block(self, content, suggest_name):
        if not self.window:
            return False
        
        file_path = self.window.create_file_dialog(
            webview.SAVE_FILE_DIALOG,
            directory=None,
            save_filename=suggest_name
        )
        if not file_path:
            return False
            
        if isinstance(file_path, (list, tuple)):
            if file_path:
                file_path = file_path[0]
            else:
                return False
                
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"Error saving code block: {e}")
            return False

    def mainloop(self):
        logger.info("Starting Claude Chat app GUI via pywebview...")
        # We start the webview window
        # HTML file path
        ui_dir = Path(__file__).parent / "ui"
        html_file = ui_dir / "index.html"
        
        # Load custom API bridge
        api = WebAPI(self)
        
        # Create pywebview window
        self.window = webview.create_window(
            title="Claude Chat",
            url=str(html_file.resolve()),
            js_api=api,
            width=1200,
            height=800,
            min_size=(950, 650),
            background_color="#181825",
            text_select=True
        )
        
        # Start the pywebview event loop
        webview.start()

    def _refresh_models_async(self):
        def _fetch():
            api_key = self.config.get("api_key")
            proxy_mode = self.config.get("proxy_mode", "system")
            proxy_url = self.config.get("proxy_url", "")
            
            model_ids = fetch_available_models(api_key, proxy_mode, proxy_url)
            if model_ids:
                self.available_models = model_ids
                if self.window:
                    js_code = f"if (window.onModelsUpdated) window.onModelsUpdated({json.dumps(model_ids)});"
                    self.window.evaluate_js(js_code)

        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()

    def _process_sending_stream(self):
        streaming_text = ""
        streaming_thinking_text = ""
        
        while True:
            try:
                msg = self.streaming_queue.get() # block until next message
                msg_type, msg_data = msg
                
                if msg_type == "text":
                    streaming_text += msg_data
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('text', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "thinking":
                    streaming_thinking_text += msg_data
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('thinking', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "done":
                    # Finalize message in DB
                    if self.current_conv:
                        self.current_conv["input_tokens"] = msg_data.get("input_tokens", 0)
                        self.current_conv["output_tokens"] = msg_data.get("output_tokens", 0)
                        self.current_conv["messages"].append({
                            "role": "assistant",
                            "content": streaming_text,
                            "thinking": streaming_thinking_text if streaming_thinking_text else None
                        })
                        # Auto set title if it was a new conversation
                        if len(self.current_conv["messages"]) == 2:
                            title = streaming_text[:30].replace("\n", " ")
                            self.current_conv["title"] = title or "新对话"
                        self.current_conv["updated_at"] = datetime.now().isoformat()
                        self.conv_manager.save_conversation(self.current_conv)
                    
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('done', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                    self.is_streaming = False
                    break
                    
                elif msg_type == "aborted":
                    if self.current_conv:
                        self.current_conv["messages"].append({
                            "role": "assistant",
                            "content": streaming_text,
                            "thinking": streaming_thinking_text if streaming_thinking_text else None,
                            "aborted": True
                        })
                        if len(self.current_conv["messages"]) == 2:
                            title = streaming_text[:30].replace("\n", " ")
                            self.current_conv["title"] = title or "新对话"
                        self.current_conv["updated_at"] = datetime.now().isoformat()
                        self.conv_manager.save_conversation(self.current_conv)
                    
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('aborted', {{}});"
                        self.window.evaluate_js(js_code)
                    
                    self.is_streaming = False
                    break

                elif msg_type == "error":
                    if self.current_conv and streaming_text:
                        self.current_conv["messages"].append({
                            "role": "assistant",
                            "content": streaming_text
                        })
                        self.current_conv["updated_at"] = datetime.now().isoformat()
                        self.conv_manager.save_conversation(self.current_conv)
                        
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                    self.is_streaming = False
                    break
            except Exception as e:
                if self.window:
                    js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(str(e))});"
                    self.window.evaluate_js(js_code)
                self.is_streaming = False
                break


class WebAPI:
    def __init__(self, app):
        self._app = app

    def get_config(self):
        return self._app.config.data

    def save_config(self, new_config):
        logger.info(f"Saving system configuration. Keys present: {list(new_config.keys())}")
        # Update config fields
        for k, v in new_config.items():
            self._app.config.set(k, v)
            
        # If API key or proxy settings changed, update available models
        self._app._refresh_models_async()
        
        # If there's an active conversation, update parameters as well
        if self._app.current_conv:
            self._app.current_conv["model"] = self._app.config.get("model")
            self._app.current_conv["temperature"] = self._app.config.get("temperature", 0.7)
            self._app.current_conv["max_tokens"] = self._app.config.get("max_tokens", 4096)
            if self._app.config.get("thinking_enabled"):
                self._app.current_conv["thinking"] = {
                    "type": self._app.config.get("thinking_type", "adaptive"),
                    "budget_tokens": self._app.config.get("thinking_budget", 16000),
                }
            else:
                self._app.current_conv["thinking"] = None
            self._app.conv_manager.save_conversation(self._app.current_conv)
        return True

    def fetch_models(self):
        # Trigger background refresh asynchronously
        self._app._refresh_models_async()
        # Return cached list immediately to prevent blocking the startup UI
        return self._app.available_models

    def paste_from_clipboard(self):
        import sys
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                
                # Setup arg/res types
                user32.OpenClipboard.argtypes = [wintypes.HWND]
                user32.OpenClipboard.restype = wintypes.BOOL
                user32.CloseClipboard.argtypes = []
                user32.CloseClipboard.restype = wintypes.BOOL
                user32.GetClipboardData.argtypes = [wintypes.UINT]
                user32.GetClipboardData.restype = wintypes.HANDLE
                
                kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
                kernel32.GlobalLock.restype = wintypes.LPVOID
                kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
                kernel32.GlobalUnlock.restype = wintypes.BOOL
                
                if user32.OpenClipboard(None):
                    try:
                        CF_UNICODETEXT = 13
                        handle = user32.GetClipboardData(CF_UNICODETEXT)
                        if handle:
                            ptr = kernel32.GlobalLock(handle)
                            if ptr:
                                try:
                                    return ctypes.wstring_at(ptr)
                                finally:
                                    kernel32.GlobalUnlock(handle)
                    finally:
                        user32.CloseClipboard()
            except Exception:
                pass
        
        # Fallback to tkinter
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            text = root.clipboard_get()
            root.destroy()
            return text
        except Exception:
            return ""


    def load_conversations(self):
        self._app.conv_manager.auto_clean(keep=50)
        return self._app.conv_manager.refresh()

    def load_conversation(self, conv_id):
        logger.info(f"Loading conversation details for ID: {conv_id}")
        conv = self._app.conv_manager.load_conversation(conv_id)
        if conv:
            self._app.current_conv = conv
        return conv

    def new_conversation(self):
        logger.info("Initializing a new conversation session...")
        conv_data = self._app.conv_manager.new_conversation()
        conv_data["model"] = self._app.config.get("model", FALLBACK_MODELS[0])
        conv_data["temperature"] = self._app.config.get("temperature", 0.7)
        conv_data["max_tokens"] = self._app.config.get("max_tokens", 4096)
        if self._app.config.get("thinking_enabled"):
            conv_data["thinking"] = {
                "type": self._app.config.get("thinking_type", "adaptive"),
                "budget_tokens": self._app.config.get("thinking_budget", 16000),
            }
        self._app.conv_manager.save_conversation(conv_data)
        self._app.current_conv = conv_data
        return conv_data

    def delete_conversation(self, conv_id):
        logger.info(f"Deleting conversation ID: {conv_id}")
        self._app.conv_manager.delete_conversation(conv_id)
        if self._app.current_conv and self._app.current_conv.get("id") == conv_id:
            self._app.current_conv = None
        return True

    def get_message_packet(self, conv_id, message_index):
        logger.info(f"Fetching raw packet for conversation {conv_id} at index {message_index}")
        try:
            with self._app.conv_manager.get_connection() as conn:
                conv_row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
                if not conv_row:
                    return {"error": "Conversation not found"}
                
                cursor = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", 
                    (conv_id,)
                )
                rows = cursor.fetchall()
                if message_index < 0 or message_index >= len(rows):
                    return {"error": "Message index out of range"}
                
                row = rows[message_index]
                
                # Construct database record
                db_record = {
                    "id": row["id"],
                    "conversation_id": row["conversation_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "thinking": row["thinking"],
                    "aborted": row["aborted"],
                    "created_at": row["created_at"]
                }
                
                # Construct API payload
                role = row["role"]
                raw_content = deserialize_content(row["content"])
                
                # Use client.py's extract_api_message logic to form content
                from claude_chat.client import extract_api_message
                
                temp_msg = {
                    "role": role,
                    "content": raw_content
                }
                
                api_msg = extract_api_message(temp_msg)
                
                # For assistant message, if there is thinking, include it in the API payload's content
                if role == "assistant" and row["thinking"]:
                    thinking_text = row["thinking"]
                    has_thinking_block = False
                    if isinstance(api_msg.get("content"), list):
                        for block in api_msg["content"]:
                            if isinstance(block, dict) and block.get("type") == "thinking":
                                has_thinking_block = True
                                break
                    
                    if not has_thinking_block:
                        thinking_block = {
                            "type": "thinking",
                            "thinking": thinking_text,
                            "signature": "omitted_for_display"
                        }
                        if isinstance(api_msg.get("content"), list):
                            api_msg["content"].insert(0, thinking_block)
                        else:
                            api_msg["content"] = [thinking_block, {"type": "text", "text": str(raw_content)}]
                
                # Sanitize base64 in api_msg
                def sanitize_base64_in_block(block):
                    if isinstance(block, dict) and block.get("type") in ("image", "document"):
                        source = block.get("source")
                        if isinstance(source, dict) and source.get("type") == "base64":
                            data = source.get("data")
                            if isinstance(data, str) and len(data) > 120:
                                source["data"] = f"[BASE64_DATA_OMITTED_SIZE_{len(data)}_CHARS]"
                    return block
                
                if isinstance(api_msg.get("content"), list):
                    api_msg["content"] = [sanitize_base64_in_block(b) for b in api_msg["content"]]
                
                return {
                    "database_record": db_record,
                    "api_payload": api_msg,
                    "model": conv_row["model"],
                    "temperature": conv_row["temperature"],
                    "max_tokens": conv_row["max_tokens"],
                }
        except Exception as e:
            logger.exception(f"Error fetching raw packet: {e}")
            return {"error": str(e)}

    def select_attachments(self):
        if not self._app.window:
            return []
        
        file_paths = self._app.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=('All Supported Files (*.png;*.jpg;*.jpeg;*.gif;*.webp;*.pdf;*.txt;*.py;*.js;*.ts;*.html;*.css;*.md;*.json;*.xml;*.yaml;*.yml;*.csv;*.sql;*.c;*.cpp;*.h)', 'All Files (*.*)')
        )
        if not file_paths:
            return []
        
        result = []
        for fp in file_paths:
            try:
                p = Path(fp)
                if p.exists():
                    result.append({
                        "path": str(p.resolve()),
                        "name": p.name,
                        "size": p.stat().st_size
                    })
            except Exception:
                pass
        return result

    def send_message(self, conv_id, text, attachments):
        logger.info(f"Sending message in conversation {conv_id}. Text length: {len(text)}. Attachments: {len(attachments) if attachments else 0}")
        if self._app.is_streaming:
            return False
        
        self._app.is_streaming = True
        self._app.abort_event.clear()
        self._app.active_stream = None
        
        # Load conversation
        conv = self._app.conv_manager.load_conversation(conv_id)
        if not conv:
            self._app.is_streaming = False
            return False
            
        self._app.current_conv = conv
        
        # Set config model if not matching
        if not conv.get("model"):
            conv["model"] = self._app.config.get("model")
            
        user_msg_display = {"role": "user", "content": text}
        if attachments:
            user_msg_display["content"] = [{"type": "text", "text": text}]
            for att in attachments:
                fp = att["path"]
                ext = Path(fp).suffix.lower()
                mime = get_mime_type(fp)
                if ext in IMAGE_EXTENSIONS:
                    user_msg_display["content"].append({"type": "image", "source": {"file_path": fp, "media_type": mime}})
                elif ext in PDF_EXTENSIONS:
                    user_msg_display["content"].append({"type": "document", "source": {"file_path": fp, "media_type": "application/pdf"}})
                else:
                    file_text = read_text_file(fp)
                    user_msg_display["content"].append({"type": "text", "text": f"\n\n--- 文件: {Path(fp).name} ---\n{file_text}\n--- 文件结束 ---"})
        
        self._app.current_conv["messages"].append(user_msg_display)
        self._app.conv_manager.save_conversation(self._app.current_conv)
        
        api_messages = [extract_api_message(msg) for msg in self._app.current_conv["messages"]]
        
        thinking_config = None
        if self._app.config.get("thinking_enabled"):
            ttype = self._app.config.get("thinking_type", "adaptive")
            if ttype == "adaptive":
                thinking_config = {"type": "adaptive"}
            elif ttype == "enabled":
                thinking_config = {"type": "enabled", "budget_tokens": self._app.config.get("thinking_budget", 16000)}
                
        self._app.streaming_queue = queue.Queue()
        
        # Get active system prompt from config
        system_prompt_val = None
        selected_id = self._app.config.get("selected_system_prompt_id", "")
        if selected_id:
            presets = self._app.config.get("system_prompts", [])
            for p in presets:
                if p.get("id") == selected_id:
                    system_prompt_val = p.get("content")
                    break
        
        def on_stream_created(stream):
            self._app.active_stream = stream

        # Launch background stream
        thread = threading.Thread(
            target=stream_claude_response,
            args=(
                self._app.config.get("api_key"),
                self._app.config.get("proxy_mode", "system"),
                self._app.config.get("proxy_url", ""),
                api_messages,
                self._app.current_conv.get("model", self._app.config.get("model")),
                self._app.config.get("max_tokens", 4096),
                self._app.config.get("temperature", 0.7),
                thinking_config,
                self._app.streaming_queue,
                self._app.abort_event,
                on_stream_created
            ),
            kwargs={"system": system_prompt_val},
            daemon=True
        )
        thread.start()
        
        # Start a queue reader thread
        reader_thread = threading.Thread(
            target=self._app._process_sending_stream,
            daemon=True
        )
        reader_thread.start()
        return True

    def abort_generation(self):
        return self._app.abort_generation()

    def save_code_block(self, content, suggest_name):
        return self._app.save_code_block(content, suggest_name)

    def upload_dropped_file(self, name, size, base64_data):
        import base64
        import tempfile
        temp_dir = Path(tempfile.gettempdir()) / "claude_chat_uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        if "," in base64_data:
            _, encoded = base64_data.split(",", 1)
        else:
            encoded = base64_data
            
        try:
            data = base64.b64decode(encoded)
            stem = Path(name).stem
            suffix = Path(name).suffix
            dest_path = temp_dir / name
            counter = 1
            while dest_path.exists():
                dest_path = temp_dir / f"{stem}_{counter}{suffix}"
                counter += 1
                
            with open(dest_path, "wb") as f:
                f.write(data)
                
            return {
                "path": str(dest_path.resolve()),
                "name": dest_path.name,
                "size": size
            }
        except Exception as e:
            logger.error(f"Error saving dropped file: {e}")
            return None

    def get_logs(self, max_lines=200):
        from claude_chat.config import LOG_PATH
        if not LOG_PATH.exists():
            return "暂无运行日志。"
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            last_lines = lines[-max_lines:] if len(lines) > max_lines else lines
            return "".join(last_lines)
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            return f"读取日志出错: {e}"

    def clear_logs(self):
        from claude_chat.config import LOG_PATH
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} - claude_chat - INFO - Log cleared by user.\n")
            logger.info("Logs cleared successfully.")
            return True
        except Exception as e:
            logger.error(f"Error clearing log file: {e}")
            return False
