import json
import os
import queue
import re
import socket
import socketserver
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime

import logging
logger = logging.getLogger("claude_chat")

def get_local_ip():
    """
    获取本机在局域网 (LAN) 中的 IP 地址，用于多设备跨端访问时显示正确的连接 URL。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不需要实际建立连接，只需探测本地路由路径
        s.connect(('8.8.8.8', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """
    支持多线程并发处理连接的 HTTP 服务器，防止静态资源加载或大流量 API 请求阻塞主循环。
    """
    daemon_threads = True
    
    def __init__(self, server_address, RequestHandlerClass, app, api):
        self.app = app
        self.api = api
        self.ssl_context = None
        super().__init__(server_address, RequestHandlerClass)

    def finish_request(self, request, client_address):
        """
        在工作线程中处理请求。如果启用了 SSL/HTTPS：
        1. 使用 MSG_PEEK 探测首个字节，如果不是 0x16（TLS ClientHello），则认为是普通 HTTP 或恶意扫描，直接丢弃。
        2. 如果是合法 TLS，则在工作线程中进行 wrap_socket 握手，规避主线程 accept 阻塞。
        3. 对所有连接设置合理的 socket 超时时间，防止 slowloris 等攻击挂起线程。
        """
        if getattr(self, 'ssl_context', None):
            try:
                # 设置握手探测短期超时
                request.settimeout(10.0)
                
                # 使用 MSG_PEEK 探测首个字节
                # TLS ClientHello 必须以 0x16 (Handshake 记录类型) 开头
                try:
                    first_byte = request.recv(1, socket.MSG_PEEK)
                except Exception as peek_err:
                    logger.debug(f"探测来自 {client_address} 的连接首字节失败: {peek_err}")
                    first_byte = b""
                
                if not first_byte or first_byte[0] != 0x16:
                    logger.debug(f"来自 {client_address} 的非 TLS/SSL 连接（可能是普通 HTTP 或扫描器），直接丢弃该请求。")
                    try:
                        request.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    try:
                        request.close()
                    except Exception:
                        pass
                    return
                
                # 开始进行 SSL/TLS 握手
                wrapped_socket = self.ssl_context.wrap_socket(request, server_side=True)
                # 握手成功，设置 HTTP 请求处理的超时时间
                wrapped_socket.settimeout(30.0)
                request = wrapped_socket
            except Exception as e:
                logger.debug(f"与 {client_address} 进行 SSL 握手失败: {e}")
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    request.close()
                except Exception:
                    pass
                return
        else:
            # 普通 HTTP 模式，设置超时时间防止线程卡死在 recv() 上
            request.settimeout(30.0)
            
        super().finish_request(request, client_address)


class ClaudeChatHTTPHandler(BaseHTTPRequestHandler):
    """
    HTTP 请求处理器，负责：
    1. 托管静态 UI 资源文件 (index.html, style.css, app.js, fonts, libs 等)。
    2. 处理前后端交互的 RESTful API。
    3. 管道转发模型的流式响应事件 (Server-Sent Event 简易实现)。
    4. 管道转发本地代码终端执行的实时输出流。
    """
    # 禁用默认的每一条请求日志终端输出，保持控制台整洁（统一记入 log 文件）
    def log_message(self, format, *args):
        logger.debug(f"HTTP 请求: {format % args}")

    def _send_cors_headers(self):
        """
        根据请求中的 Origin 报头动态设置 CORS 响应头，
        只允许来自本地信任环回地址 (localhost, 127.0.0.1, [::1]) 或相同 Origin 的局域网跨域请求，
        彻底拒绝第三方恶意站点的跨域嗅探与 CSRF 代码执行漏洞。
        """
        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlparse
            try:
                parsed_origin = urlparse(origin)
                hostname = parsed_origin.hostname
                host_header = self.headers.get("Host", "")
                
                # 同源检查 (Host 匹配 Origin Netloc)
                is_same_origin = (parsed_origin.netloc == host_header)
                # 环回地址/本机信任域检查
                is_loopback = hostname in ("localhost", "127.0.0.1", "[::1]", "::1")
                
                if is_same_origin or is_loopback:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Credentials", "true")
            except Exception:
                pass

    def get_mime_type(self, file_path):
        """
        根据文件后缀获取相应的 MIME Content-Type。
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
            ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"
        }
        return mime_map.get(ext, "application/octet-stream")

    def serve_static(self, rel_path):
        """
        静态文件服务器实现：读取 UI 静态文件并返回客户端，包含安全目录沙盒校验。
        """
        ui_dir = Path(__file__).parent / "ui"
        
        # 根路径默认重定向到 index.html
        if not rel_path or rel_path == "/":
            rel_path = "index.html"
        else:
            rel_path = rel_path.lstrip("/")
            
        file_path = (ui_dir / rel_path).resolve()
        
        # 安全性校验：确保请求的文件绝对在 ui_dir 目录下，防止路径跨越攻击（如 ../../..）
        try:
            file_path.relative_to(ui_dir.resolve())
        except ValueError:
            self.send_error(403, "Access Denied")
            return
            
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "File Not Found")
            return
            
        mime_type = self.get_mime_type(file_path)
        
        try:
            # 动态替换 index.html 中的本地库为 CDN
            if file_path.name == "index.html" and self.server.app.config.get("use_cdn_assets", False):
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                content = content.replace('src="libs/marked.min.js"', 'src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"')
                content = content.replace('src="libs/purify.min.js"', 'src="https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js"')
                content = content.replace('src="libs/highlight.min.js"', 'src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"')
                content = content.replace('src="libs/mermaid.min.js"', 'src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"')
                content = content.replace('href="libs/github-dark.min.css"', 'href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css"')
                
                data = content.encode('utf-8')
                self.send_response(200)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Length", str(len(data)))
                self._send_cors_headers()
                self.send_header("Referrer-Policy", "same-origin")
                self.end_headers()
                self.wfile.write(data)
                return

            file_size = file_path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self._send_cors_headers()
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            
            # 流式分块发送，避免内存飙升，提高弱网环境传输稳定性
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except ConnectionError as e:
            logger.debug(f"客户端提前断开连接，取消发送资源 {file_path}: {e}")
        except Exception as e:
            logger.error(f"渲染静态资源文件出错 {file_path}: {e}")
            try:
                self.send_error(500, "Internal Server Error")
            except Exception:
                pass

    def read_json_body(self):
        """
        解析 POST 请求中的 JSON 请求体数据，带有请求大小上限检查，防御 OOM 攻击
        """
        # 限制请求体最大为 50MB，防御超大 payload 导致的 OOM 攻击
        # M-fix#7: 用 <= 0 拒绝负值(Content-Length: -1 经 read(-1) 会被当成"读到 EOF",
        # 绕过上限耗尽内存);非法/缺省头统一视作 0。
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            return {}
        if content_length <= 0:
            return {}
        if content_length > 50 * 1024 * 1024:
            raise ValueError("Payload too large")
            
        body = self.rfile.read(content_length).decode('utf-8')
        try:
            return json.loads(body)
        except Exception:
            return {}

    def send_json_response(self, data, status=200):
        """
        发送 JSON 格式的 HTTP 响应
        """
        try:
            res_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res_bytes)))
            self._send_cors_headers()
            self.send_header("Referrer-Policy", "same-origin")
            self.end_headers()
            self.wfile.write(res_bytes)
        except Exception as e:
            logger.error(f"发送 JSON 响应失败: {e}")

    def do_OPTIONS(self):
        """
        处理 CORS 跨域预检请求，使本地局域网其他设备能够平滑跨域调用本服务 API
        """
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Security-Token, Authorization")
        self.end_headers()

    def is_request_authorized(self):
        """
        验证请求是否已授权。
        所有对 /api/ 接口的请求（包括本地环回）均须在 Header 中提供 X-Security-Token，
        或在 URL 参数中传入 ?token=<token>，以此防御跨站请求伪造 (CSRF) 及未授权的本地命令执行 (RCE)。
        """
        server_token = self.server.app.config.get("security_token", "")
        if not server_token:
            return False
            
        # 1. 尝试从 Header 获取
        token = self.headers.get("X-Security-Token")
        if not token:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                
        # 2. 尝试从 URL 查询参数获取
        if not token:
            match = re.search(r'[?&]token=([^&]+)', self.path)
            if match:
                token = match.group(1)
                
        # 使用 hmac.compare_digest 进行常量时间比较，防止计时攻击
        import hmac
        return isinstance(token, str) and hmac.compare_digest(token, server_token)

    def send_unauthorized_response(self):
        """
        发送 401 未授权响应
        """
        self.send_json_response({"error": "Unauthorized / 未授权访问，请提供有效的安全 Token"}, status=401)

    def do_GET(self):
        """
        分发 GET 请求：如果是 /api/ 路径则路由到 API 函数，否则由静态服务器处理
        """
        path = self.path.split('?')[0] # 剥离查询参数
        
        if path.startswith("/api/"):
            if not self.is_request_authorized():
                self.send_unauthorized_response()
                return
            self.handle_api_get(path)
        else:
            self.serve_static(path)

    def do_POST(self):
        """
        分发 POST 请求
        """
        path = self.path.split('?')[0]
        if path.startswith("/api/"):
            if not self.is_request_authorized():
                self.send_unauthorized_response()
                return
            try:
                self.handle_api_post(path)
            except ValueError as ve:
                if str(ve) == "Payload too large":
                    self.send_error(413, "Payload Too Large")
                else:
                    self.send_error(400, "Bad Request")
        else:
            self.send_error(404)

    def do_DELETE(self):
        """
        分发 DELETE 请求
        """
        path = self.path.split('?')[0]
        if path.startswith("/api/"):
            if not self.is_request_authorized():
                self.send_unauthorized_response()
                return
            self.handle_api_delete(path)
        else:
            self.send_error(404)

    def handle_api_get(self, path):
        """
        处理所有 GET 类型的 API 路由
        """
        # GET /api/config -> 读取当前配置 (已在 WebAPI 层完成 API Key 脱敏)
        if path == "/api/config":
            config_data = dict(self.server.api.get_config())
            self.send_json_response(config_data)
            
        # GET /api/models -> 读取可用模型列表
        elif path == "/api/models":
            self.send_json_response(self.server.api.fetch_models())
            
        # GET /api/check_parsers -> 检查本地可选解析依赖库安装状态
        elif path == "/api/check_parsers":
            self.send_json_response(self.server.api.check_parsers())
            
        # GET /api/conversations -> 获取历史对话列表（无具体内容，仅展示列表）
        elif path == "/api/conversations":
            self.send_json_response(self.server.api.load_conversations())
            
        # GET /api/conversation/<id> -> 加载指定的某个对话及消息详情
        elif path.startswith("/api/conversation/"):
            conv_id = path.split("/")[-1]
            self.send_json_response(self.server.api.load_conversation(conv_id))
            
        # GET /api/message_packet/<conv_id>/<index> -> 获取对话中某条消息的数据库原始记录和发送给 API 的 Payload
        elif path.startswith("/api/message_packet/"):
            parts = path.split("/")
            if len(parts) >= 5:
                conv_id = parts[-2]
                # M-fix#31: 索引段可能非数字(如 /abc),int 会抛 ValueError 漏到顶层变 500,补 400。
                try:
                    index = int(parts[-1])
                except (ValueError, TypeError):
                    self.send_error(400, "Bad Request")
                    return
                self.send_json_response(self.server.api.get_message_packet(conv_id, index))
            else:
                self.send_error(400, "Bad Request")
                
        # GET /api/get_logs -> 拉取本地最新的系统日志
        elif path == "/api/get_logs":
            logs = self.server.api.get_logs()
            self.send_json_response(logs)
            
        # GET /api/console_stream/<process_id> -> SSE 形式推送终端进程的输出数据
        elif path.startswith("/api/console_stream/"):
            proc_id = path.split("/")[-1]
            self.handle_console_stream(proc_id)
            
        # GET /api/custom_providers -> 获取自定义模型提供商列表
        elif path == "/api/custom_providers":
            self.send_json_response(self.server.api.list_custom_providers())
            
        else:
            self.send_error(404, "Endpoint Not Found")

    def handle_api_post(self, path):
        """
        处理所有 POST 类型的 API 路由
        """
        body = self.read_json_body()
        
        # POST /api/save_config -> 保存新配置
        if path == "/api/save_config":
            # 禁止通过网络接口篡改 sync_config_to_web 本身的值，防止安全绕过
            if "sync_config_to_web" in body:
                body["sync_config_to_web"] = self.server.app.config.get("sync_config_to_web", True)
                
            if not self.server.app.config.get("sync_config_to_web", True):
                # 隐藏保存通道：若关闭了向 Web 同步，网络端提交的全部敏感凭证在后端强制以本地现有数据覆盖，保证存储隔离
                from claude_chat.config import get_sensitive_api_keys
                keys_to_preserve = get_sensitive_api_keys(self.server.app.config.data)
                for key in keys_to_preserve:
                    body[key] = self.server.app.config.get(key)
                    # 避免触发清除逻辑
                    body.pop(f"clear_{key}", None)
            success = self.server.api.save_config(body)
            self.send_json_response({"success": success})
            
        # POST /api/add_custom_provider -> 新增自定义模型提供商并保存到服务器
        elif path == "/api/add_custom_provider":
            result = self.server.api.add_custom_provider(
                name=body.get("name", ""),
                api_url=body.get("api_url", ""),
                api_key=body.get("api_key", ""),
                models=body.get("models", []),
                temperature=body.get("temperature", 0.7),
                max_tokens=body.get("max_tokens", 4096),
                models_api_url=body.get("models_api_url", "")
            )
            self.send_json_response(result)
            
        # POST /api/update_custom_provider -> 更新自定义提供商元数据 / API Key
        elif path == "/api/update_custom_provider":
            result = self.server.api.update_custom_provider(
                provider_id=body.get("id", ""),
                name=body.get("name"),
                api_url=body.get("api_url"),
                api_key=body.get("api_key"),
                models=body.get("models"),
                temperature=body.get("temperature"),
                max_tokens=body.get("max_tokens"),
                models_api_url=body.get("models_api_url"),
                clear_api_key=body.get("clear_api_key", False)
            )
            self.send_json_response(result)
            
        # POST /api/remove_custom_provider -> 删除自定义提供商
        elif path == "/api/remove_custom_provider":
            result = self.server.api.remove_custom_provider(body.get("id", ""))
            self.send_json_response(result)
            
        # POST /api/new_conversation -> 新建对话
        elif path == "/api/new_conversation":
            self.send_json_response(self.server.api.new_conversation())
            
        # POST /api/upload_dropped_file -> 处理前端拖拽上传的附件文件并保存到临时目录
        elif path == "/api/upload_dropped_file":
            name = body.get("name")
            size = body.get("size")
            base64_data = body.get("base64_data")
            uploaded = self.server.api.upload_dropped_file(name, size, base64_data)
            self.send_json_response(uploaded)
            
        # POST /api/branch_conversation -> 从当前对话中某条消息分裂出一个新分支对话
        elif path == "/api/branch_conversation":
            conv_id = body.get("conv_id")
            msg_index = body.get("msg_index")
            new_conv = self.server.api.branch_conversation(conv_id, msg_index)
            self.send_json_response(new_conv)
            
        # POST /api/send_console_input -> 写入输入到正在执行的代码进程 stdin
        elif path == "/api/send_console_input":
            process_id = body.get("process_id")
            text = body.get("text")
            success = self.server.api.send_console_input(process_id, text)
            self.send_json_response({"success": success})
            
        # POST /api/kill_console_process -> 强制结束正在执行的代码进程
        elif path == "/api/kill_console_process":
            process_id = body.get("process_id")
            success = self.server.api.kill_console_process(process_id)
            self.send_json_response({"success": success})
            
        # POST /api/abort_generation -> 强行中止当前的模型输出流生成
        elif path == "/api/abort_generation":
            success = self.server.api.abort_generation()
            self.send_json_response({"success": success})
            
        # POST /api/clear_logs -> 清空系统日志
        elif path == "/api/clear_logs":
            success = self.server.api.clear_logs()
            self.send_json_response(success)
            
        # POST /api/send_message -> 用户发送消息，走流式响应接口
        elif path == "/api/send_message":
            conv_id = body.get("conv_id")
            text = body.get("text")
            attachments = body.get("attachments", [])
            self.handle_streaming_generation("send_message", conv_id, text, attachments)
            
        # POST /api/edit_and_resend -> 用户修改历史消息并重新发送生成，走流式响应接口
        elif path == "/api/edit_and_resend":
            conv_id = body.get("conv_id")
            msg_index = body.get("msg_index")
            new_content = body.get("new_content")
            self.handle_streaming_generation("edit_and_resend", conv_id, new_content, msg_index)
            
        # POST /api/retry_message -> 重新生成某条 Assistant 消息，走流式响应接口
        elif path == "/api/retry_message":
            conv_id = body.get("conv_id")
            msg_index = body.get("msg_index")
            self.handle_streaming_generation("retry_message", conv_id, msg_index)
            
        # POST /api/start_code_execution -> 在本地安全沙盒环境中异步执行代码块 (JS/Python)
        elif path == "/api/start_code_execution":
            code = body.get("code")
            lang = body.get("lang")
            result = self.server.api.start_code_execution(code, lang)
            self.send_json_response(result)
            
        # POST /api/paste_from_clipboard -> 从主机系统剪贴板读取数据 (主要解决 Webview2 下剪贴板受阻问题)
        elif path == "/api/paste_from_clipboard":
            txt = self.server.api.paste_from_clipboard()
            self.send_json_response(txt)
            
        else:
            self.send_error(404, "Endpoint Not Found")

    def handle_api_delete(self, path):
        """
        处理所有 DELETE 类型的 API 路由
        """
        # DELETE /api/conversation/<id> -> 删除某个对话记录
        if path.startswith("/api/conversation/"):
            conv_id = path.split("/")[-1]
            success = self.server.api.delete_conversation(conv_id)
            self.send_json_response({"success": success})
        else:
            self.send_error(404, "Endpoint Not Found")

    def handle_streaming_generation(self, action, *args):
        """
        核心流式输出数据分发器。
        使用一个局域队列 `q` 订阅 API 客户端后台线程的生成事件，
        然后将队列中读取到的消息块实时通过 HTTP 响应流 (分块传输编码) 写回前端。
        """
        q = queue.Queue()
        
        # 触发对应的 WebAPI 接口函数在后台线程建立 Anthropic 生成流
        conv_id = None
        if action == "send_message":
            conv_id, text, attachments = args
            success = self.server.api.send_message(conv_id, text, attachments, custom_queue=q)
        elif action == "edit_and_resend":
            conv_id, new_content, msg_index = args
            success = self.server.api.edit_and_resend(conv_id, msg_index, new_content, custom_queue=q)
        elif action == "retry_message":
            conv_id, msg_index = args
            success = self.server.api.retry_message(conv_id, msg_index, custom_queue=q)
        else:
            self.send_error(400, "Invalid action")
            return
            
        if not success:
            self.send_json_response({"error": "Failed to start generation / 无法开启流生成通道"}, status=500)
            return
            
        # 发送流式 HTTP 响应头
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self._send_cors_headers()
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        
        streaming_text = ""
        streaming_thinking_text = ""
        
        # 循环读取后台队列直到流结束
        while True:
            try:
                # 60 秒无事件代表网络中断，作安全超时处理
                msg = q.get(timeout=60)
                msg_type, msg_data = msg
                
                # 将每次获得的事件编码为一行标准的 JSON 数据推回前端
                line = json.dumps({"type": msg_type, "data": msg_data}, ensure_ascii=False) + "\n"
                self.wfile.write(line.encode('utf-8'))
                self.wfile.flush()
                
                if msg_type == "text":
                    streaming_text += msg_data
                elif msg_type == "thinking":
                    streaming_thinking_text += msg_data
                elif msg_type == "done":
                    # 增量安全写入 AI 响应及 Token，在首轮对话自动生成标题，规避并发覆盖冲突
                    input_tokens = msg_data.get("input_tokens", 0)
                    output_tokens = msg_data.get("output_tokens", 0)
                    thinking = streaming_thinking_text if streaming_thinking_text else None
                    self.server.api._app.conv_manager.add_assistant_message_and_update_tokens(
                        conv_id, 
                        streaming_text, 
                        thinking, 
                        input_tokens, 
                        output_tokens
                    )
                    
                    # 仅当内存中当前选中的对话 ID 仍匹配时才从 DB 重新加载进行同步，防状态污染
                    # M-fix#20: 读-检查-写 current_conv 须持 app.lock,与 api_bridge 各持锁写入互斥,避免 TOCTOU 覆盖
                    with self.server.api._app.lock:
                        curr = self.server.api._app.current_conv
                        if curr and curr.get("id") == conv_id:
                            self.server.api._app.current_conv = self.server.api._app.conv_manager.load_conversation(conv_id)

                    self.server.api._app.set_streaming_done()
                    break
                elif msg_type == "aborted":
                    # 被强行中止，增量对已生成的内容作局部保存存档
                    thinking = streaming_thinking_text if streaming_thinking_text else None
                    self.server.api._app.conv_manager.add_assistant_message_and_update_tokens(
                        conv_id,
                        streaming_text,
                        thinking,
                        aborted=True
                    )

                    with self.server.api._app.lock:
                        curr = self.server.api._app.current_conv
                        if curr and curr.get("id") == conv_id:
                            self.server.api._app.current_conv = self.server.api._app.conv_manager.load_conversation(conv_id)

                    self.server.api._app.set_streaming_done()
                    break
                elif msg_type == "error":
                    # 发生错误，将当前已生成文本增量妥善存档
                    if streaming_text:
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.server.api._app.conv_manager.add_assistant_message_and_update_tokens(
                            conv_id,
                            streaming_text,
                            thinking
                        )
                        with self.server.api._app.lock:
                            curr = self.server.api._app.current_conv
                            if curr and curr.get("id") == conv_id:
                                self.server.api._app.current_conv = self.server.api._app.conv_manager.load_conversation(conv_id)

                    self.server.api._app.set_streaming_done()
                    break
            except queue.Empty:
                logger.warning("模型流输出队列超时。")
                # M-fix#19: 超时不能直接丢已累积的流式文本;按 error 模式增量存档并停止后端生成线程
                if streaming_text:
                    try:
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.server.api._app.conv_manager.add_assistant_message_and_update_tokens(
                            conv_id, streaming_text, thinking
                        )
                    except Exception as save_err:
                        logger.error(f"超时时存档部分响应失败: {save_err}")
                try:
                    self.server.api._app.abort_generation()
                except Exception:
                    pass
                self.server.api._app.set_streaming_done()
                break
            except Exception as e:
                logger.error(f"推送流事件数据时出错: {e}")
                # M-fix#19: 同上,异常/客户端断开时也存档已生成文本并停止后台线程
                if streaming_text:
                    try:
                        thinking = streaming_thinking_text if streaming_thinking_text else None
                        self.server.api._app.conv_manager.add_assistant_message_and_update_tokens(
                            conv_id, streaming_text, thinking
                        )
                    except Exception as save_err:
                        logger.error(f"异常时存档部分响应失败: {save_err}")
                try:
                    self.server.api._app.abort_generation()
                except Exception:
                    pass
                self.server.api._app.set_streaming_done()
                break

    def handle_console_stream(self, process_id):
        """
        终端实时控制台输出流处理。
        利用队列和监听器机制订阅终端进程的 stdout/stderr 打印并管道实时输出给前端浏览器。
        """
        q = queue.Queue()
        
        # 消息接收回调
        def callback(event):
            if event.get("proc_id") == process_id:
                q.put(event)
                
        # 挂载到全局应用监听列表中
        self.server.app.console_listeners.append(callback)

        # M-fix#21: 连接前先校验进程是否仍存活,已退出者直接 404,避免线程在无事件的
        # queue.get() 上永久阻塞、监听器累积泄漏。
        with self.server.app.process_lock:
            process_exists = process_id in self.server.app.active_processes
        if not process_exists:
            if callback in self.server.app.console_listeners:
                self.server.app.console_listeners.remove(callback)
            self.send_error(404, "Process Not Found")
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self._send_cors_headers()
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()

        try:
            while True:
                # 带超时的阻塞读取:进程已退出且无后续事件时也能及时退出,防止线程泄漏
                try:
                    event = q.get(timeout=5)
                except queue.Empty:
                    # 若进程已不在活跃表中且队列空,判定为已退出的陈旧连接,安全退出
                    with self.server.app.process_lock:
                        if process_id not in self.server.app.active_processes:
                            break
                    continue
                line = json.dumps(event, ensure_ascii=False) + "\n"
                self.wfile.write(line.encode('utf-8'))
                self.wfile.flush()

                # 如果进程退出，则终止该推送流连接
                if event.get("stream") == "exit":
                    break
        except Exception as e:
            logger.error(f"推送终端输出流出错: {e}")
        finally:
            # 链接断开后注销该监听器
            if callback in self.server.app.console_listeners:
                self.server.app.console_listeners.remove(callback)


