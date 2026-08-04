import json
import logging

from claude_chat.clients import fetch_available_models
from claude_chat.clients.models import _do_fetch_registry
from claude_chat.config import custom_platform_id, find_custom_provider, sanitize_provider_id
from claude_chat.services.base import AppService

logger = logging.getLogger("claude_chat")


class ModelService(AppService):
    """Model discovery, registry refresh, and custom-provider operations."""

    def update_model_registry(self):
        """
        供前端手动调用的方法，强制重新拉取并更新模型能力表，阻塞直到完成并返回。
        """
        logger.info("Manual update of model registry requested from UI.")
        try:
            result = _do_fetch_registry()
            if result is not None:
                return {"success": True, "count": len(result)}
            else:
                return {"success": False, "error": "Fetch failed, see logs."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def fetch_models(self, requested_platform=None):
        """
        前端请求在线模型列表。改为同步获取并返回最新结果，以支持 headless server 模式。
        """
        configured_platform = self._app.config.get("active_platform", "claude")
        active_platform = requested_platform if isinstance(requested_platform, str) and requested_platform else configured_platform
        if active_platform not in {"claude", "deepseek", "gemini"} and not active_platform.startswith("custom:"):
            return []
        if active_platform.startswith("custom:") and not find_custom_provider(self._app.config.data, active_platform):
            return []
        if active_platform == "deepseek":
            api_key = self._app.config.get("deepseek_api_key", "")
            platform_api_url = self._app.config.get("deepseek_api_url", "https://api.deepseek.com")
        elif active_platform == "gemini":
            api_key = self._app.config.get("gemini_api_key", "")
            platform_api_url = self._app.config.get("gemini_api_url", "")
        elif active_platform.startswith("custom:"):
            provider = find_custom_provider(self._app.config.data, active_platform) or {}
            pid = custom_platform_id(active_platform)
            api_key = self._app.config.get(f"custom_{pid}_api_key", "") if pid else ""
            platform_api_url = provider.get("api_url", "")
        else:
            api_key = self._app.config.get("api_key", "")
            platform_api_url = None

        proxy_mode = self._app.config.get("proxy_mode", "system")
        proxy_url = self._app.config.get("proxy_url", "")

        custom_fallback = None
        custom_models_api_url = None
        if active_platform.startswith("custom:"):
            provider = find_custom_provider(self._app.config.data, active_platform) or {}
            custom_fallback = provider.get("models", [])
            custom_models_api_url = provider.get("models_api_url", "")

        model_ids = fetch_available_models(
            api_key, proxy_mode, proxy_url,
            active_platform=active_platform,
            platform_api_url=platform_api_url,
            custom_fallback_models=custom_fallback,
            custom_models_api_url=custom_models_api_url,
            user_model_configs=self._app.config.get("model_configs", {})
        )

        # Only publish a global callback for the configured platform. Explicit
        # per-conversation requests return their result directly to avoid stale UI races.
        current_platform = self._app.config.get("active_platform", "claude")
        if model_ids:
            if current_platform == active_platform:
                self._app.available_models = model_ids
            if requested_platform is None and current_platform == active_platform and self._app.window:
                js_code = (
                    "if (window.onModelsUpdated) "
                    f"window.onModelsUpdated({json.dumps(model_ids)}, {json.dumps(active_platform)});"
                )
                try:
                    self._app.window.evaluate_js(js_code)
                except Exception:
                    pass
            return model_ids
        if current_platform == active_platform:
            self._app.available_models = []
        return []

    def list_custom_providers(self):
        """返回当前已注册的自定义提供商列表（API Key 以 has_ 标志替代明文）。"""
        with self._app.lock:
            providers = self._app.config.get("custom_providers", []) or []
            result = []
            for p in providers:
                item = dict(p)
                pid = sanitize_provider_id(p.get("id", ""))
                key_name = f"custom_{pid}_api_key"
                item["has_api_key"] = bool(self._app.config.get(key_name, "").strip())
                item["api_key"] = ""
                item["platform_id"] = f"custom:{pid}"
                result.append(item)
            return result

    def add_custom_provider(self, name, api_url, api_key="", models=None, temperature=0.7, max_tokens=4096, models_api_url=""):
        """
        新增一个自定义 OpenAI 兼容提供商并持久化保存到服务器配置。
        自动生成唯一 id（基于名称规整 + 短随机后缀以避免冲突）。
        models_api_url 可单独指定模型列表获取地址，留空则回退到 api_url。
        """
        with self._app.lock:
            import secrets as _secrets
            providers = list(self._app.config.get("custom_providers", []) or [])
            base_id = sanitize_provider_id(name) or "provider"
            # 生成唯一 id
            new_id = base_id
            existing_ids = {sanitize_provider_id(p.get("id", "")) for p in providers}
            while new_id in existing_ids:
                new_id = f"{base_id}_{_secrets.token_hex(2)}"
            provider = {
                "id": new_id,
                "name": name,
                "api_url": api_url,
                "models_api_url": models_api_url or "",
                "models": [m for m in (models or []) if isinstance(m, str) and m.strip()],
                "temperature": float(temperature),
                "max_tokens": int(max_tokens)
            }
            providers.append(provider)
            self._app.config.set("custom_providers", providers)
            if api_key:
                self._app.config.set(f"custom_{new_id}_api_key", api_key)
            logger.info(f"新增自定义提供商: {name} (id={new_id})")
            return self._provider_view(new_id)

    def update_custom_provider(self, provider_id, name=None, api_url=None, api_key=None, models=None, temperature=None, max_tokens=None, models_api_url=None, clear_api_key=False):
        """更新已有自定义提供商的元数据；api_key 仅在非空时覆盖；clear_api_key=True 时清除 Key。models_api_url 传 None 表示不修改，传空串表示清空。"""
        with self._app.lock:
            pid = sanitize_provider_id(provider_id)
            providers = list(self._app.config.get("custom_providers", []) or [])
            updated = False
            for p in providers:
                if sanitize_provider_id(p.get("id", "")) == pid:
                    if name is not None:
                        p["name"] = name
                    if api_url is not None:
                        p["api_url"] = api_url
                    if models_api_url is not None:
                        p["models_api_url"] = models_api_url
                    if models is not None:
                        p["models"] = [m for m in models if isinstance(m, str) and m.strip()]
                    if temperature is not None:
                        p["temperature"] = float(temperature)
                    if max_tokens is not None:
                        p["max_tokens"] = int(max_tokens)
                    updated = True
                    break
            if not updated:
                return {"error": f"未找到提供商: {pid}"}
            self._app.config.set("custom_providers", providers)
            # API Key 处理：clear_api_key 优先；否则非空才覆盖；都不动则保持原值
            if clear_api_key:
                self._app.config.set(f"custom_{pid}_api_key", "")
            elif api_key:
                self._app.config.set(f"custom_{pid}_api_key", api_key)
            logger.info(f"更新自定义提供商: id={pid}, clear_key={clear_api_key}")
            return self._provider_view(pid)

    def remove_custom_provider(self, provider_id):
        """删除自定义提供商，并清理其加密存储的 API Key 及 Keyring 中的孤立条目。"""
        with self._app.lock:
            pid = sanitize_provider_id(provider_id)
            providers = list(self._app.config.get("custom_providers", []) or [])
            new_providers = [p for p in providers if sanitize_provider_id(p.get("id", "")) != pid]
            if len(new_providers) == len(providers):
                return {"error": f"未找到提供商: {pid}"}
            self._app.config.set("custom_providers", new_providers)
            # 清理 API Key（置空触发 save 写入空值）
            key_name = f"custom_{pid}_api_key"
            self._app.config.set(key_name, "")
            # M12: 清理系统 Keyring 中的孤立密钥条目，防止安全残留
            try:
                import keyring
                keyring.delete_password("ClaudeChat", key_name)
            except Exception:
                pass  # Keyring 中不存在该项时静默忽略
            # 同时清理 config.data 中的 storage/obfuscated 元数据字段
            self._app.config.data.pop(f"{key_name}_storage", None)
            self._app.config.data.pop(f"{key_name}_obfuscated", None)
            self._app.config.save()
            # 若当前激活平台正是被删除的提供商，回退到 claude
            if self._app.config.get("active_platform", "") == f"custom:{pid}":
                self._app.config.set("active_platform", "claude")
            logger.info(f"删除自定义提供商: id={pid}")
            return {"success": True, "removed": pid}

    def _provider_view(self, pid):
        """构造单个提供商的安全视图（不含明文 Key）返回给前端。"""
        providers = self._app.config.get("custom_providers", []) or []
        for p in providers:
            if sanitize_provider_id(p.get("id", "")) == pid:
                item = dict(p)
                key_name = f"custom_{pid}_api_key"
                item["has_api_key"] = bool(self._app.config.get(key_name, "").strip())
                item["api_key"] = ""
                item["platform_id"] = f"custom:{pid}"
                return item
        return {"error": f"未找到提供商: {pid}"}
