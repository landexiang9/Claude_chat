import logging
import base64
import binascii
import copy
from pathlib import Path

import webview

from claude_chat.config import MAX_ATTACHMENT_SIZE, SUPPORTED_ATTACHMENT_EXTENSIONS
from claude_chat.db import deserialize_content
from claude_chat.services.base import AppService
from claude_chat.services.attachment_store import (
    preview_content_block,
    preview_managed_attachment,
    remove_managed_attachment,
    store_attachment_bytes,
    store_attachment_path,
    validate_preview_id,
)

logger = logging.getLogger("claude_chat")


def _content_references_preview_id(content, preview_id):
    """Conservatively find managed attachment references in stored JSON content."""
    pending = [content]
    inspected = 0
    while pending:
        current = pending.pop()
        inspected += 1
        # Treat pathological content as referenced instead of risking deletion.
        if inspected > 100_000:
            return True
        if isinstance(current, list):
            pending.extend(current)
            continue
        if not isinstance(current, dict):
            continue
        if current.get("preview_id") == preview_id:
            return True
        metadata = current.get("_attachment")
        if isinstance(metadata, dict) and metadata.get("preview_id") == preview_id:
            return True
        source = current.get("source")
        if isinstance(source, dict):
            file_path = source.get("file_path")
            if isinstance(file_path, str) and Path(file_path).stem == preview_id:
                return True
        pending.extend(value for value in current.values() if isinstance(value, (dict, list)))
    return False

