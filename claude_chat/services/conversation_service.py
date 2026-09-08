import json
import logging
import mimetypes
import re
import threading
from datetime import datetime
from pathlib import Path

from claude_chat.clients import extract_api_message, stream_claude_response
from claude_chat.config import (
    DEFAULT_MAX_TOKENS,
    FALLBACK_MODELS,
    IMAGE_EXTENSIONS,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_ATTACHMENT_EXTENSIONS,
    find_custom_provider,
)
from claude_chat.db import deserialize_content
from claude_chat.platform_params import PlatformParamMapper
from claude_chat.provider_adapters import adapter_accepts_attachment, normalize_custom_provider_adapter
from claude_chat.services.base import AppService
from claude_chat.services.attachment_store import migrate_legacy_conversation_attachments
from claude_chat.stream_protocol import StreamTaskState, is_stream_error_message

logger = logging.getLogger("claude_chat")

LEGACY_ATTACHMENT_TEXT_RE = re.compile(
    r"^\s*---\s*附件文件:\s*(.+?)\s*---\s*(?:\r?\n)[\s\S]*?(?:\r?\n)---\s*附件结束\s*---\s*$"
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

def read_text_file(file_path):
    """
    尝试以不同的编码格式读取文本文件。
    优先采用 UTF-8 编码，失败时尝试 GBK，最后使用 UTF-8（对无法解码的字符进行替换）。
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, "r", encoding="gbk") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()


def prepare_attachment_content(att, platform, config):
    """Validate one attachment and convert it into a persisted message block."""
    if not isinstance(att, dict):
        raise ValueError("附件信息格式无效")
    if "path" in att or "file_path" in att:
        raise ValueError("附件发送不接受本地路径")

    from claude_chat.services.attachment_store import load_managed_attachment

    path, trusted_metadata = load_managed_attachment(att.get("preview_id"))
    display_name = trusted_metadata["name"]
    file_size = trusted_metadata["size"]
    ext = Path(display_name).suffix.lower()
    if ext not in SUPPORTED_ATTACHMENT_EXTENSIONS:
        raise ValueError(f"不支持的附件格式: {display_name}")

    mime = trusted_metadata["media_type"]
    if ext in IMAGE_EXTENSIONS:
        attachment_kind = "image"
    elif ext in PDF_EXTENSIONS or ext in OFFICE_EXTENSIONS:
        attachment_kind = "document"
    elif mime.startswith("text/"):
        attachment_kind = "text"
    else:
        attachment_kind = "file"
    attachment_metadata = {
        "preview_id": trusted_metadata["preview_id"],
        "preview_available": True,
        "name": display_name,
        "size": file_size,
        "media_type": mime,
        "kind": attachment_kind,
    }
    custom_provider = None
    if str(platform).startswith("custom:"):
        config_data = getattr(config, "data", None)
        if not isinstance(config_data, dict):
            config_data = getattr(config, "values", {})
        custom_provider = find_custom_provider(config_data, platform) or {}
    custom_adapter = normalize_custom_provider_adapter(
        custom_provider.get("provider_adapter") if custom_provider else None,
        custom_provider.get("file_upload_enabled", False) if custom_provider else False,
    )
    builtin_upload_enabled = {
        "claude": bool(config.get("claude_file_upload_enabled", True)),
        "deepseek": bool(config.get("deepseek_file_upload_enabled", True)),
        "gemini": bool(config.get("gemini_file_upload_enabled", True)),
    }.get(platform, False)
    # Office formats are not accepted as native message documents by the three
    # providers. DeepSeek's Files API currently accepts images only. Custom
    # OpenAI-compatible providers retain the previous parser fallback unless the
    # user explicitly enables their Files API setting.
    needs_text_extraction = ext in OFFICE_EXTENSIONS or (
        platform == "deepseek" and ext in PDF_EXTENSIONS
    ) or (
        str(platform).startswith("custom:")
        and not adapter_accepts_attachment(custom_adapter, ext)
        and ext in IMAGE_EXTENSIONS | PDF_EXTENSIONS
    ) or (
        platform in {"claude", "gemini"} and not builtin_upload_enabled and ext in IMAGE_EXTENSIONS | PDF_EXTENSIONS
    )

    if needs_text_extraction:
        from claude_chat.attachment_parser import parse_attachment_to_markdown

        parsed = parse_attachment_to_markdown(
            {"path": str(path), "name": display_name, "size": file_size},
            ocr_mode=config.get("ocr_mode", "auto"),
            cloud_provider=config.get("ocr_cloud_model", "gemini"),
            api_key=config.get("gemini_api_key", ""),
            proxy_mode=config.get("proxy_mode", "system"),
            proxy_url=config.get("proxy_url", ""),
        )
        return {
            "type": "text",
            "text": f"\n\n--- 附件文件: {display_name} ---\n{parsed}\n--- 附件结束 ---",
            "_attachment": attachment_metadata,
        }

    if ext in IMAGE_EXTENSIONS:
        return {
            "type": "image",
            "source": {"file_path": str(path), "media_type": mime},
            "_attachment": attachment_metadata,
        }
    if ext in PDF_EXTENSIONS:
        return {
            "type": "document",
            "source": {"file_path": str(path), "media_type": "application/pdf"},
            "_attachment": attachment_metadata,
        }

    file_text = read_text_file(path)
    return {
        "type": "text",
        "text": f"\n\n--- 附件文件: {display_name} ---\n{file_text}\n--- 附件结束 ---",
        "_attachment": attachment_metadata,
    }


def is_attachment_content_block(block):
    """Return whether a persisted content block represents a user attachment."""
    if not isinstance(block, dict):
        return False
    if isinstance(block.get("_attachment"), dict):
        return True
    if block.get("type") in {"image", "document", "file", "audio", "video"}:
        return True
    return block.get("type") == "text" and bool(LEGACY_ATTACHMENT_TEXT_RE.fullmatch(str(block.get("text", ""))))


def messages_for_api(messages):
    """Convert stored history while excluding local-only stream error records."""
    return [
        extract_api_message(message, preserve_file_paths=True)
        for message in messages
        if not is_stream_error_message(message)
    ]


def conversation_for_frontend(conversation):
    """Return a bounded DTO containing attachment metadata but no local payloads."""
    if not isinstance(conversation, dict):
        return conversation

    import copy

    frontend_conversation = copy.deepcopy(conversation)
    for message in frontend_conversation.get("messages", []):
        if message.get("role") != "user" or not isinstance(message.get("content"), list):
            continue
        public_content = []
        for block in message["content"]:
            if not isinstance(block, dict):
                public_content.append(block)
                continue
            if not is_attachment_content_block(block):
                public_content.append(block)
                continue

            source = block.get("source") if isinstance(block.get("source"), dict) else None
            local_path = source.get("file_path") if source else None
            metadata = block.get("_attachment")
            if not isinstance(metadata, dict):
                legacy_match = None
                if block.get("type") == "text":
                    legacy_match = LEGACY_ATTACHMENT_TEXT_RE.fullmatch(str(block.get("text", "")))
                metadata = {
                    "name": Path(str(
                        legacy_match.group(1) if legacy_match else local_path or block.get("name") or ""
                    )).name or "未命名附件",
                    "size": None,
                    "media_type": (source or {}).get("media_type", ""),
                    "kind": block.get("type") if block.get("type") in {"image", "document"} else "file",
                }
            name = Path(str(metadata.get("name") or "未命名附件")).name or "未命名附件"
            preview_id = metadata.get("preview_id") if isinstance(metadata.get("preview_id"), str) else None
            preview_available = bool(
                preview_id
                or block.get("type") == "text"
                or (block.get("type") == "image" and source and source.get("data"))
            )
            public_metadata = {
                "name": name,
                "size": metadata.get("size") if isinstance(metadata.get("size"), (int, float)) else None,
                "media_type": str(metadata.get("media_type") or (source or {}).get("media_type") or ""),
                "kind": str(metadata.get("kind") or (
                    block.get("type") if block.get("type") in {"image", "document"} else "file"
                )),
                "preview_id": preview_id,
                "preview_available": preview_available,
            }
            # Replacing the whole block prevents parsed text, base64, and server paths
            # from inflating or leaking through load_conversation responses.
            public_content.append({"type": "attachment", "_attachment": public_metadata})
        message["content"] = public_content
    return frontend_conversation


def migrate_and_persist_legacy_attachments(conv_manager, conversation):
    """Migrate eligible legacy blocks and persist the changed conversation."""
    if conversation and migrate_legacy_conversation_attachments(conversation):
        conv_manager.save_conversation(conversation)
    return conversation


class ConversationService(AppService):
    """Conversation persistence, message mutation, and streaming generation."""

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
        M-fix#5/#6:流式生成进行中时拒绝切换 current_conv,否则 reader 线程会把
        响应写进被切换到的新对话,导致原始对话丢失回复、新对话混入错误消息。
        """
        with self._app.lock:
            if self._app.is_streaming:
                logger.warning(f"流式生成进行中,拒绝切换对话,返回当前对话。请求 ID: {conv_id}")
                return conversation_for_frontend(self._app.current_conv)
            logger.info(f"正在加载对话记录，ID: {conv_id}")
            conv = self._app.conv_manager.load_conversation(conv_id)
            conv = migrate_and_persist_legacy_attachments(self._app.conv_manager, conv)
            if conv:
                self._app.current_conv = conv
            return conversation_for_frontend(conv)

    def new_conversation(self):
        """
        在数据库中初始化一条空白新对话，同时将全局配置项设置作为其默认初始值
        """
        with self._app.lock:
            logger.info("正在创建新会话...")
            conv_data = self._app.conv_manager.new_conversation()
            conv_platform = self._app.config.get("active_platform", "claude")
            conv_model = self._app.config.get("model", FALLBACK_MODELS[0])
            mapped = PlatformParamMapper.map_params(conv_platform, self._app.config.data, model_id=conv_model)

            conv_data["model"] = conv_model
            conv_data["platform"] = conv_platform
            conv_data["temperature"] = mapped.get("temperature", 0.7)
            conv_data["max_tokens"] = mapped.get("max_tokens", DEFAULT_MAX_TOKENS)

            # Build thinking config from the same model-aware mapping used by generation.
            conv_data["thinking"] = None
            if mapped.get("thinking_config"):
                conv_data["thinking"] = dict(mapped["thinking_config"])
                if (mapped.get("output_config") or {}).get("effort"):
                    conv_data["thinking"]["effort"] = mapped["output_config"]["effort"]
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
                from claude_chat.clients import extract_api_message

                temp_msg = {
                    "role": role,
                    "content": raw_content
                }

                # The packet inspector is also a public UI response.  Keep record
                # metadata, but never expose attachment payloads or server paths.
                public_message = conversation_for_frontend({"messages": [temp_msg]})["messages"][0]
                public_content = public_message.get("content", "")
                db_record["content"] = (
                    json.dumps(public_content, ensure_ascii=False)
                    if isinstance(public_content, (list, dict))
                    else public_content
                )

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

    def _start_stream_generation(self, conv_id, custom_queue=None):
        """
        流式接收的核心启动函数。
        根据会话 ID 捞出所有上下文消息，对复杂附件调用 client 进行转换，
        配置思维模型扩展思考模式参数（思维预算，深度水平），获取系统角色设置并启动后台生成线程。
        """
        try:
            task = self._app.begin_stream_task(conv_id, custom_queue)

            self._app.current_conv = self._app.conv_manager.load_conversation(conv_id)
            if not self._app.current_conv:
                self._app.set_streaming_done(StreamTaskState.FAILED, task)
                return False

            # Stream failures are kept in local history for diagnosis, but they are
            # UI records rather than model responses and must not enter API context.
            api_messages = messages_for_api(self._app.current_conv["messages"])

            # 获取活跃平台与投影映射后的扁平配置
            active_platform = self._app.current_conv.get("platform") or self._app.config.get("active_platform", "claude")
            current_model = self._app.current_conv.get("model") or self._app.config.get("model", FALLBACK_MODELS[0])
            mapped = PlatformParamMapper.map_params(active_platform, self._app.config.data, model_id=current_model)

            # 挂载流通道队列
            active_queue = task.events

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
                task.bind_stream(stream)

            # 解析特定于平台的思维信息传入
            thinking_enabled = False
            thinking_budget = 16000
            thinking_level = "high"
            if active_platform == "claude" and mapped["thinking_config"]:
                thinking_enabled = True
                if "budget_tokens" in mapped["thinking_config"]:
                    thinking_budget = mapped["thinking_config"]["budget_tokens"]
                if mapped["output_config"] and "effort" in mapped["output_config"]:
                    thinking_level = mapped["output_config"]["effort"]
            elif active_platform == "gemini" and mapped["thinking_config"]:
                thinking_enabled = True
                thinking_budget = mapped["thinking_config"]["budget_tokens"]
                thinking_level = mapped["thinking_config"]["effort"]

            # 后台线程异步发起 API 通信，防止阻塞主 GUI 事件循环导致卡死
            thread = threading.Thread(
                target=stream_claude_response,
                args=(
                    mapped["api_key"],
                    self._app.config.get("proxy_mode", "system"),
                    self._app.config.get("proxy_url", ""),
                    api_messages,
                    current_model,
                    mapped["max_tokens"],
                    mapped["temperature"],
                    mapped.get("thinking_config"),
                    active_queue,
                    task.abort_event,
                    on_stream_created
                ),
                kwargs={
                    "request_params": mapped.get("request_params"),
                    "custom_params": mapped["custom_params"],
                    "system": system_prompt_val,
                    "output_config": mapped["output_config"] if active_platform == "claude" else None,
                    "enable_search": mapped["enable_search"],
                    "enable_web_fetch": mapped["enable_web_fetch"],
                    "web_fetch_limit": mapped["web_fetch_limit"],
                    "search_engine": mapped["search_engine"],
                    "tavily_api_key": mapped["tavily_api_key"],
                    "jina_api_key": mapped["jina_api_key"],
                    "web_page_parser": mapped["web_page_parser"],
                    "conv_id": conv_id,
                    "conv_manager": self._app.conv_manager,
                    "active_platform": active_platform,
                    "deepseek_api_key": mapped["api_key"] if active_platform == "deepseek" else "",
                    "deepseek_api_url": mapped["api_url"],
                    "gemini_api_key": mapped["api_key"] if active_platform == "gemini" else "",
                    "gemini_api_url": mapped["api_url"],
                    "gemini_enable_code_sandbox": mapped["enable_code_sandbox"],
                    "gemini_code_sandbox_type": mapped["code_sandbox_type"],
                    "custom_api_key": mapped["api_key"] if active_platform.startswith("custom:") else "",
                    "custom_api_url": mapped["api_url"],
                    "thinking_enabled": thinking_enabled,
                    "thinking_budget": thinking_budget,
                    "thinking_level": thinking_level,
                    "file_upload_enabled": mapped["file_upload_enabled"],
                    "file_upload_expires_in_seconds": mapped["file_upload_expires_in_seconds"],
                    "file_upload_purpose": mapped["file_upload_purpose"],
                    "provider_adapter": mapped["provider_adapter"],
                    "depth": 0
                },
                daemon=True
            )
            thread.start()

            if custom_queue is None:
                # 开启异步读取流线程
                reader_thread = threading.Thread(
                    target=self._app._process_sending_stream,
                    args=(task,),
                    daemon=True
                )
                reader_thread.start()
            return True
        except Exception as e:
            logger.exception(f"启动流生成线程发生错误: {e}")
            if "task" in locals():
                self._app.set_streaming_done(StreamTaskState.FAILED, task)
            return False

    def send_message(self, conv_id, text, attachments, render_markdown=True, custom_queue=None):
        """
        发送用户消息。如果是大文本附件会自动拼装文本隔离区域随 Prompt 一同发送，
        图片或 PDF 会转换为多媒体结构，并由供应商客户端通过官方 Files API 上传后发送。
        """
        text = text if isinstance(text, str) else ""
        attachments = attachments if isinstance(attachments, list) else []
        if not isinstance(render_markdown, bool):
            # 兼容旧的第四个位置参数 custom_queue。
            if custom_queue is None:
                custom_queue = render_markdown
            render_markdown = True
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

            user_msg_display = {"role": "user", "content": text, "render_markdown": render_markdown}
            if attachments:
                user_msg_display["content"] = [{"type": "text", "text": text}]
                conv_platform = conv.get("platform") or self._app.config.get("active_platform", "claude")
                for att in attachments:
                    user_msg_display["content"].append(
                        prepare_attachment_content(att, conv_platform, self._app.config)
                    )

            conv["messages"].append(user_msg_display)
            self._app.conv_manager.save_conversation(conv)

            success = self._start_stream_generation(conv_id, custom_queue=custom_queue)
            if not success:
                self._app.conv_manager.save_conversation(backup_conv)
                return False
            return True

    def edit_and_resend(self, conv_id, msg_index, new_content, render_markdown=True, custom_queue=None):
        """
        用户修改并重新发送历史已发送消息：删除目标索引后的所有历史消息，重新发起生成请求。
        """
        if not isinstance(render_markdown, bool):
            if custom_queue is None:
                custom_queue = render_markdown
            render_markdown = True
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

                original_content = conv["messages"][msg_index].get("content")
                retained_attachments = []
                if isinstance(original_content, list):
                    retained_attachments = [
                        copy.deepcopy(block)
                        for block in original_content
                        if is_attachment_content_block(block)
                    ]

                conv["messages"] = conv["messages"][:msg_index]
                persisted_content = new_content
                if retained_attachments:
                    persisted_content = [{"type": "text", "text": new_content}, *retained_attachments]
                user_msg_display = {
                    "role": "user",
                    "content": persisted_content,
                    "render_markdown": render_markdown,
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
                    "platform": conv.get("platform", ""),
                    "temperature": conv.get("temperature", 0.7),
                    "max_tokens": conv.get("max_tokens", DEFAULT_MAX_TOKENS),
                    "thinking": conv.get("thinking"),
                    "created_at": now_str,
                    "updated_at": now_str,
                    # The branch contains only a prefix of the source messages, so the
                    # source conversation's aggregate usage cannot be copied accurately.
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "messages": branch_messages
                }

                self._app.conv_manager.save_conversation(new_conv)
                logger.info(f"对话成功分叉至新会话，新 ID: {new_conv_id}")
                return conversation_for_frontend(self._app.conv_manager.load_conversation(new_conv_id))
            except Exception as e:
                logger.exception(f"创建分叉对话失败: {e}")
                return None

    def abort_generation(self):
        return self._app.abort_generation()
