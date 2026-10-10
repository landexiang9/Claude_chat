"""Independent embedding transports. Credentials stay in the existing key store."""

import hashlib
import json
import math
import struct
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class _DiscoveryError(ValueError):
    """Only locally authored discovery errors may be shown to the UI."""


def _embedding_model(row, dedicated=False):
    """Read only text-embedding entries; generic catalogs sometimes omit capability metadata."""
    if not isinstance(row, dict):
        return None
    mid = row.get("id") or row.get("name")
    if not isinstance(mid, str):
        return None
    mid = mid.removeprefix("models/").strip()
    if not mid or len(mid) > 200 or any(ord(char) < 32 for char in mid):
        return None
    architecture = row.get("architecture") or {}
    architecture = architecture if isinstance(architecture, dict) else {}
    outputs = architecture.get("output_modalities", row.get("output_modalities"))
    inputs = architecture.get("input_modalities", row.get("input_modalities"))
    if isinstance(inputs, list) and "text" not in inputs:
        return None
    actions = row.get("supported_actions", row.get("supportedGenerationMethods"))
    if isinstance(outputs, list):
        supported = any(value in outputs for value in ("embedding", "embeddings"))
    elif isinstance(actions, list):
        supported = "embedContent" in actions or "embedText" in actions
    else:
        kind = row.get("type") or row.get("model_type") or ""
        kind = kind if isinstance(kind, str) else ""
        name = mid.casefold().split("/")[-1]
        supported = (
            dedicated
            or kind in {"embedding", "embeddings"}
            or (
                "rerank" not in name
                and ("embed" in name or name.startswith(("bge-", "e5-", "multilingual-e5-", "gte-")))
            )
        )
    if not supported:
        return None
    display = row.get("display_name") or row.get("name") or mid
    return {"id": mid, "display_name": str(display)[:200]}


def fetch_embedding_models(platform, config):
    """Discover separately from chat models. Never generate embeddings or mutate config/indexes."""
    from claude_chat.config import find_custom_provider
    from claude_chat.platform_params import PlatformParamMapper

    if platform == "local":
        return []
    custom = find_custom_provider(config, platform) if isinstance(platform, str) else None
    if platform != "gemini" and not custom:
        raise ValueError("请选择 Gemini 或已配置的兼容供应商获取 Embedding 模型")
    mapped = PlatformParamMapper.map_params(platform, config)
    key = mapped.get("api_key", "")
    if not key:
        raise ValueError("该供应商尚未配置 API Key，请先在供应商设置中保存")
    mode, proxy = config.get("proxy_mode", "system"), config.get("proxy_url", "")
    if mode == "custom" and not proxy.strip():
        raise ValueError("自定义代理地址不能为空")
    try:
        result = {}
        if platform == "gemini":
            from google import genai

            from claude_chat.clients.gemini_http import build_gemini_http_options

            options = build_gemini_http_options(mode, proxy, mapped.get("api_url", ""), 15000)
            with genai.Client(api_key=key, http_options=options) as client:
                for position, model in enumerate(client.models.list(config={"page_size": 100})):
                    if position >= 2000:
                        break
                    entry = _embedding_model(
                        {
                            "name": getattr(model, "name", None),
                            "display_name": getattr(model, "display_name", None),
                            "supported_actions": getattr(model, "supported_actions", None),
                        }
                    )
                    if entry:
                        result[entry["id"]] = entry
        else:
            import httpx

            from claude_chat.clients.base import build_http_client

            address = (custom.get("models_api_url") or mapped.get("api_url", "")).strip()
            parsed = urlsplit(address)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise _DiscoveryError("模型列表地址无效，请检查供应商设置")
            path = parsed.path.rstrip("/")
            for suffix in ("/chat/completions", "/responses", "/embeddings"):
                if path.endswith(suffix):
                    path = path[: -len(suffix)]
                    break
            # OpenRouter's /models catalog is for generation; use its dedicated embedding catalog.
            dedicated = parsed.hostname.casefold() == "openrouter.ai" or path.endswith("/embeddings/models")
            if parsed.hostname.casefold() == "openrouter.ai":
                if path.endswith("/embeddings/models"):
                    pass
                else:
                    if path.endswith("/models"):
                        path = path[: -len("/models")]
                    path = (path or "/api/v1") + "/embeddings/models"
            elif not path.endswith("/models"):
                path += "/models"
            url = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))
            with build_http_client(mode, proxy) as client:
                response = client.get(
                    url, headers={"Authorization": "Bearer " + key}, timeout=httpx.Timeout(15, connect=5)
                )
                if response.status_code >= 400:
                    raise _DiscoveryError(
                        f"获取 Embedding 模型失败（HTTP {response.status_code}），请检查密钥及模型列表地址"
                    )
                payload = response.json()
                rows = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(rows, list):
                    raise _DiscoveryError("供应商返回的模型列表格式无效")
                for row in rows[:2000]:
                    entry = _embedding_model(row, dedicated)
                    if entry:
                        result[entry["id"]] = entry
        return sorted(result.values(), key=lambda item: item["id"].casefold())
    except _DiscoveryError:
        raise
    except Exception:
        raise ValueError("获取 Embedding 模型失败，请检查供应商地址、密钥和代理；仍可手动填写") from None


