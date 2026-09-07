"""Provider-native file upload helpers.

Local attachment paths never leave this module.  Each provider receives only the
opaque identifier/URI returned by its official Files API.
"""

from __future__ import annotations

import copy
import hashlib
import mimetypes
import threading
import time
from pathlib import Path


_CACHE_LOCK = threading.Lock()
_UPLOAD_CACHE: dict[tuple, tuple[float, dict]] = {}


def upload_cache_namespace(provider: str, api_key: str, api_url: str = "") -> str:
    """Return a non-secret cache namespace scoped to credentials and endpoint."""
    digest = hashlib.sha256(f"{api_url}\0{api_key}".encode("utf-8")).hexdigest()[:20]
    return f"{provider}:{digest}"


def _local_source(block):
    if not isinstance(block, dict):
        return None
    source = block.get("source")
    if not isinstance(source, dict) or not source.get("file_path"):
        return None
    path = Path(source["file_path"])
    if not path.is_file():
        raise FileNotFoundError(f"附件不可用: {path.name}")
    mime_type = source.get("media_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path, mime_type


def _cached_upload(namespace, path, mime_type, purpose, ttl_seconds, uploader):
    stat = path.stat()
    key = (namespace, str(path.resolve()), stat.st_size, stat.st_mtime_ns, mime_type, purpose)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _UPLOAD_CACHE.get(key)
        if cached and cached[0] > now:
            return dict(cached[1])

    value = uploader()
    with _CACHE_LOCK:
        _UPLOAD_CACHE[key] = (now + max(60, int(ttl_seconds)), dict(value))
    return value


def prepare_anthropic_files(client, messages, namespace, expires_in_seconds=172800):
    """Upload Claude image/PDF blocks and replace paths with Files API IDs."""
    prepared = copy.deepcopy(messages)
    for message in prepared:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            local = _local_source(block)
            if not local or block.get("type") not in {"image", "document"}:
                continue
            path, mime_type = local

            def upload():
                with path.open("rb") as handle:
                    kwargs = {"file": (path.name, handle, mime_type)}
                    if expires_in_seconds:
                        kwargs["expires_in_seconds"] = int(expires_in_seconds)
                    result = client.files.upload(**kwargs)
                return {"file_id": result.id}

            remote = _cached_upload(
                namespace,
                path,
                mime_type,
                "anthropic",
                min(int(expires_in_seconds or 3600) - 60, 47 * 3600),
                upload,
            )
            block.pop("_attachment", None)
            block["source"] = {"type": "file", "file_id": remote["file_id"]}
    return prepared


def prepare_openai_compatible_files(
    client,
    messages,
    namespace,
    *,
    purpose="user_data",
    image_only=False,
    expires_after_seconds=172800,
):
    """Upload attachments through an OpenAI-compatible ``POST /files`` API."""
    prepared = copy.deepcopy(messages)
    for message in prepared:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            local = _local_source(block)
            if not local or block.get("type") not in {"image", "document", "file"}:
                continue
            if image_only and block.get("type") != "image":
                continue
            path, mime_type = local

            def upload():
                with path.open("rb") as handle:
                    kwargs = {"file": handle, "purpose": purpose}
                    if expires_after_seconds:
                        kwargs["expires_after"] = {
                            "anchor": "created_at",
                            "seconds": int(expires_after_seconds),
                        }
                    result = client.files.create(**kwargs)
                return {"file_id": result.id}

            ttl = min(int(expires_after_seconds or 3600) - 60, 47 * 3600)
            remote = _cached_upload(namespace, path, mime_type, purpose, ttl, upload)
            block.pop("_attachment", None)
            block["source"] = {"type": "file", "file_id": remote["file_id"], "media_type": mime_type}
    return prepared


def prepare_gemini_files(client, messages, namespace):
    """Upload Gemini image/PDF blocks and replace paths with Files API URIs."""
    prepared = copy.deepcopy(messages)
    for message in prepared:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            local = _local_source(block)
            if not local or block.get("type") not in {"image", "document", "file"}:
                continue
            path, mime_type = local

            def upload():
                result = client.files.upload(
                    file=str(path),
                    config={"display_name": path.name, "mime_type": mime_type},
                )
                deadline = time.monotonic() + 60
                state = getattr(getattr(result, "state", None), "name", "")
                while state == "PROCESSING" and time.monotonic() < deadline:
                    time.sleep(0.5)
                    result = client.files.get(name=result.name)
                    state = getattr(getattr(result, "state", None), "name", "")
                if state == "FAILED":
                    raise RuntimeError(f"Gemini 文件处理失败: {path.name}")
                if state == "PROCESSING":
                    raise TimeoutError(f"Gemini 文件处理超时: {path.name}")
                return {"file_uri": result.uri, "mime_type": result.mime_type or mime_type}

            remote = _cached_upload(namespace, path, mime_type, "gemini", 47 * 3600, upload)
            block.pop("_attachment", None)
            block["source"] = {
                "type": "uri",
                "file_uri": remote["file_uri"],
                "media_type": remote["mime_type"],
            }
    return prepared
