"""
Claude Chat - pywebview Application App module
Replaces the old CustomTkinter app with Webview2 interface.
"""

import queue
import threading
import json
import mimetypes
from datetime import datetime
from pathlib import Path
import webview

# Local package imports
from claude_chat.config import (
    FALLBACK_MODELS, IMAGE_EXTENSIONS, PDF_EXTENSIONS, TEXT_EXTENSIONS, ConfigManager
)
from claude_chat.conversation import ConversationManager, read_text_file
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
        self.conv_manager = ConversationManager()
        self.current_conv = None
        self.available_models = list(FALLBACK_MODELS)
        self.streaming_queue = queue.Queue()
        self.is_streaming = False
        self.window = None

    def mainloop(self):
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
        # Update config fields
        for k, v in new_config.items():
            self._app.config.set(k, v)
            
        # If API key or proxy settings changed, update available models
        self._app._refresh_models_async()
        
        # If there's an active conversation, update parameters as well
        if self._app.current_conv:
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
        conv = self._app.conv_manager.load_conversation(conv_id)
        if conv:
            self._app.current_conv = conv
        return conv

    def new_conversation(self):
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
        self._app.conv_manager.delete_conversation(conv_id)
        if self._app.current_conv and self._app.current_conv.get("id") == conv_id:
            self._app.current_conv = None
        return True

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
        if self._app.is_streaming:
            return False
        
        self._app.is_streaming = True
        
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
                self._app.streaming_queue
            ),
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
