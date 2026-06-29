"""
Claude Chat - pywebview 桌面客户端核心应用模块
基于 pywebview 将前端的 HTML/JS/CSS 视图层与后端的 Python 逻辑层及本地 SQLite 数据库进行绑定。
"""

import queue
import threading
import json
import logging
import ipaddress
import socket
from datetime import datetime
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

# 尝试引入本地图形库容器包
try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    webview = None
    HAS_WEBVIEW = False

logger = logging.getLogger("claude_chat")

from claude_chat.config import FALLBACK_MODELS, ConfigManager, find_custom_provider, custom_platform_id
from claude_chat.db import DatabaseManager
from claude_chat.clients import fetch_available_models, get_default_capabilities, sanitize_error_message

from claude_chat.api_bridge import WebAPI

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
        from claude_chat.clients import get_default_capabilities
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
        self.lock = threading.RLock()
        
        # 挂载的终端日志监听回调列表
        self.console_listeners = []

    def set_streaming_done(self):
        """
        M9: 线程安全地将 is_streaming 置为 False。
        供 reader 线程和 HTTP server 的流式处理线程统一调用，避免无锁并发竞态。
        """
        with self.lock:
            self.is_streaming = False

    def abort_generation(self):
        """
        强行中止当前的模型输出生成流：设置事件，关闭底层连接，标记状态为非流式。
        M9: 加锁保护 is_streaming/active_stream 的读写，防止与 reader 线程并发竞态。
        """
        with self.lock:
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
            webview.FileDialog.SAVE,
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

    @staticmethod
    def _is_safe_url(url):
        """
        SSRF 防护:校验 http(s) URL 的主机不指向私有/环回/链路本地地址。
        用于 LLM 渲染输出中 <img src> 等模型可控 URL 的出站抓取。
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False
        # 域名解析为 IP 后逐个校验,拒绝内网/元数据地址
        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            return False
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True

    def save_image(self, image_data, suggest_name):
        """
        弹出文件保存对话框（在 GUI 模式下），将图片数据保存为本地文件。
        image_data 可以是 data URI (data:image/...;base64,...) 或 HTTP URL。
        """
        if not self.window:
            return None

        file_path = self.window.create_file_dialog(
            webview.FileDialog.SAVE,
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
                # SSRF 防护:模型可控的 <img src> 可能指向内网/元数据地址,校验后再抓取
                if not self._is_safe_url(image_data):
                    logger.error(f"拒绝抓取不安全的图片 URL: {image_data[:80]}...")
                    return None
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
            elif active_platform.startswith("custom:"):
                provider = find_custom_provider(self.config.data, active_platform) or {}
                pid = custom_platform_id(active_platform)
                api_key = self.config.get(f"custom_{pid}_api_key", "") if pid else ""
                platform_api_url = provider.get("api_url", "")
            else:
                api_key = self.config.get("api_key", "")
                platform_api_url = None

            proxy_mode = self.config.get("proxy_mode", "system")
            proxy_url = self.config.get("proxy_url", "")
            
            custom_fallback = None
            custom_models_api_url = None
            if active_platform.startswith("custom:"):
                provider = find_custom_provider(self.config.data, active_platform) or {}
                custom_fallback = provider.get("models", [])
                custom_models_api_url = provider.get("models_api_url", "")
            
            model_ids = fetch_available_models(api_key, proxy_mode, proxy_url, active_platform=active_platform, platform_api_url=platform_api_url, custom_fallback_models=custom_fallback, custom_models_api_url=custom_models_api_url)
            
            # 校验平台是否在拉取期间发生切换，防止旧请求覆盖新平台的模型列表
            current_platform = self.config.get("active_platform", "claude")
            if current_platform != active_platform:
                return
                
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
        M9: 保存 queue 的局部引用，循环中始终使用该引用而非 self.streaming_queue，
        防止流结束后用户立即发新消息导致 self.streaming_queue 被替换、旧 reader 读到新 queue 的竞态。
        """
        my_queue = self.streaming_queue
        streaming_text = ""
        streaming_thinking_text = ""
        # 在线程启动时一次性捕获当前对话 id,避免后续被 load/delete/save 等持锁操作改写
        # 导致"流式中切换对话 → 响应写进错误对话"或 NoneType 不可下标的竞态 (#5/#6)。
        my_conv = self.current_conv
        conv_id = my_conv["id"] if my_conv else None

        while True:
            try:
                msg = my_queue.get() # 阻塞直至下一条消息块到来
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
                    # 生成成功结束包,计算 Token 数量并持久化写入 SQLite 数据库
                    if conv_id:
                        input_tokens = msg_data.get("input_tokens", 0)
                        output_tokens = msg_data.get("output_tokens", 0)
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.conv_manager.add_assistant_message_and_update_tokens(
                            conv_id,
                            streaming_text,
                            thinking,
                            input_tokens,
                            output_tokens
                        )
                        # 仅当用户未在流式期间切换对话时才同步内存,避免覆盖已切换到的新对话
                        with self.lock:
                            if self.current_conv and self.current_conv.get("id") == conv_id:
                                self.current_conv = self.conv_manager.load_conversation(conv_id)
                    
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('done', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                    self.set_streaming_done()
                    break
                    
                elif msg_type == "aborted":
                    # 用户手工中止包,对现有生成内容作局部保存
                    if conv_id:
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.conv_manager.add_assistant_message_and_update_tokens(
                            conv_id,
                            streaming_text,
                            thinking,
                            aborted=True
                        )
                        with self.lock:
                            if self.current_conv and self.current_conv.get("id") == conv_id:
                                self.current_conv = self.conv_manager.load_conversation(conv_id)
                    
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('aborted', {{}});"
                        self.window.evaluate_js(js_code)
                    
                    self.set_streaming_done()
                    break
 
                elif msg_type == "error":
                    # 出错,通知前端弹窗并在本地数据库记录已生成的这部分文本内容
                    if conv_id and streaming_text:
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.conv_manager.add_assistant_message_and_update_tokens(
                            conv_id,
                            streaming_text,
                            thinking
                        )
                        with self.lock:
                            if self.current_conv and self.current_conv.get("id") == conv_id:
                                self.current_conv = self.conv_manager.load_conversation(conv_id)
                        
                    if self.window:
                        js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(msg_data)});"
                        self.window.evaluate_js(js_code)
                    
                    self.set_streaming_done()
                    break
            except Exception as e:
                if self.window:
                    from claude_chat.clients import sanitize_error_message
                    cleaned_err = sanitize_error_message(e)
                    js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(cleaned_err)});"
                    self.window.evaluate_js(js_code)
                self.set_streaming_done()
                break