def normalize(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 8192:
        raise ValueError("Embedding 向量维度无效")
    try:
        valid = all(type(x) in (int, float) and math.isfinite(x) for x in values)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Embedding 向量包含无效数值")
    scale = max(abs(x) for x in values)
    if scale == 0:
        raise ValueError("Embedding 返回了零向量")
    scaled = [x / scale for x in values]
    length = math.hypot(*scaled)
    result = [x / length for x in scaled]
    if not all(math.isfinite(x) for x in result) or not any(result):
        raise ValueError("Embedding 向量归一化失败")
    return result


def pack(vector):
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob, dimensions):
    if not 1 <= dimensions <= 8192 or len(blob) != 4 * dimensions:
        raise ValueError("向量索引已损坏，请重建")
    return list(struct.unpack(f"<{dimensions}f", blob))


class EmbeddingProvider:
    def __init__(self, options, config):
        self.options, self.config = options, config
        self.platform = options["embedding_platform"]
        self.model = options["embedding_model"]
        self.dimensions = options["embedding_dimensions"]
        self.mapped = {}
        if self.platform and self.platform != "local":
            from claude_chat.platform_params import PlatformParamMapper

            self.mapped = PlatformParamMapper.map_params(self.platform, config, model_id=self.model)
        signature = {
            "platform": self.platform,
            "model": self.model,
            "dimensions": self.dimensions,
            "url": self.mapped.get("api_url", ""),
            "key_hash": hashlib.sha256(self.mapped.get("api_key", "").encode()).hexdigest(),
            "local_path": options["embedding_local_path"],
        }
        self.signature = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        self._local_model = None

    def embed(self, texts, query=False):
        if not self.platform or not self.model:
            raise ValueError("未配置 Embedding，使用关键词检索")
        if not isinstance(texts, list) or not 1 <= len(texts) <= 32:
            raise ValueError("Embedding 批次应为 1–32 条")
        if self.platform == "local":
            directory = Path(self.options["embedding_local_path"])
            if not directory.is_dir():
                raise ValueError("请指定已下载的本地 Embedding 模型目录")
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ValueError("本地 Embedding 需要可选依赖 sentence-transformers") from exc
            if self._local_model is None:
                self._local_model = SentenceTransformer(str(directory), local_files_only=True, trust_remote_code=False)
            encoder = getattr(
                self._local_model, "encode_query" if query else "encode_document", self._local_model.encode
            )
            arguments = {"normalize_embeddings": True}
            if self.dimensions:
                arguments["truncate_dim"] = self.dimensions
            vectors = encoder(texts, **arguments).tolist()
        elif self.platform == "gemini":
            vectors = self._gemini(texts, query)
        elif self.platform.startswith("custom:") or self.platform == "deepseek":
            vectors = self._openai(texts, query)
        else:
            raise ValueError("该供应商未提供支持的 Embedding 接口，请选择 Gemini 或兼容供应商")
        if len(vectors) != len(texts):
            raise ValueError("Embedding 返回数量与输入不一致")
        vectors = [normalize(v) for v in vectors]
        if len({len(v) for v in vectors}) != 1 or (self.dimensions and len(vectors[0]) != self.dimensions):
            raise ValueError("Embedding 维度不匹配，请检查设置并重建索引")
        return vectors

    def _openai(self, texts, query):
        import httpx

        from claude_chat.clients.base import build_http_client

        if not self.mapped.get("api_key"):
            raise ValueError("Embedding 供应商未配置 API Key")
        url = self.mapped.get("api_url", "").rstrip("/")
        if not url:
            raise ValueError("Embedding 供应商地址不能为空")
        if url.endswith(("/chat/completions", "/responses")):
            url = url.rsplit("/", 2)[0] if url.endswith("/chat/completions") else url.rsplit("/", 1)[0]
        url = url if url.endswith("/embeddings") else url + "/embeddings"
        body = {"model": self.model, "input": texts, "encoding_format": "float"}
        if self.dimensions:
            body["dimensions"] = self.dimensions
        mode, proxy = self.config.get("proxy_mode", "system"), self.config.get("proxy_url", "")
        if mode == "custom" and not proxy.strip():
            raise ValueError("自定义代理地址不能为空")
        with build_http_client(mode, proxy) as client:
            client.timeout = httpx.Timeout(5 if query else 30, connect=5)
            response = client.post(url, json=body, headers={"Authorization": "Bearer " + self.mapped["api_key"]})
            if response.status_code >= 400:
                raise ValueError(f"Embedding 请求失败（HTTP {response.status_code}），请检查模型与服务可用地区")
            rows = response.json().get("data", [])
            if sorted(r.get("index", -1) for r in rows) != list(range(len(texts))):
                raise ValueError("Embedding 返回索引无效")
            return [r["embedding"] for r in sorted(rows, key=lambda r: r["index"])]

    def _gemini(self, texts, query):
        from google import genai
        from google.genai import types

        from claude_chat.clients.gemini_http import build_gemini_http_options

        if not self.mapped.get("api_key"):
            raise ValueError("Embedding 供应商未配置 API Key")
        options = build_gemini_http_options(
            self.config.get("proxy_mode", "system"),
            self.config.get("proxy_url", ""),
            self.mapped.get("api_url", ""),
            5000 if query else 30000,
        )
        config = {"output_dimensionality": self.dimensions} if self.dimensions else {}
        with genai.Client(api_key=self.mapped["api_key"], http_options=options) as client:
            if "gemini-embedding-2" in self.model:
                # embedding-2 combines multi-input contents and does not support task_type.
                prefix = "task: search result | query: " if query else "title: none | text: "
                return [
                    client.models.embed_content(
                        model=self.model,
                        contents=prefix + text,
                        config=types.EmbedContentConfig(**config),
                    )
                    .embeddings[0]
                    .values
                    for text in texts
                ]
            config["task_type"] = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
            result = client.models.embed_content(
                model=self.model, contents=texts, config=types.EmbedContentConfig(**config)
            )
            return [item.values for item in result.embeddings]
