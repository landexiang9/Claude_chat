"""Managed attachment storage and bounded preview generation.

Only random preview IDs cross the JS/Python or HTTP boundary.  Local paths are
kept server-side and are always reconstructed beneath ``ATTACHMENT_STORE_DIR``.
"""

import base64
import binascii
import io
import json
import logging
import mimetypes
import os
import re
import uuid
import warnings
from pathlib import Path

from claude_chat.config import (
    ATTACHMENT_STORE_DIR,
    IMAGE_EXTENSIONS,
    MAX_ATTACHMENT_PREVIEW_IMAGE_BYTES,
    MAX_ATTACHMENT_PREVIEW_IMAGE_EDGE,
    MAX_ATTACHMENT_PREVIEW_IMAGE_PIXELS,
    MAX_ATTACHMENT_PREVIEW_TEXT_BYTES,
    MAX_ATTACHMENT_SIZE,
    OFFICE_EXTENSIONS,
    PDF_EXTENSIONS,
    SUPPORTED_ATTACHMENT_EXTENSIONS,
    TEXT_EXTENSIONS,
)

logger = logging.getLogger("claude_chat")

PREVIEW_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ATTACHMENT_TEXT_WRAPPER_RE = re.compile(
    r"^\s*---\s*附件文件:\s*(.+?)\s*---\s*(?:\r?\n)([\s\S]*?)(?:\r?\n)---\s*附件结束\s*---\s*$"
)
IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
METADATA_SIZE_LIMIT = 16 * 1024


def validate_preview_id(value):
    """Return a normalized preview ID or raise without echoing attacker input."""
    if not isinstance(value, str) or not PREVIEW_ID_RE.fullmatch(value):
        raise ValueError("附件预览 ID 无效")
    return value


