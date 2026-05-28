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
        super().__init__(server_address, RequestHandlerClass)


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
            self.send_error(403, "Access Denied / 拒绝访问")
            return
            
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404, "File Not Found / 文件不存在")
            return
            
        mime_type = self.get_mime_type(file_path)
        
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(len(data)))
            # 支持跨域以增强浏览器端调试灵活性
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            logger.error(f"渲染静态资源文件出错 {file_path}: {e}")
            self.send_error(500, "Internal Server Error / 服务器内部错误")

    def read_json_body(self):
        """
        解析 POST 请求中的 JSON 请求体数据
        """
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            return {}
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
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(res_bytes)
        except Exception as e:
            logger.error(f"发送 JSON 响应失败: {e}")

    def do_OPTIONS(self):
        """
        处理 CORS 跨域预检请求，使本地局域网其他设备能够平滑跨域调用本服务 API
        """
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """
        分发 GET 请求：如果是 /api/ 路径则路由到 API 函数，否则由静态服务器处理
        """
        path = self.path.split('?')[0] # 剥离查询参数
        
        if path.startswith("/api/"):
            self.handle_api_get(path)
        else:
            self.serve_static(path)

    def do_POST(self):
        """
        分发 POST 请求
        """
        path = self.path.split('?')[0]
        if path.startswith("/api/"):
            self.handle_api_post(path)
        else:
            self.send_error(404)

    def do_DELETE(self):
        """
        分发 DELETE 请求
        """
        path = self.path.split('?')[0]
        if path.startswith("/api/"):
            self.handle_api_delete(path)
        else:
            self.send_error(404)

    def handle_api_get(self, path):
        """
        处理所有 GET 类型的 API 路由
        """
        # GET /api/config -> 读取当前配置
        if path == "/api/config":
            config_data = dict(self.server.api.get_config())
            # 如果未开启跨端共享密钥，则向浏览器端脱敏 API Key
            if not self.server.app.config.get("sync_config_to_web", True):
                config_data["api_key"] = ""
            self.send_json_response(config_data)
            
        # GET /api/models -> 读取可用模型列表
        elif path == "/api/models":
            self.send_json_response(self.server.api.fetch_models())
            
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
                index = int(parts[-1])
                self.send_json_response(self.server.api.get_message_packet(conv_id, index))
            else:
                self.send_error(400, "Bad Request / 参数错误")
                
        # GET /api/get_logs -> 拉取本地最新的系统日志
        elif path == "/api/get_logs":
            logs = self.server.api.get_logs()
            self.send_json_response(logs)
            
        # GET /api/console_stream/<process_id> -> SSE 形式推送终端进程的输出数据
        elif path.startswith("/api/console_stream/"):
            proc_id = path.split("/")[-1]
            self.handle_console_stream(proc_id)
            
        else:
            self.send_error(404, "Endpoint Not Found / 接口不存在")

    def handle_api_post(self, path):
        """
        处理所有 POST 类型的 API 路由
        """
        body = self.read_json_body()
        
        # POST /api/save_config -> 保存新配置
        if path == "/api/save_config":
            if not self.server.app.config.get("sync_config_to_web", True):
                body["api_key"] = self.server.app.config.get("api_key")
            success = self.server.api.save_config(body)
            self.send_json_response({"success": success})
            
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
            self.send_error(404, "Endpoint Not Found / 接口不存在")

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
            self.send_error(404, "Endpoint Not Found / 接口不存在")

    def handle_streaming_generation(self, action, *args):
        """
        核心流式输出数据分发器。
        使用一个局域队列 `q` 订阅 API 客户端后台线程的生成事件，
        然后将队列中读取到的消息块实时通过 HTTP 响应流 (分块传输编码) 写回前端。
        """
        q = queue.Queue()
        
        # 触发对应的 WebAPI 接口函数在后台线程建立 Anthropic 生成流
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
            self.send_error(400, "Invalid action / 无效操作")
            return
            
        if not success:
            self.send_json_response({"error": "Failed to start generation / 无法开启流生成通道"}, status=500)
            return
            
        # 发送流式 HTTP 响应头
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
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
                    # 流接收成功，将最终结果及 Token 消耗统计数据序列化持久写入 SQLite 数据库
                    if self.server.api._app.current_conv:
                        self.server.api._app.current_conv["input_tokens"] = msg_data.get("input_tokens", 0)
                        self.server.api._app.current_conv["output_tokens"] = msg_data.get("output_tokens", 0)
                        self.server.api._app.current_conv["messages"].append({
                            "role": "assistant",
                            "content": streaming_text,
                            "thinking": streaming_thinking_text if streaming_thinking_text else None
                        })
                        if len(self.server.api._app.current_conv["messages"]) == 2:
                            title = streaming_text[:30].replace("\n", " ")
                            self.server.api._app.current_conv["title"] = title or "新对话"
                        self.server.api._app.current_conv["updated_at"] = datetime.now().isoformat()
                        self.server.api._app.conv_manager.save_conversation(self.server.api._app.current_conv)
                    self.server.api._app.is_streaming = False
                    break
                elif msg_type == "aborted":
                    # 被强行中止，仍对已生成的内容作局部保存存档
                    if self.server.api._app.current_conv:
                        self.server.api._app.current_conv["messages"].append({
                            "role": "assistant",
                            "content": streaming_text,
                            "thinking": streaming_thinking_text if streaming_thinking_text else None,
                            "aborted": True
                        })
                        if len(self.server.api._app.current_conv["messages"]) == 2:
                            title = streaming_text[:30].replace("\n", " ")
                            self.server.api._app.current_conv["title"] = title or "新对话"
                        self.server.api._app.current_conv["updated_at"] = datetime.now().isoformat()
                        self.server.api._app.conv_manager.save_conversation(self.server.api._app.current_conv)
                    self.server.api._app.is_streaming = False
                    break
                elif msg_type == "error":
                    # 发生错误，将错误提示推回，并对当前已生成文本妥善存档
                    if self.server.api._app.current_conv and streaming_text:
                        self.server.api._app.current_conv["messages"].append({
                            "role": "assistant",
                            "content": streaming_text
                        })
                        self.server.api._app.current_conv["updated_at"] = datetime.now().isoformat()
                        self.server.api._app.conv_manager.save_conversation(self.server.api._app.current_conv)
                    self.server.api._app.is_streaming = False
                    break
            except queue.Empty:
                logger.warning("模型流输出队列超时。")
                self.server.api._app.is_streaming = False
                break
            except Exception as e:
                logger.error(f"推送流事件数据时出错: {e}")
                self.server.api._app.is_streaming = False
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
        
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        try:
            while True:
                # 阻塞读取进程读写线程写入的数据
                event = q.get()
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
        return None
        
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    
    local_ip = get_local_ip()
    logger.info(f"本地 HTTP Web 服务器成功开启并运行！")
    logger.info(f" - 本地访问 URL: http://localhost:{port}")
    logger.info(f" - 局域网访问 URL: http://{local_ip}:{port} (允许同网络内其他手机/电脑访问)")
    
    return port
