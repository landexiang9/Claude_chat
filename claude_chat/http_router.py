"""Validated HTTP API routing separated from transport handling."""

from urllib.parse import parse_qs, urlparse

from claude_chat.services.attachment_store import validate_preview_id


def validate_http_attachment_paths(attachments):
    """
    校验 HTTP 请求只能引用先前由本服务创建的受管附件 ID。

    保留旧函数名以兼容已有导入，但本边界不再接受任何客户端文件路径。
    """
    if not isinstance(attachments, list):
        raise ValueError("attachments 必须为数组")
    if not attachments:
        return
    for att in attachments:
        if not isinstance(att, dict):
            raise ValueError("附件信息格式无效")
        if "path" in att or "file_path" in att:
            raise ValueError("HTTP 附件参数不接受本地路径")
        validate_preview_id(att.get("preview_id"))


class HttpApiRouter:
    """Validate endpoint parameters and dispatch requests to domain services."""

    def __init__(self, handler):
        self._handler = handler

    def __getattr__(self, name):
        # Response writing and stream transport remain owned by the HTTP handler.
        return getattr(self._handler, name)

    def dispatch_get(self, path):
        """
        处理所有 GET 类型的 API 路由
        """
        # GET /api/config -> 读取当前配置 (已在 WebAPI 层完成 API Key 脱敏)
        if path == "/api/config":
            config_data = dict(self.server.api.get_config())
            self.send_json_response(config_data)

        # GET /api/models -> 读取可用模型列表
        elif path == "/api/models":
            query = parse_qs(urlparse(self.path).query)
            requested_platform = query.get("platform", [None])[0]
            self.send_json_response(self.server.api.fetch_models(requested_platform))

        # GET /api/check_parsers -> 检查本地可选解析依赖库安装状态
        elif path == "/api/check_parsers":
            self.send_json_response(self.server.api.check_parsers())

        # GET /api/code_sandbox_environment -> 检查安全代码执行后端及运行环境
        elif path == "/api/code_sandbox_environment":
            self.send_json_response(self.server.api.check_code_sandbox_environment())

        # GET /api/conversations -> 获取历史对话列表（无具体内容，仅展示列表）
        elif path == "/api/conversations":
            self.send_json_response(self.server.api.load_conversations())

        # GET /api/conversation/<id> -> 加载指定的某个对话及消息详情
        elif path.startswith("/api/conversation/"):
            conv_id = path.split("/")[-1]
            if not conv_id:
                self.send_error(400, "Bad Request")
                return
            self.send_json_response(self.server.api.load_conversation(conv_id))

        # GET /api/message_packet/<conv_id>/<index> -> 获取对话中某条消息的数据库原始记录和发送给 API 的 Payload
        elif path.startswith("/api/message_packet/"):
            parts = path.split("/")
            if len(parts) == 5 and parts[-2]:
                conv_id = parts[-2]
                # M-fix#31: 索引段可能非数字(如 /abc),int 会抛 ValueError 漏到顶层变 500,补 400。
                try:
                    index = int(parts[-1])
                except (ValueError, TypeError):
                    self.send_error(400, "Bad Request")
                    return
                if index < 0:
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
            if not proc_id:
                self.send_error(400, "Bad Request")
                return
            self.handle_console_stream(proc_id)

        # GET /api/custom_providers -> 获取自定义模型提供商列表
        elif path == "/api/custom_providers":
            self.send_json_response(self.server.api.list_custom_providers())

        else:
            self.send_error(404, "Endpoint Not Found")

    def dispatch_post(self, path):
        """
        处理所有 POST 类型的 API 路由
        """
        body = self.read_json_body()
        if not isinstance(body, dict):
            self.send_error(400, "JSON object required")
            return

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

        # POST /api/update_model_registry -> 手动刷新模型能力注册表
        elif path == "/api/update_model_registry":
            self.send_json_response(self.server.api.update_model_registry())

        # POST /api/install_code_sandbox_environment -> 拉取 Linux Docker 运行时镜像
        elif path == "/api/install_code_sandbox_environment":
            self.send_json_response(self.server.api.install_code_sandbox_environment())

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

        # POST /api/upload_dropped_file -> 处理前端拖拽上传并保存到受管附件目录
        elif path == "/api/upload_dropped_file":
            name = body.get("name")
            size = body.get("size")
            base64_data = body.get("base64_data")
            uploaded = self.server.api.upload_dropped_file(name, size, base64_data)
            self.send_json_response(uploaded)

        # POST /api/attachment_preview -> 获取严格限流的图片或文本附件预览
        elif path == "/api/attachment_preview":
            preview = self.server.api.get_attachment_preview(body)
            self.send_json_response(preview, status=400 if preview.get("error") else 200)

        # POST /api/discard_pending_attachment -> 清理尚未被消息引用的受管附件
        elif path == "/api/discard_pending_attachment":
            result = self.server.api.discard_pending_attachment(body.get("preview_id"))
            self.send_json_response(result, status=400 if result.get("error") else 200)

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
            # 安全边界:HTTP 模式只接受服务签发的随机受管附件 ID。
            validate_http_attachment_paths(attachments)
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

    def dispatch_delete(self, path):
        """
        处理所有 DELETE 类型的 API 路由
        """
        # DELETE /api/conversation/<id> -> 删除某个对话记录
        if path.startswith("/api/conversation/"):
            conv_id = path.split("/")[-1]
            if not conv_id:
                self.send_error(400, "Bad Request")
                return
            success = self.server.api.delete_conversation(conv_id)
            self.send_json_response({"success": success})
        else:
            self.send_error(404, "Endpoint Not Found")
