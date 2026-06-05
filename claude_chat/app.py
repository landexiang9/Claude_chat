"""
Claude Chat - pywebview 桌面客户端核心应用模块
基于 pywebview 将前端的 HTML/JS/CSS 视图层与后端的 Python 逻辑层及本地 SQLite 数据库进行绑定。
"""

import queue
import threading
import json
import mimetypes
import logging
from datetime import datetime
from pathlib import Path

# 尝试引入本地图形库容器包
try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    webview = None
    HAS_WEBVIEW = False

logger = logging.getLogger("claude_chat")

# 本地依赖包引入
from claude_chat.config import (
    FALLBACK_MODELS, IMAGE_EXTENSIONS, PDF_EXTENSIONS, TEXT_EXTENSIONS, ConfigManager
)
from claude_chat.conversation import read_text_file
from claude_chat.db import DatabaseManager, deserialize_content
from claude_chat.client import (
    extract_api_message, stream_claude_response, fetch_available_models
)


def get_mime_type(file_path):
    """
    智能推断给定文件的 MIME Content-Type 标头值
    """
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
    """
    应用程序控制器，承载主要的生命周期事件、后台生成流的事件循环线程、本地终端运行进程锁，
    以及拉取 API 在线模型并刷新 UI 下拉菜单等异步任务。
    """
    def __init__(self, host=None, port=None, force_server=False):
        # 接收并缓存来自命令行的参数选项
        self.cli_host = host
        self.cli_port = port
        self.cli_force_server = force_server
        
        # 初始化数据库与配置管理器
        self.config = ConfigManager()
        self.conv_manager = DatabaseManager()
        self.current_conv = None
        
        # 加载初始的模型列表
        from claude_chat.client import get_default_capabilities
        self.available_models = [get_default_capabilities(m) for m in FALLBACK_MODELS]
        
        # 流生成事件队列与控制状态
        self.streaming_queue = queue.Queue()
        self.is_streaming = False
        self.abort_event = threading.Event()
        self.active_stream = None
        
        # WebView2 窗口实例
        self.window = None
        
        # 当前活跃的本地代码执行子进程映射 { process_id: { "proc": Popen对象, "temp_path": 临时文件路径 } }
        self.active_processes = {}
        self.process_lock = threading.Lock()
        self.lock = threading.Lock()
        
        # 挂载的终端日志监听回调列表
        self.console_listeners = []

    def abort_generation(self):
        """
        强行中止当前的模型输出生成流：设置事件，关闭底层连接，标记状态为非流式。
        """
        self.abort_event.set()
        if self.active_stream:
            try:
                self.active_stream.close()
            except Exception:
                pass
            self.active_stream = None
        return True

    def save_code_block(self, content, suggest_name):
        """
        弹出文件保存对话框（在 GUI 模式下），将指定内容保存为本地文件。
        """
        if not self.window:
            return False
        
        file_path = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            directory='',
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
            logger.error(f"保存代码块时发生写入错误: {e}")
            return False

    def save_image(self, image_data, suggest_name):
        """
        弹出文件保存对话框（在 GUI 模式下），将图片数据保存为本地文件。
        image_data 可以是 data URI (data:image/...;base64,...) 或 HTTP URL。
        """
        if not self.window:
            return None

        file_path = self.window.create_file_dialog(
            webview.SAVE_DIALOG,
            directory='',
            save_filename=suggest_name
        )
        if not file_path:
            return None

        if isinstance(file_path, (list, tuple)):
            file_path = file_path[0] if file_path else None
        if not file_path:
            return None

        try:
            import base64, re
            image_bytes = None

            if image_data.startswith("data:"):
                # data URI: data:image/png;base64,xxxxx
                header, encoded = image_data.split(",", 1)
                # Ensure we only decode the base64 part
                if "base64" in header:
                    image_bytes = base64.b64decode(encoded)
                else:
                    logger.error("不支持的 data URI 格式（非 base64）")
                    return None
            elif image_data.startswith("http://") or image_data.startswith("https://"):
                import urllib.request
                req = urllib.request.Request(image_data, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    image_bytes = resp.read()
            else:
                logger.error(f"不支持的图片数据格式: {image_data[:80]}...")
                return None

            if image_bytes:
                with open(file_path, "wb") as f:
                    f.write(image_bytes)
                return file_path
            return None
        except Exception as e:
            logger.error(f"保存图片时发生错误: {e}")
            return None

    def mainloop(self):
        """
        启动应用程序：
        1. 加载 JS 映射 API 桥接层。
        2. 根据配置与命令行参数决定是否启动本地 Web API 服务器。
        3. 如果是纯 Web 服务模式或未检测到 GUI 运行环境，则阻塞主线程，保持 Web 服务运行。
        4. 否则，创建 pywebview 桌面窗口并启动 GUI 消息循环。
        5. 在 GUI 退出时，自动清理并杀死所有仍然残留的控制台代码子进程。
        """
        api = WebAPI(self)
        
        # 读取服务器配置
        enable_server = self.config.get("enable_server", True)
        server_port = self.config.get("server_port", 8000)
        only_server = self.config.get("only_server", False)
        
        # 命令行参数覆盖默认值
        if self.cli_port is not None:
            server_port = self.cli_port
        if self.cli_force_server:
            only_server = True
            enable_server = True
            
        # 运行平台兼容性：如果缺失图形环境，自动降级为服务器模式运行
        if not HAS_WEBVIEW:
            logger.warning("本地未安装 pywebview 或当前运行平台不支持图形界面。自动切换为纯 Web 服务模式。")
            only_server = True
            enable_server = True
            
        bind_host = self.cli_host if self.cli_host is not None else '0.0.0.0'
        
        port = None
        is_ssl = False
        if enable_server:
            # 开启本地 HTTP/API Web 服务器
            from claude_chat.server import start_server
            port, is_ssl = start_server(self, api, host=bind_host, start_port=server_port)
            
        if only_server and enable_server and port:
            # 纯服务模式 (Headless)：阻塞当前主线程，等待退出信号
            logger.info("系统已进入 Web 纯服务模式运行，正在挂起主线程...")
            import time
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("收到终端退出指令 (Ctrl+C)，正在安全停止 Web 服务器...")
            return
            
        # 图形界面 (GUI) 模式
        logger.info("正在通过 pywebview 渲染启动 Claude Chat 图形界面...")
        if port:
            protocol = "https" if is_ssl else "http"
            url_target = f"{protocol}://localhost:{port}/?token={self.config.get('security_token', '')}"
            logger.info(f"窗口加载的本地服务器端 URL: {protocol}://localhost:{port}")
        else:
            # 如果服务器被彻底禁用，使用直接加载本地 index.html 方案
            ui_dir = Path(__file__).parent / "ui"
            url_target = str((ui_dir / "index.html").resolve())
            logger.warning("加载本地静态资源文件寻址方案。")
        
        # 初始化图形窗口
        self.window = webview.create_window(
            title="Claude Chat",
            url=url_target,
            js_api=api,
            width=1200,
            height=800,
            min_size=(950, 650),
            background_color="#181825",
            text_select=True
        )
        
        # 挂载并进入 GUI 事件循环
        webview.start()
        
        # 退出窗口后的清理收尾阶段
        logger.info("图形窗口已被关闭。正在开始清理所有后台代码运行子进程...")
        with self.process_lock:
            for proc_id, p_info in list(self.active_processes.items()):
                try:
                    proc = p_info["proc"]
                    if proc.poll() is None:
                        proc.kill()
                    Path(p_info["temp_path"]).unlink(missing_ok=True)
                except Exception:
                    pass
            self.active_processes.clear()

    def _refresh_models_async(self):
        """
        后台异步线程拉取官方 API 最新的模型列表，并在获取到最新结果后，
        执行 evaluate_js 回调将最新列表更新推送到 GUI 前端下拉列表中。
        """
        def _fetch():
            active_platform = self.config.get("active_platform", "claude")
            
            # Select the correct api key and url based on platform
            if active_platform == "deepseek":
                api_key = self.config.get("deepseek_api_key", "")
                platform_api_url = self.config.get("deepseek_api_url", "https://api.deepseek.com")
            elif active_platform == "gemini":
                api_key = self.config.get("gemini_api_key", "")
                platform_api_url = self.config.get("gemini_api_url", "")
            else:
                api_key = self.config.get("api_key", "")
                platform_api_url = None

            proxy_mode = self.config.get("proxy_mode", "system")
            proxy_url = self.config.get("proxy_url", "")
            
            model_ids = fetch_available_models(api_key, proxy_mode, proxy_url, active_platform=active_platform, platform_api_url=platform_api_url)
            if model_ids:
                self.available_models = model_ids
                if self.window:
                    # 将模型字典推送给前端注册好的全局回调函数
                    js_code = f"if (window.onModelsUpdated) window.onModelsUpdated({json.dumps(model_ids)});"
                    self.window.evaluate_js(js_code)

        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()

    def _process_sending_stream(self):
        """
        [在后台线程中运行] 主流消息读取线程。
        从内部线程队列 `streaming_queue` 中循环读取 API 响应事件块，
        并将其通过 `evaluate_js` 推送回桌面客户端前端渲染页面。
        """
        streaming_text = ""
        streaming_thinking_text = ""
        
        while True:
            try:
                msg = self.streaming_queue.get() # 阻塞直至下一条消息块到来
                msg_type, msg_data = msg
                
                if msg_type == "text":
                    # 普通文本生成包，累加并推至前端渲染
                    streaming_text += msg_data
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('text', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "thinking":
                    # 推理思维生成包，累加并推至前端渲染
                    streaming_thinking_text += msg_data
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('thinking', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "search_start":
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('search_start', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "search_done":
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('search_done', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "fetch_start":
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('fetch_start', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "fetch_done":
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('fetch_done', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                elif msg_type == "done":
                    # 生成成功结束包，计算 Token 数量并持久化写入 SQLite 数据库
                    if self.current_conv:
                        input_tokens = msg_data.get("input_tokens", 0)
                        output_tokens = msg_data.get("output_tokens", 0)
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.conv_manager.add_assistant_message_and_update_tokens(
                            self.current_conv["id"],
                            streaming_text,
                            thinking,
                            input_tokens,
                            output_tokens
                        )
                        # 从数据库重新加载以保持内存状态同步
                        self.current_conv = self.conv_manager.load_conversation(self.current_conv["id"])
                    
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('done', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                    self.is_streaming = False
                    break
                    
                elif msg_type == "aborted":
                    # 用户手工中止包，对现有生成内容作局部保存
                    if self.current_conv:
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.conv_manager.add_assistant_message_and_update_tokens(
                            self.current_conv["id"],
                            streaming_text,
                            thinking,
                            aborted=True
                        )
                        self.current_conv = self.conv_manager.load_conversation(self.current_conv["id"])
                    
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('aborted', {{}});"
                        self.window.evaluate_js(js_code)
                    
                    self.is_streaming = False
                    break
 
                elif msg_type == "error":
                    # 出错，通知前端弹窗并在本地数据库记录已生成的这部分文本内容
                    if self.current_conv and streaming_text:
                        self.conv_manager.add_assistant_message_and_update_tokens(
                            self.current_conv["id"],
                            streaming_text
                        )
                        self.current_conv = self.conv_manager.load_conversation(self.current_conv["id"])
                        
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                    self.is_streaming = False
                    break
            except Exception as e:
                if self.window:
                    from claude_chat.client import sanitize_error_message
                    cleaned_err = sanitize_error_message(e)
                    js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(cleaned_err)});"
                    self.window.evaluate_js(js_code)
                self.is_streaming = False
                break


class WebAPI:
    """
    前端 JS 的后门通信桥。
    此类中声明的全部公有方法均会被映射到前端 `window.pywebview.api` 中，
    前端可以直接以 Promise 的方式调用这些方法与 Python 线程通信。
    """
    def __init__(self, app):
        self._app = app

    def check_parsers(self):
        """
        检查本地可选解析依赖库安装状态
        """
        status = {
            "docx": False,
            "openpyxl": False,
            "pptx": False,
            "pypdf": False,
            "easyocr": False
        }
        try:
            import docx
            status["docx"] = True
        except ImportError: pass
        try:
            import openpyxl
            status["openpyxl"] = True
        except ImportError: pass
        try:
            import pptx
            status["pptx"] = True
        except ImportError: pass
        try:
            import pypdf
            status["pypdf"] = True
        except ImportError: pass
        try:
            import easyocr
            status["easyocr"] = True
        except ImportError: pass
        return status

    def get_config(self):
        """
        获取当前系统的全部配置字典数据，将敏感密钥替换为 has_xxx 标志返回给前端，保护密钥安全。
        """
        with self._app.lock:
            cfg = dict(self._app.config.data)
            api_keys = ["api_key", "tavily_api_key", "jina_api_key", "deepseek_api_key", "gemini_api_key"]
            for key in api_keys:
                cfg[f"has_{key}"] = bool(cfg.get(key, "").strip())
                cfg[key] = ""
            return cfg

    def save_config(self, new_config):
        """
        更新并存储系统配置信息
        """
        with self._app.lock:
            logger.info(f"保存系统配置，键名列表: {list(new_config.keys())}")
            
            # 1. 过滤并保存常规字段，跳过 has_xxx / clear_xxx 标志
            for k, v in new_config.items():
                if k.startswith("has_") or k.startswith("clear_"):
                    continue
                # 对于敏感 Key 字段，仅当其有值时才保存；空值由 clear_xxx 逻辑处理，防止前端空值覆盖
                if k in ["api_key", "deepseek_api_key", "gemini_api_key", "tavily_api_key", "jina_api_key"]:
                    if v:
                        self._app.config.set(k, v)
                else:
                    self._app.config.set(k, v)
            
            # 2. 显式处理清除敏感 Key 的请求
            api_keys = ["api_key", "deepseek_api_key", "gemini_api_key", "tavily_api_key", "jina_api_key"]
            for key in api_keys:
                if new_config.get(f"clear_{key}"):
                    self._app.config.set(key, "")
                
            # 刷新可用的模型列表以更新凭证
            self._app._refresh_models_async()
            
            # 联调：如果存在活跃的对话且属于全局配置范畴，同步修改对话内置的模型参数以防数据割裂
            if self._app.current_conv:
                self._app.current_conv["model"] = self._app.config.get("model")
                self._app.current_conv["temperature"] = self._app.config.get("temperature", 0.7)
                self._app.current_conv["max_tokens"] = self._app.config.get("max_tokens", 4096)
                if self._app.config.get("thinking_enabled"):
                    self._app.current_conv["thinking"] = {
                        "type": self._app.config.get("thinking_type", "adaptive"),
                        "budget_tokens": self._app.config.get("thinking_budget", 16000),
                        "effort": self._app.config.get("thinking_level", "high"),
                    }
                else:
                    self._app.current_conv["thinking"] = None
                self._app.conv_manager.save_conversation(self._app.current_conv)
            return True

    def fetch_models(self):
        """
        前端请求在线模型列表。先异步拉取缓存备用模型列表，直接立刻返回备用缓存以防止前端白屏等待。
        """
        self._app._refresh_models_async()
        return self._app.available_models

    def paste_from_clipboard(self):
        """
        从操作系统剪贴板读取文本。
        WebView2 默认存在较为严苛的安全防范策略，不允许直接通过 js 读取剪贴板。
        我们此处使用 ctypes 库直接挂钩 Windows 系统底层 Clipboard 接口，
        如果平台非 Windows，则安全退化使用 Tkinter 的剪贴板钩子读取。
        """
        import sys
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                
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
        
        # 跨端退化使用 tkinter
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
        """
        侧边栏拉取对话卡片列表并做对话总数的自动清理（默认最多保存 50 个）
        """
        with self._app.lock:
            self._app.conv_manager.auto_clean(keep=50)
            return self._app.conv_manager.refresh()

    def load_conversation(self, conv_id):
        """
        根据 ID 从 SQLite 数据库读取指定对话的详情记录并缓存至内存中
        """
        with self._app.lock:
            logger.info(f"正在加载对话记录，ID: {conv_id}")
            conv = self._app.conv_manager.load_conversation(conv_id)
            if conv:
                self._app.current_conv = conv
            return conv

    def new_conversation(self):
        """
        在数据库中初始化一条空白新对话，同时将全局配置项设置作为其默认初始值
        """
        with self._app.lock:
            logger.info("正在创建新会话...")
            conv_data = self._app.conv_manager.new_conversation()
            conv_data["model"] = self._app.config.get("model", FALLBACK_MODELS[0])
            conv_data["temperature"] = self._app.config.get("temperature", 0.7)
            conv_data["max_tokens"] = self._app.config.get("max_tokens", 4096)
            if self._app.config.get("thinking_enabled"):
                conv_data["thinking"] = {
                    "type": self._app.config.get("thinking_type", "adaptive"),
                    "budget_tokens": self._app.config.get("thinking_budget", 16000),
                    "effort": self._app.config.get("thinking_level", "high"),
                }
            self._app.conv_manager.save_conversation(conv_data)
            self._app.current_conv = conv_data
            return conv_data

    def delete_conversation(self, conv_id):
        """
        删除指定的对话及名下所有消息
        """
        with self._app.lock:
            logger.info(f"正在删除对话，ID: {conv_id}")
            self._app.conv_manager.delete_conversation(conv_id)
            if self._app.current_conv and self._app.current_conv.get("id") == conv_id:
                self._app.current_conv = None
            return True

    def get_message_packet(self, conv_id, message_index):
        """
        [高级调试：抓包工具功能] 
        返回指定消息的数据库字段记录值以及最终拼装向 Anthropic 官方接口的 JSON API Payload。
        会对大文件或图片的二进制 Base64 数据进行自动截断脱敏保护，防止 UI 渲染卡顿。
        """
        logger.info(f"正在读取抓包 Payload 数据，对话 ID: {conv_id}，索引: {message_index}")
        try:
            with self._app.conv_manager.get_connection() as conn:
                conv_row = conn.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
                if not conv_row:
                    return {"error": "未找到指定会话记录"}
                
                cursor = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", 
                    (conv_id,)
                )
                rows = cursor.fetchall()
                if message_index < 0 or message_index >= len(rows):
                    return {"error": "消息索引超出合法范围"}
                
                row = rows[message_index]
                
                # 构建数据库存储层记录展示
                db_record = {
                    "id": row["id"],
                    "conversation_id": row["conversation_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "thinking": row["thinking"],
                    "aborted": row["aborted"],
                    "created_at": row["created_at"]
                }
                
                role = row["role"]
                raw_content = deserialize_content(row["content"])
                
                # 重新映射接口兼容的数据
                from claude_chat.client import extract_api_message
                
                temp_msg = {
                    "role": role,
                    "content": raw_content
                }
                
                api_msg = extract_api_message(temp_msg)
                
                # 如果是 Assistant 思考产生的推理，在接口 Payload 中将其思维轨迹单独组装打包
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
                
                # 敏感及海量 Base64 数据流截断逻辑，以防渲染导致卡死崩溃
                def sanitize_base64_in_block(block):
                    if isinstance(block, dict) and block.get("type") in ("image", "document"):
                        source = block.get("source")
                        if isinstance(source, dict) and source.get("type") == "base64":
                            data = source.get("data")
                            if isinstance(data, str) and len(data) > 120:
                                source["data"] = f"[Base64数据已脱敏省略，大小: {len(data)} 字符]"
                    return block
                
                if isinstance(api_msg.get("content"), list):
                    api_msg["content"] = [sanitize_base64_in_block(b) for b in api_msg["content"]]
                
                thinking_val = None
                if conv_row["thinking"]:
                    try:
                        thinking_val = json.loads(conv_row["thinking"])
                    except Exception:
                        pass
                
                return {
                    "database_record": db_record,
                    "api_payload": api_msg,
                    "model": conv_row["model"],
                    "temperature": conv_row["temperature"],
                    "max_tokens": conv_row["max_tokens"],
                    "thinking_config": thinking_val,
                }
        except Exception as e:
            logger.exception(f"读取调试包数据失败: {e}")
            return {"error": str(e)}

    def select_attachments(self):
        """
        弹出文件选择对话框（在 GUI 模式下），获取用户选中的文件（支持图片、PDF和纯文本源码等），
        并将其元数据（物理路径、文件名、字节大小）返回前端。
        """
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

    def _start_stream_generation(self, conv_id, custom_queue=None):
        """
        流式接收的核心启动函数。
        根据会话 ID 捞出所有上下文消息，对复杂附件调用 client 进行转换，
        配置思维模型扩展思考模式参数（思维预算，深度水平），获取系统角色设置并启动后台生成线程。
        """
        try:
            self._app.is_streaming = True
            self._app.abort_event.clear()
            self._app.active_stream = None
            
            self._app.current_conv = self._app.conv_manager.load_conversation(conv_id)
            if not self._app.current_conv:
                self._app.is_streaming = False
                return False
                
            api_messages = [extract_api_message(msg) for msg in self._app.current_conv["messages"]]
            
            # 组装思考推理 Extended Thinking 字段
            thinking_config = None
            output_config = None
            if self._app.config.get("thinking_enabled"):
                ttype = self._app.config.get("thinking_type", "adaptive")
                if ttype == "adaptive":
                    thinking_config = {"type": "adaptive"}
                    effort_val = self._app.config.get("thinking_level", "high")
                    if effort_val:
                        output_config = {"effort": effort_val}
                elif ttype == "enabled":
                    thinking_config = {"type": "enabled", "budget_tokens": self._app.config.get("thinking_budget", 16000)}
                    
            # 挂载流通道队列
            active_queue = custom_queue if custom_queue is not None else queue.Queue()
            if custom_queue is None:
                self._app.streaming_queue = active_queue
            
            # 获取选定的系统提示词 System Prompt Preset 属性并应用
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

            # 后台线程异步发起 API 通信，放置阻塞主 GUI 事件循环导致卡死
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
                    active_queue,
                    self._app.abort_event,
                    on_stream_created
                ),
                kwargs={
                    "system": system_prompt_val,
                    "output_config": output_config,
                    "enable_search": self._app.config.get("enable_web_search", False),
                    "enable_web_fetch": self._app.config.get("enable_web_fetch", True),
                    "web_fetch_limit": self._app.config.get("web_fetch_limit", 15000),
                    "search_engine": self._app.config.get("web_search_engine", "google"),
                    "tavily_api_key": self._app.config.get("tavily_api_key", ""),
                    "jina_api_key": self._app.config.get("jina_api_key", ""),
                    "web_page_parser": self._app.config.get("web_page_parser", "local"),
                    "conv_id": conv_id,
                    "conv_manager": self._app.conv_manager,
                    "active_platform": self._app.config.get("active_platform", "claude"),
                    "deepseek_api_key": self._app.config.get("deepseek_api_key", ""),
                    "deepseek_api_url": self._app.config.get("deepseek_api_url", "https://api.deepseek.com"),
                    "gemini_api_key": self._app.config.get("gemini_api_key", ""),
                    "gemini_api_url": self._app.config.get("gemini_api_url", ""),
                    "thinking_enabled": self._app.config.get("thinking_enabled", False),
                    "thinking_budget": self._app.config.get("thinking_budget", 1024),
                    "thinking_level": self._app.config.get("thinking_level", "high"),
                    "depth": 0
                },
                daemon=True
            )
            thread.start()
            
            if custom_queue is None:
                # 开启异步读取流线程
                reader_thread = threading.Thread(
                    target=self._app._process_sending_stream,
                    daemon=True
                )
                reader_thread.start()
            return True
        except Exception as e:
            logger.exception(f"启动流生成线程发生错误: {e}")
            self._app.is_streaming = False
            return False

    def send_message(self, conv_id, text, attachments, custom_queue=None):
        """
        发送用户消息。如果是大文本附件会自动拼装文本隔离区域随 Prompt 一同发送，
        图片或 PDF 则被转换为符合接口的多媒体结构在后台线程以二进制块编码为 Base64 发送。
        """
        with self._app.lock:
            logger.info(f"正在发送消息，会话 ID: {conv_id}，文本大小: {len(text)}")
            if self._app.is_streaming:
                return False
                
            conv = self._app.conv_manager.load_conversation(conv_id)
            if not conv:
                return False
            import copy
            backup_conv = copy.deepcopy(conv)
                
            # 设置当前对话选择的模型
            if not conv.get("model"):
                conv["model"] = self._app.config.get("model")
                
            user_msg_display = {"role": "user", "content": text}
            if attachments:
                user_msg_display["content"] = [{"type": "text", "text": text}]
                for att in attachments:
                    fp = att["path"]
                    ext = Path(fp).suffix.lower()
                    mime = get_mime_type(fp)
                    if self._app.config.get("active_platform") == "deepseek" and (ext in IMAGE_EXTENSIONS or ext in PDF_EXTENSIONS):
                        from claude_chat.attachment_parser import parse_attachment_to_markdown
                        md_res = parse_attachment_to_markdown(
                            att, 
                            ocr_mode=self._app.config.get("ocr_mode", "auto"), 
                            cloud_provider=self._app.config.get("ocr_cloud_model", "gemini"), 
                            api_key=self._app.config.get("gemini_api_key", ""),
                            proxy_mode=self._app.config.get("proxy_mode", "system"),
                            proxy_url=self._app.config.get("proxy_url", "")
                        )
                        user_msg_display["content"].append({"type": "text", "text": f"\n\n--- 附件文件: {Path(fp).name} ---\n{md_res}\n--- 附件结束 ---"})
                    elif ext in IMAGE_EXTENSIONS:
                        # 图片附件
                        user_msg_display["content"].append({"type": "image", "source": {"file_path": fp, "media_type": mime}})
                    elif ext in PDF_EXTENSIONS:
                        # PDF 文档附件
                        user_msg_display["content"].append({"type": "document", "source": {"file_path": fp, "media_type": "application/pdf"}})
                    else:
                        # 纯文本/代码源文件读取附件
                        file_text = read_text_file(fp)
                        user_msg_display["content"].append({"type": "text", "text": f"\n\n--- 附件文件: {Path(fp).name} ---\n{file_text}\n--- 附件结束 ---"})
            
            conv["messages"].append(user_msg_display)
            self._app.conv_manager.save_conversation(conv)
            
            success = self._start_stream_generation(conv_id, custom_queue=custom_queue)
            if not success:
                self._app.conv_manager.save_conversation(backup_conv)
                return False
            return True

    def edit_and_resend(self, conv_id, msg_index, new_content, custom_queue=None):
        """
        用户修改并重新发送历史已发送消息：删除目标索引后的所有历史消息，重新发起生成请求。
        """
        with self._app.lock:
            logger.info(f"编辑并重新发送消息，会话 ID: {conv_id}，消息索引: {msg_index}")
            if self._app.is_streaming:
                logger.warning("当前正处于流生成阶段，禁止修改。")
                return False
                
            conv = self._app.conv_manager.load_conversation(conv_id)
            if not conv:
                return False
            import copy
            backup_conv = copy.deepcopy(conv)
                
            try:
                if msg_index < 0 or msg_index >= len(conv["messages"]):
                    logger.error(f"消息索引越界 {msg_index}")
                    return False
                
                conv["messages"] = conv["messages"][:msg_index]
                user_msg_display = {
                    "role": "user",
                    "content": new_content
                }
                conv["messages"].append(user_msg_display)
                self._app.conv_manager.save_conversation(conv)
                
                success = self._start_stream_generation(conv_id, custom_queue=custom_queue)
                if not success:
                    self._app.conv_manager.save_conversation(backup_conv)
                    return False
                return True
            except Exception as e:
                logger.exception(f"重新编辑生成消息失败: {e}")
                self._app.conv_manager.save_conversation(backup_conv)
                return False

    def retry_message(self, conv_id, msg_index, custom_queue=None):
        """
        重新生成某条 Assistant 的回答：删除此回答及其后面的所有记录，重新发起推理。
        """
        with self._app.lock:
            logger.info(f"重新生成回答，会话 ID: {conv_id}，目标消息索引: {msg_index}")
            if self._app.is_streaming:
                logger.warning("已处于流生成阶段。")
                return False
                
            conv = self._app.conv_manager.load_conversation(conv_id)
            if not conv:
                return False
            import copy
            backup_conv = copy.deepcopy(conv)
                
            try:
                if msg_index < 0 or msg_index >= len(conv["messages"]):
                    logger.error(f"消息索引越界: {msg_index}")
                    return False
                
                if conv["messages"][msg_index]["role"] != "assistant":
                     logger.error("只能对 Assistant 产生的回答消息发起重新生成。")
                     return False
                     
                conv["messages"] = conv["messages"][:msg_index]
                self._app.conv_manager.save_conversation(conv)
                
                success = self._start_stream_generation(conv_id, custom_queue=custom_queue)
                if not success:
                    self._app.conv_manager.save_conversation(backup_conv)
                    return False
                return True
            except Exception as e:
                logger.exception(f"重新生成模型回答时出错: {e}")
                self._app.conv_manager.save_conversation(backup_conv)
                return False

    def branch_conversation(self, conv_id, msg_index):
        """
        分叉新对话：从当前对话的指定位置消息中切断，复制出一条带有分支标记的新对话及上下文。
        """
        with self._app.lock:
            logger.info(f"正在创建分叉对话，源 ID: {conv_id}，目标索引: {msg_index}")
            try:
                conv = self._app.conv_manager.load_conversation(conv_id)
                if not conv:
                    logger.error(f"源对话 {conv_id} 不存在。")
                    return None
                    
                if msg_index < 0 or msg_index >= len(conv["messages"]):
                    logger.error("消息索引越界。")
                    return None
                
                branch_messages = conv["messages"][:msg_index + 1]
                branch_title = f"{conv['title'] or '新对话'} (分叉)"
                import uuid
                new_conv_id = str(uuid.uuid4())
                now_str = datetime.now().isoformat()
                
                new_conv = {
                    "id": new_conv_id,
                    "title": branch_title,
                    "model": conv.get("model", ""),
                    "temperature": conv.get("temperature", 0.7),
                    "max_tokens": conv.get("max_tokens", 4096),
                    "thinking": conv.get("thinking"),
                    "created_at": now_str,
                    "updated_at": now_str,
                    "input_tokens": conv.get("input_tokens", 0),
                    "output_tokens": conv.get("output_tokens", 0),
                    "messages": branch_messages
                }
                
                self._app.conv_manager.save_conversation(new_conv)
                logger.info(f"对话成功分叉至新会话，新 ID: {new_conv_id}")
                return self._app.conv_manager.load_conversation(new_conv_id)
            except Exception as e:
                logger.exception(f"创建分叉对话失败: {e}")
                return None

    def start_code_execution(self, code, lang):
        """
        [核心功能：代码沙盒执行器]
        接收前端传入的逻辑代码和目标语言类型 (Python/Javascript)，
        将其写入安全临时文件中并启动本地相应的子进程 (Python 解释器 / Node.js) 异步执行之。
        使用线程实时读取子进程的 stdout 与 stderr 管道，并即时推送到前端控制台面板进行交互式渲染。
        """
        if not self._app.config.get("enable_code_sandbox", False):
            return {"error": "出于安全考虑，代码沙盒执行功能已默认关闭。请在配置文件或设置中手动开启后使用。"}
            
        logger.info(f"正在异步启动本地沙盒代码块执行，语言: {lang}")
        import subprocess
        import tempfile
        import sys
        import uuid
        import os
        import codecs
        import threading
        import atexit
        
        norm_lang = lang.lower()
        if norm_lang not in ("python", "javascript", "js"):
            return {"error": f"不支持的代码执行语言: {lang}"}
            
        if norm_lang == "python":
            suffix = ".py"
            executable = sys.executable
        else:
            suffix = ".js"
            executable = "node"
            
        proc_id = str(uuid.uuid4())
        
        try:
            # 写入临时文件
            with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False, encoding='utf-8') as temp_file:
                temp_file.write(code)
                temp_path = temp_file.name
                
            def cleanup_temp_file(path):
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except Exception:
                    pass
            atexit.register(cleanup_temp_file, temp_path)
                
            run_env = os.environ.copy()
            if norm_lang == "python":
                run_env["PYTHONIOENCODING"] = "utf-8"
                run_env["PYTHONUTF8"] = "1"
                
            # 拉起代码执行子进程
            proc = subprocess.Popen(
                [executable, temp_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=run_env
            )
            
            with self._app.process_lock:
                self._app.active_processes[proc_id] = {
                    "proc": proc,
                    "temp_path": temp_path
                }
                
            # 后台线程：分批解码读取进程的打印信息并即时通过 websocket 推送
            def read_stream(stream, stream_type):
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                while True:
                    try:
                        b = stream.read(1)
                        if not b:
                            break
                        char = decoder.decode(b)
                        if char:
                            if self._app.window:
                                # 调用前端的 console 全局回调进行打字机特效输出
                                js_code = f"if (window.onConsoleOutput) window.onConsoleOutput({json.dumps(proc_id)}, {json.dumps(stream_type)}, {json.dumps(char)});"
                                self._app.window.evaluate_js(js_code)
                            # 触发注册的事件广播处理器
                            event = {"proc_id": proc_id, "stream": stream_type, "text": char}
                            for cb in list(self._app.console_listeners):
                                try:
                                    cb(event)
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.error(f"读取子进程 {stream_type} 管道打印出错: {e}")
                        break
                try:
                    final_char = decoder.decode(b'', final=True)
                    if final_char:
                        if self._app.window:
                            js_code = f"if (window.onConsoleOutput) window.onConsoleOutput({json.dumps(proc_id)}, {json.dumps(stream_type)}, {json.dumps(final_char)});"
                            self._app.window.evaluate_js(js_code)
                        event = {"proc_id": proc_id, "stream": stream_type, "text": final_char}
                        for cb in list(self._app.console_listeners):
                            try:
                                    cb(event)
                            except Exception:
                                    pass
                except Exception:
                    pass
            
            # 后台线程：对进程生存状态进行监听，负责进程死亡后的垃圾文件清理及出口状态收集
            def monitor_process(t_out, t_err):
                try:
                    # 设定 120 秒超时机制防止资源死锁
                    exit_code = proc.wait(timeout=120.0)
                    # 进程退出后，等待 stdout 和 stderr 线程将缓冲区的数据读取完毕 (最多等待2秒防死锁)
                    t_out.join(timeout=2.0)
                    t_err.join(timeout=2.0)
                except subprocess.TimeoutExpired:
                    logger.warning(f"沙盒进程执行超时 (120秒)，即将强制终止 {proc_id}")
                    proc.terminate()
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    exit_code = -1
                except Exception as e:
                    logger.error(f"等待子进程返回异常: {e}")
                    exit_code = -1
                finally:
                    # 销毁清除映射及删除磁盘中的代码临时文件
                    with self._app.process_lock:
                        if proc_id in self._app.active_processes:
                            p_info = self._app.active_processes.pop(proc_id)
                            try:
                                Path(p_info["temp_path"]).unlink(missing_ok=True)
                            except Exception:
                                pass
                                
                    if self._app.window:
                        js_code = f"if (window.onConsoleExit) window.onConsoleExit({json.dumps(proc_id)}, {exit_code});"
                        self._app.window.evaluate_js(js_code)
                    event = {"proc_id": proc_id, "stream": "exit", "exit_code": exit_code}
                    for cb in list(self._app.console_listeners):
                        try:
                            cb(event)
                        except Exception:
                            pass
            
            # 开启三驾马车子线程实时读取和监控
            t_stdout = threading.Thread(target=read_stream, args=(proc.stdout, "stdout"), daemon=True)
            t_stderr = threading.Thread(target=read_stream, args=(proc.stderr, "stderr"), daemon=True)
            t_monitor = threading.Thread(target=monitor_process, args=(t_stdout, t_stderr), daemon=True)
            
            t_stdout.start()
            t_stderr.start()
            t_monitor.start()
            
            return {"process_id": proc_id}
            
        except FileNotFoundError:
            if norm_lang in ("javascript", "js"):
                err_msg = "本地系统未检测到 Node.js 运行环境，请确保安装并已将其添加进环境变量 PATH。"
            else:
                err_msg = "本地未检测到 Python 运行环境。"
            return {"error": err_msg}
        except Exception as e:
            logger.exception(f"启动沙盒代码执行模块错误: {e}")
            return {"error": str(e)}

    def send_console_input(self, process_id, text):
        """
        向正在后台执行的代码进程输入区写入回车文本（向其 stdin 发送信息）
        """
        logger.info(f"正在向子进程发送输入 {process_id}: {text}")
        with self._app.process_lock:
            p_info = self._app.active_processes.get(process_id)
            if not p_info:
                return False
            
            try:
                proc = p_info["proc"]
                if proc.poll() is None:
                    proc.stdin.write((text + "\n").encode("utf-8"))
                    proc.stdin.flush()
                    return True
            except Exception as e:
                logger.error(f"写入子进程 stdin 管道错误 {process_id}: {e}")
            return False

    def kill_console_process(self, process_id):
        """
        强制杀掉后台代码进程
        """
        logger.info(f"强制中止代码进程: {process_id}")
        with self._app.process_lock:
            p_info = self._app.active_processes.get(process_id)
            if not p_info:
                return False
                
            try:
                proc = p_info["proc"]
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                return True
            except Exception as e:
                logger.error(f"杀死后台进程 {process_id} 失败: {e}")
            return False

    def abort_generation(self):
        return self._app.abort_generation()

    def save_code_block(self, content, suggest_name):
        return self._app.save_code_block(content, suggest_name)

    def save_image(self, image_data, suggest_name):
        return self._app.save_image(image_data, suggest_name)

    def upload_dropped_file(self, name, size, base64_data):
        """
        处理前端拖拽或上传的小文件/图片等。
        解析前端上传的 Base64 文件流并存盘至操作系统的用户目录下的临时缓存路径中。
        """
        import base64
        import tempfile
        temp_dir = Path(tempfile.gettempdir()) / "claude_chat_uploads"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        # 兼容处理 base64-data 头部声明
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
            # 重名冲突预防
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
            logger.error(f"保存前端上传的附件临时文件失败: {e}")
            return None

    def get_logs(self, max_lines=200):
        """
        读取本地最新的运行日志（反向读取最新 max_lines 行）
        """
        from claude_chat.config import LOG_PATH
        if not LOG_PATH.exists():
            return "暂无运行日志。"
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            last_lines = lines[-max_lines:] if len(lines) > max_lines else lines
            return "".join(last_lines)
        except Exception as e:
            logger.error(f"读取本地日志失败: {e}")
            return f"读取日志出错: {e}"

    def clear_logs(self):
        """
        清空日志内容
        """
        from claude_chat.config import LOG_PATH
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} - claude_chat - INFO - Log cleared by user.\n")
            logger.info("系统日志已被用户成功清除。")
            return True
        except Exception as e:
            logger.error(f"清空日志失败: {e}")
            return False