class FileService(AppService):
    """Clipboard, attachment selection, upload, and export operations."""

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

    def select_attachments(self):
        """
        弹出文件选择对话框（在 GUI 模式下），获取用户选中的文件（支持图片、PDF和纯文本源码等），
        将文件复制进应用受管目录，并仅把不透明预览 ID 与公共元数据返回前端。
        """
        if not self._app.window:
            return []

        try:
            attachment_pattern = ";".join(
                f"*{extension}" for extension in sorted(SUPPORTED_ATTACHMENT_EXTENSIONS)
            )
            file_paths = self._app.window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory="",
                allow_multiple=True,
                file_types=(
                    f"支持的附件 ({attachment_pattern})",
                    "所有文件 (*.*)",
                ),
            )
        except Exception as exc:
            logger.exception("打开附件选择窗口失败: %s", exc)
            raise RuntimeError(f"无法打开文件选择窗口: {exc}") from exc
        if not file_paths:
            return []

        result = []
        rejected = []
        for fp in file_paths:
            try:
                p = Path(fp)
                if not p.is_file():
                    rejected.append(f"{p.name or fp}（不是文件）")
                    continue
                file_size = p.stat().st_size
                if file_size > MAX_ATTACHMENT_SIZE:
                    rejected.append(f"{p.name}（超过 20 MB）")
                    continue
                result.append(store_attachment_path(p, display_name=p.name))
            except Exception as exc:
                logger.warning("读取附件元数据失败 %r: %s", fp, exc)
                rejected.append(f"{Path(str(fp)).name or fp}（无法读取）")
        if rejected:
            logger.warning("已跳过无法添加的附件: %s", "、".join(rejected))
            if not result:
                raise ValueError("文件添加失败: " + "、".join(rejected))
        return result

    def save_code_block(self, content, suggest_name):
        return self._app.save_code_block(content, suggest_name)

    def save_image(self, image_data, suggest_name):
        return self._app.save_image(image_data, suggest_name)

    def upload_dropped_file(self, name, size, base64_data):
        """
        处理前端拖拽或上传的小文件/图片等。
        解析 Base64 后直接存入应用受管附件目录，绝不向前端暴露服务器路径。
        """
        try:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("文件名无效")
            if not isinstance(base64_data, str) or not base64_data:
                raise ValueError("文件内容为空")
            try:
                declared_size = int(size)
            except (TypeError, ValueError) as exc:
                raise ValueError("文件大小无效") from exc
            if declared_size < 0 or declared_size > MAX_ATTACHMENT_SIZE:
                raise ValueError("文件大小不能超过 20 MB")

            # 兼容 data URL 与纯 Base64；严格校验，避免损坏内容被静默接受。
            encoded = base64_data.split(",", 1)[1] if "," in base64_data else base64_data
            encoded = "".join(encoded.split())
            maximum_encoded = ((MAX_ATTACHMENT_SIZE + 2) // 3) * 4 + 4
            if len(encoded) > maximum_encoded:
                raise ValueError("文件大小不能超过 20 MB")
            try:
                data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("文件内容不是有效的 Base64 数据") from exc
            if len(data) != declared_size:
                raise ValueError(f"文件大小不匹配（声明 {declared_size} 字节，实际 {len(data)} 字节）")
            if len(data) > MAX_ATTACHMENT_SIZE:
                raise ValueError("文件大小不能超过 20 MB")

            # 强制提取文件名，阻止路径穿越 (../../)
            safe_name = Path(name.replace("\\", "/")).name.strip()
            if safe_name in {"", ".", ".."}:
                raise ValueError("文件名无效")

            return store_attachment_bytes(data, safe_name)
        except Exception as e:
            logger.error("保存前端上传的附件临时文件失败: %s", e)
            return {"error": str(e)}

    def get_attachment_preview(self, request):
        """Return a bounded preview without ever accepting a client filesystem path."""
        try:
            if not isinstance(request, dict):
                raise ValueError("附件预览请求格式无效")
            if "path" in request or "file_path" in request:
                raise ValueError("附件预览不接受本地路径")

            scope = request.get("scope", "pending")
            if scope == "pending":
                preview_id = validate_preview_id(request.get("preview_id"))
                return preview_managed_attachment(preview_id, self._app.config)
            if scope != "message":
                raise ValueError("附件预览范围无效")

            conv_id = request.get("conv_id")
            msg_index = request.get("message_index", request.get("msg_index"))
            block_index = request.get("block_index")
            attachment_index = request.get("attachment_index")
            if not isinstance(conv_id, str) or not conv_id or len(conv_id) > 128:
                raise ValueError("会话 ID 无效")
            if isinstance(msg_index, bool) or not isinstance(msg_index, int) or msg_index < 0:
                raise ValueError("消息索引无效")

            from claude_chat.services.conversation_service import (
                is_attachment_content_block,
                migrate_and_persist_legacy_attachments,
            )

            with self._app.lock:
                conversation = self._app.conv_manager.load_conversation(conv_id)
                conversation = migrate_and_persist_legacy_attachments(
                    self._app.conv_manager,
                    conversation,
                )
                messages = conversation.get("messages", []) if conversation else []
                if msg_index >= len(messages):
                    raise ValueError("未找到指定消息")
                message = messages[msg_index]
                if message.get("role") != "user" or not isinstance(message.get("content"), list):
                    raise ValueError("指定消息不包含可预览附件")

                if block_index is not None:
                    if isinstance(block_index, bool) or not isinstance(block_index, int) or block_index < 0:
                        raise ValueError("内容块索引无效")
                    if block_index >= len(message["content"]):
                        raise ValueError("未找到指定附件")
                    block = message["content"][block_index]
                    if not is_attachment_content_block(block):
                        raise ValueError("指定内容块不是附件")
                else:
                    if isinstance(attachment_index, bool) or not isinstance(attachment_index, int) or attachment_index < 0:
                        raise ValueError("附件索引无效")
                    attachment_blocks = [
                        candidate for candidate in message["content"] if is_attachment_content_block(candidate)
                    ]
                    if attachment_index >= len(attachment_blocks):
                        raise ValueError("未找到指定附件")
                    block = attachment_blocks[attachment_index]
                block = copy.deepcopy(block)

            requested_preview_id = request.get("preview_id")
            persisted_metadata = block.get("_attachment") if isinstance(block, dict) else None
            persisted_preview_id = persisted_metadata.get("preview_id") if isinstance(persisted_metadata, dict) else None
            if requested_preview_id is not None:
                validate_preview_id(requested_preview_id)
                if requested_preview_id != persisted_preview_id:
                    raise ValueError("附件预览引用已过期")
            return preview_content_block(block, self._app.config)
        except Exception as exc:
            logger.warning("生成附件预览失败: %s", exc)
            return {"error": str(exc)}

    def discard_pending_attachment(self, preview_id):
        """Delete an unreferenced managed attachment; never accepts a path."""
        try:
            preview_id = validate_preview_id(preview_id)
            with self._app.lock:
                with self._app.conv_manager.get_connection() as connection:
                    rows = connection.execute("SELECT content FROM messages").fetchall()
                    for row in rows:
                        raw_content = row["content"] if hasattr(row, "keys") else row[0]
                        content = deserialize_content(raw_content)
                        if _content_references_preview_id(content, preview_id):
                            return {
                                "success": False,
                                "deleted": False,
                                "reason": "referenced",
                                "message": "附件已被消息引用，不能删除",
                            }
                deleted = remove_managed_attachment(preview_id)
            return {"success": True, "deleted": deleted}
        except Exception as exc:
            logger.warning("丢弃待发送附件失败: %s", exc)
            return {"success": False, "deleted": False, "error": str(exc)}

    def preview_attachment(self, attachment):
        """Compatibility wrapper for pending previews; paths remain forbidden."""
        request = dict(attachment) if isinstance(attachment, dict) else attachment
        if isinstance(request, dict):
            request["scope"] = "pending"
        return self.get_attachment_preview(request)

    def preview_message_attachment(self, conv_id, msg_index, attachment_index):
        """Compatibility wrapper for the previous indexed preview API."""
        return self.get_attachment_preview({
            "scope": "message",
            "conv_id": conv_id,
            "message_index": msg_index,
            "attachment_index": attachment_index,
        })