def start_server(app, api, host='0.0.0.0', start_port=8000):
    """
    在后台守护线程中启动多线程 HTTP Web/API 服务器。
    如果在 start_port 绑定失败，则会依次递增尝试后续端口（至多递增尝试 100 次）。
    支持通过 host 绑定不同的网卡接口，传入 '0.0.0.0' 代表允许在局域网内任意客户端发起连接。
    """
    port = start_port
    server = None
    while port < start_port + 100:
        try:
            server = ThreadingHTTPServer((host, port), ClaudeChatHTTPHandler, app, api)
            break
        except socket.error:
            logger.warning(f"端口 {port} 已被占用，正在尝试下一个端口...")
            port += 1
            
    if not server:
        logger.error("在指定范围内的全部本地端口均无法成功绑定 HTTP 服务。")
        return None, False

    # 动态检测并配置 SSL 传输通道（可选）
    enable_ssl = app.config.get("enable_ssl", False)
    is_ssl = False
    if enable_ssl:
        try:
            import ssl
            import subprocess
            import shutil
            from claude_chat.config import BASE_DIR
            
            crt_path = BASE_DIR / "server.crt"
            key_path = BASE_DIR / "server.key"
            
            # 若证书不存在，尝试使用系统 openssl CLI 工具自动生成
            if not crt_path.exists() or not key_path.exists():
                if shutil.which("openssl") is not None:
                    try:
                        logger.info("检测到系统存在 openssl 命令行工具，正在自动生成自签名 SSL 证书...")
                        subprocess.run([
                            "openssl", "req", "-new", "-x509", "-days", "365", "-nodes",
                            "-out", str(crt_path),
                            "-keyout", str(key_path),
                            "-subj", "/C=CN/CN=localhost"
                        ], check=True, capture_output=True)
                        logger.info("自签名 SSL 证书 (server.crt/server.key) 生成成功。")
                    except Exception as e:
                        logger.warning(f"使用 openssl 工具生成证书失败: {e}")
                else:
                    logger.warning("系统未检测到 openssl 命令行工具，无法自动生成自签名证书。")
            
            if crt_path.exists() and key_path.exists():
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certfile=str(crt_path), keyfile=str(key_path))
                server.ssl_context = context
                is_ssl = True
                logger.info("SSL/HTTPS 传输通道已成功启用！")
            else:
                logger.warning("由于缺失 SSL 证书文件且无法自动生成，将回退至普通 HTTP 模式。")
        except Exception as ssl_err:
            logger.error(f"启用 SSL 传输通道失败，回退至普通 HTTP 模式: {ssl_err}")
         
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    local_ip = get_local_ip()
    token = app.config.get("security_token", "")
    protocol = "https" if is_ssl else "http"
    token_masked = token[:4] + "***" + token[-4:] if len(token) > 8 else "***"
    logger.info(f"本地 HTTP Web 服务器成功开启并运行！")
    logger.info(f" - 安全验证 Token: {token_masked} (完整 Token 仅输出至控制台，不写入日志文件)")
    
    # 完整访问地址使用 print() 仅输出到控制台，避免持久化存入日志文件泄露
    print(f"===================================================")
    print(f" - 完整安全 Token: {token}")
    print(f" - 本地访问 URL: {protocol}://localhost:{port}/?token={token}")
    print(f" - 局域网访问 URL: {protocol}://{local_ip}:{port}/?token={token} (允许同网络内其他手机/电脑访问)")
    print(f"===================================================")
    
    return port, is_ssl