def _store_root():
    root = Path(ATTACHMENT_STORE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_name(value):
    if not isinstance(value, str):
        raise ValueError("文件名无效")
    name = Path(value.replace("\\", "/")).name.strip()
    if name in {"", ".", ".."}:
        raise ValueError("文件名无效")
    extension = Path(name).suffix.lower()
    if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
        raise ValueError(f"不支持的附件格式: {name}")
    return name, extension


def _mime_type(name):
    extension = Path(name).suffix.lower()
    overrides = {
        ".py": "text/x-python",
        ".js": "text/javascript",
        ".ts": "text/typescript",
        ".md": "text/markdown",
        ".yaml": "text/yaml",
        ".yml": "text/yaml",
        ".sql": "text/x-sql",
    }
    if extension in overrides:
        return overrides[extension]
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def _kind(name):
    extension = Path(name).suffix.lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in PDF_EXTENSIONS or extension in OFFICE_EXTENSIONS:
        return "document"
    if extension in TEXT_EXTENSIONS:
        return "text"
    return "file"


def _public_metadata(preview_id, name, size):
    return {
        "preview_id": preview_id,
        "preview_available": True,
        "name": name,
        "size": size,
        "media_type": _mime_type(name),
        "kind": _kind(name),
    }


def _metadata_path(root, preview_id):
    return root / f"{preview_id}.meta.json"


def _data_path(root, preview_id, extension):
    candidate = (root / f"{preview_id}{extension}").resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError("附件存储路径无效") from None
    return candidate


def _write_metadata(root, metadata):
    preview_id = metadata["preview_id"]
    final_path = _metadata_path(root, preview_id)
    temp_path = root / f".{preview_id}.{uuid.uuid4().hex}.meta.tmp"
    try:
        encoded = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > METADATA_SIZE_LIMIT:
            raise ValueError("附件元数据过大")
        with open(temp_path, "xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, final_path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _finalize_stored_file(root, preview_id, name, temp_path, size):
    _, extension = _safe_name(name)
    final_path = _data_path(root, preview_id, extension)
    metadata = _public_metadata(preview_id, name, size)
    metadata["extension"] = extension
    try:
        os.replace(temp_path, final_path)
        _write_metadata(root, metadata)
    except Exception:
        try:
            final_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return {key: value for key, value in metadata.items() if key != "extension"}


def store_attachment_path(source_path, display_name=None):
    """Copy one user-selected file into the managed attachment directory."""
    source = Path(source_path).resolve(strict=True)
    if not source.is_file():
        raise ValueError("所选附件不是文件")
    name, _ = _safe_name(display_name or source.name)
    preview_id = uuid.uuid4().hex
    root = _store_root()
    temp_path = root / f".{preview_id}.{uuid.uuid4().hex}.data.tmp"
    total = 0
    try:
        with open(source, "rb") as source_stream, open(temp_path, "xb") as destination:
            while True:
                chunk = source_stream.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ATTACHMENT_SIZE:
                    raise ValueError(f"附件超过 20 MB 限制: {name}")
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        return _finalize_stored_file(root, preview_id, name, temp_path, total)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def store_attachment_bytes(data, display_name):
    """Persist validated upload bytes under a random opaque preview ID."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("文件内容无效")
    if len(data) > MAX_ATTACHMENT_SIZE:
        raise ValueError("文件大小不能超过 20 MB")
    name, _ = _safe_name(display_name)
    preview_id = uuid.uuid4().hex
    root = _store_root()
    temp_path = root / f".{preview_id}.{uuid.uuid4().hex}.data.tmp"
    try:
        with open(temp_path, "xb") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        return _finalize_stored_file(root, preview_id, name, temp_path, len(data))
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def load_managed_attachment(preview_id):
    """Resolve an opaque preview ID to trusted metadata and a store-rooted path."""
    preview_id = validate_preview_id(preview_id)
    root = _store_root()
    metadata_path = _metadata_path(root, preview_id)
    try:
        if metadata_path.is_symlink() or metadata_path.stat().st_size > METADATA_SIZE_LIMIT:
            raise ValueError("附件预览元数据无效")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        raise ValueError("附件预览已失效") from None

    if not isinstance(metadata, dict) or metadata.get("preview_id") != preview_id:
        raise ValueError("附件预览元数据无效")
    name, extension = _safe_name(metadata.get("name"))
    if metadata.get("extension") != extension:
        raise ValueError("附件预览元数据无效")
    path = _data_path(root, preview_id, extension)
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("附件预览已失效")
        actual_size = path.stat().st_size
    except OSError:
        raise ValueError("附件预览已失效") from None
    if actual_size > MAX_ATTACHMENT_SIZE or actual_size != metadata.get("size"):
        raise ValueError("附件预览文件大小异常")

    trusted = _public_metadata(preview_id, name, actual_size)
    return path, trusted


def remove_managed_attachment(preview_id):
    """Delete one managed payload and its sidecar using an opaque ID only."""
    preview_id = validate_preview_id(preview_id)
    root = _store_root()
    deleted = False

    # Do not trust the sidecar to tell us what to delete.  The ID and extension
    # allowlist make every candidate an exact child of the managed store.
    for extension in sorted(SUPPORTED_ATTACHMENT_EXTENSIONS):
        candidate = root / f"{preview_id}{extension}"
        try:
            if candidate.is_symlink() or candidate.is_file():
                candidate.unlink()
                deleted = True
        except OSError as exc:
            raise ValueError("无法删除受管附件") from exc

    metadata_path = _metadata_path(root, preview_id)
    try:
        if metadata_path.is_symlink() or metadata_path.is_file():
            metadata_path.unlink()
            deleted = True
    except OSError as exc:
        raise ValueError("无法删除附件元数据") from exc
    return deleted


def migrate_legacy_attachment_block(block):
    """Move a server-loaded legacy image/document block into managed storage.

    This helper must only be called for content read from the conversation
    database.  It never accepts a path through the public preview API.
    """
    if not isinstance(block, dict) or block.get("type") not in {"image", "document"}:
        return False

    block_type = block["type"]
    source = block.get("source") if isinstance(block.get("source"), dict) else {}
    metadata = block.get("_attachment") if isinstance(block.get("_attachment"), dict) else {}

    # If the block already has a working managed ID, normalize the internal
    # source path and metadata without copying the payload again.
    preview_id = metadata.get("preview_id")
    if isinstance(preview_id, str) and PREVIEW_ID_RE.fullmatch(preview_id):
        try:
            managed_path, trusted = load_managed_attachment(preview_id)
            if (block_type == "image") != (trusted["kind"] == "image"):
                return False
            normalized_source = {
                "file_path": str(managed_path),
                "media_type": trusted["media_type"],
            }
            changed = block.get("_attachment") != trusted or block.get("source") != normalized_source
            block["_attachment"] = trusted
            block["source"] = normalized_source
            return changed
        except ValueError:
            pass

    legacy_path = source.get("file_path")
    if not isinstance(legacy_path, str) or not legacy_path:
        return False

    try:
        source_path = Path(legacy_path).resolve(strict=True)
        extension = source_path.suffix.lower()
        if block_type == "image":
            if extension not in IMAGE_EXTENSIONS:
                return False
        elif extension not in PDF_EXTENSIONS | OFFICE_EXTENSIONS:
            return False

        # Some intermediate builds already used an opaque filename but did not
        # persist _attachment.  Reuse it if the matching sidecar is intact.
        managed_path = None
        trusted = None
        candidate_id = source_path.stem
        if PREVIEW_ID_RE.fullmatch(candidate_id):
            try:
                managed_path, trusted = load_managed_attachment(candidate_id)
            except ValueError:
                pass

        if trusted is None:
            source_name = source_path.name
            stored_name = metadata.get("name")
            if not isinstance(stored_name, str) or Path(stored_name).suffix.lower() != extension:
                stored_name = source_name
            descriptor = store_attachment_path(source_path, display_name=stored_name)
            managed_path, trusted = load_managed_attachment(descriptor["preview_id"])

        if (block_type == "image") != (trusted["kind"] == "image"):
            return False
        block["_attachment"] = trusted
        block["source"] = {
            "file_path": str(managed_path),
            "media_type": trusted["media_type"],
        }
        return True
    except (OSError, RuntimeError, ValueError):
        logger.warning("无法迁移历史附件: %s", Path(str(metadata.get("name") or "未命名附件")).name)
        return False


def migrate_legacy_conversation_attachments(conversation):
    """Migrate all eligible server-loaded blocks in one conversation in place."""
    if not isinstance(conversation, dict):
        return 0
    migrated = 0
    for message in conversation.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if migrate_legacy_attachment_block(block):
                migrated += 1
    return migrated


def _truncate_text(text):
    encoded = str(text or "").encode("utf-8")
    if len(encoded) <= MAX_ATTACHMENT_PREVIEW_TEXT_BYTES:
        return encoded.decode("utf-8"), False
    return encoded[:MAX_ATTACHMENT_PREVIEW_TEXT_BYTES].decode("utf-8", errors="ignore"), True


def _decode_text_bytes(data):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text_preview(path):
    with open(path, "rb") as stream:
        data = stream.read(MAX_ATTACHMENT_PREVIEW_TEXT_BYTES + 4)
    truncated = len(data) > MAX_ATTACHMENT_PREVIEW_TEXT_BYTES
    if truncated:
        data = data[:MAX_ATTACHMENT_PREVIEW_TEXT_BYTES]
    text = _decode_text_bytes(data)
    text, encoded_truncated = _truncate_text(text)
    return text, truncated or encoded_truncated


def _text_response(metadata, text, truncated=False):
    bounded, was_truncated = _truncate_text(text)
    return {
        "type": "text",
        "preview_type": "text",
        **metadata,
        "text": bounded,
        "truncated": bool(truncated or was_truncated),
    }


def _encode_image_preview(source, metadata):
    try:
        from PIL import Image, ImageOps

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as source_image:
                width, height = source_image.size
                if width <= 0 or height <= 0 or width * height > MAX_ATTACHMENT_PREVIEW_IMAGE_PIXELS:
                    raise ValueError("图片像素尺寸超过预览限制")
                if getattr(source_image, "is_animated", False):
                    source_image.seek(0)
                image = ImageOps.exif_transpose(source_image).copy()

        original_size = image.size
        image.thumbnail(
            (MAX_ATTACHMENT_PREVIEW_IMAGE_EDGE, MAX_ATTACHMENT_PREVIEW_IMAGE_EDGE),
            Image.Resampling.LANCZOS,
        )
        resized = image.size != original_size
        has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
        output = io.BytesIO()
        if has_alpha:
            image = image.convert("RGBA")
            image.save(output, format="PNG", optimize=True)
            preview_bytes = output.getvalue()
            preview_mime = "image/png"
        else:
            image = image.convert("RGB")
            image.save(output, format="JPEG", quality=86, optimize=True)
            preview_bytes = output.getvalue()
            preview_mime = "image/jpeg"

        if len(preview_bytes) > MAX_ATTACHMENT_PREVIEW_IMAGE_BYTES:
            flattened = Image.new("RGB", image.size, "white")
            if image.mode == "RGBA":
                flattened.paste(image, mask=image.getchannel("A"))
            else:
                flattened.paste(image.convert("RGB"))
            quality = 82
            while True:
                output = io.BytesIO()
                flattened.save(output, format="JPEG", quality=quality, optimize=True)
                preview_bytes = output.getvalue()
                preview_mime = "image/jpeg"
                if len(preview_bytes) <= MAX_ATTACHMENT_PREVIEW_IMAGE_BYTES:
                    break
                if min(flattened.size) <= 128:
                    raise ValueError("图片预览编码后仍然过大")
                flattened.thumbnail(
                    (max(128, int(flattened.width * 0.8)), max(128, int(flattened.height * 0.8))),
                    Image.Resampling.LANCZOS,
                )
                quality = max(55, quality - 7)
                resized = True
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("生成附件图片预览失败: %s", exc)
        raise ValueError(f"无法预览图片附件: {metadata['name']}") from None

    return {
        "type": "image",
        "preview_type": "image",
        **metadata,
        "media_type": preview_mime,
        "source_media_type": metadata.get("media_type", ""),
        "data_url": f"data:{preview_mime};base64,{base64.b64encode(preview_bytes).decode('ascii')}",
        "truncated": False,
        "resized": resized,
    }


def _attachment_text_payload(block):
    text = str(block.get("text", ""))
    match = ATTACHMENT_TEXT_WRAPPER_RE.fullmatch(text)
    return match.group(2) if match else text


def preview_managed_attachment(preview_id, config, block=None):
    """Return a bounded image/text preview for a managed attachment."""
    path, metadata = load_managed_attachment(preview_id)
    extension = Path(metadata["name"]).suffix.lower()

    if metadata["kind"] == "image":
        return _encode_image_preview(path, metadata)
    if isinstance(block, dict) and block.get("type") == "text":
        return _text_response(metadata, _attachment_text_payload(block))
    if extension in TEXT_EXTENSIONS:
        text, truncated = _read_text_preview(path)
        return _text_response(metadata, text, truncated=truncated)

    from claude_chat.attachment_parser import parse_attachment_to_markdown

    parsed = parse_attachment_to_markdown(
        {"path": str(path), "name": metadata["name"], "size": metadata["size"]},
        ocr_mode="none",
        cloud_provider=config.get("ocr_cloud_model", "gemini"),
        api_key="",
        proxy_mode=config.get("proxy_mode", "system"),
        proxy_url=config.get("proxy_url", ""),
    )
    return _text_response(metadata, parsed)


def _decode_legacy_image_data(source, metadata):
    media_type = str(source.get("media_type") or metadata.get("media_type") or "").lower()
    if media_type not in IMAGE_MEDIA_TYPES:
        raise ValueError("旧图片附件的媒体类型无效")
    encoded = str(source.get("data") or "")
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or ";base64" not in header.lower():
            raise ValueError("旧图片附件编码无效")
    encoded = "".join(encoded.split())
    maximum_encoded = ((MAX_ATTACHMENT_SIZE + 2) // 3) * 4 + 4
    if len(encoded) > maximum_encoded:
        raise ValueError("旧图片附件超过大小限制")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("旧图片附件编码无效") from None
    if len(payload) > MAX_ATTACHMENT_SIZE:
        raise ValueError("旧图片附件超过大小限制")
    metadata = dict(metadata)
    metadata["size"] = len(payload)
    return _encode_image_preview(io.BytesIO(payload), metadata)


def preview_content_block(block, config):
    """Preview a server-loaded content block without trusting a client path."""
    if not isinstance(block, dict):
        raise ValueError("附件内容块无效")
    metadata = block.get("_attachment") if isinstance(block.get("_attachment"), dict) else {}
    preview_id = metadata.get("preview_id")
    if preview_id:
        return preview_managed_attachment(preview_id, config, block=block)

    source = block.get("source") if isinstance(block.get("source"), dict) else {}
    legacy_name = Path(str(metadata.get("name") or block.get("name") or "未命名附件")).name
    legacy_metadata = {
        "preview_id": None,
        "preview_available": True,
        "name": legacy_name or "未命名附件",
        "size": metadata.get("size"),
        "media_type": str(metadata.get("media_type") or source.get("media_type") or "application/octet-stream"),
        "kind": str(metadata.get("kind") or block.get("type") or "file"),
    }
    if block.get("type") == "text":
        return _text_response(legacy_metadata, _attachment_text_payload(block))
    if block.get("type") == "image" and source.get("data"):
        return _decode_legacy_image_data(source, legacy_metadata)

    # Historical managed blocks may have lost metadata.  Only derive an ID from
    # the basename; the supplied path itself is never opened.
    legacy_path = source.get("file_path")
    if isinstance(legacy_path, str):
        candidate_id = Path(legacy_path).stem
        if PREVIEW_ID_RE.fullmatch(candidate_id):
            return preview_managed_attachment(candidate_id, config, block=block)
    raise ValueError(f"附件预览源已不可用: {legacy_metadata['name']}")
