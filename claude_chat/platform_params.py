"""Normalize per-provider configuration into the shared client parameter shape."""

from claude_chat.custom_params import validate_custom_params
from claude_chat.config import DEFAULT_MAX_TOKENS, custom_platform_id, find_custom_provider


class PlatformParamMapper:
    @staticmethod
    def map_params(active_platform, config, model_id=None):
        model_config = config.get("model_configs", {}).get(model_id, {}) if model_id else {}
        params = {
            "request_params": model_config.get("request_params"),
            "custom_params": validate_custom_params(model_config.get("custom_params", {})),
            "api_key": "",
            "max_tokens": DEFAULT_MAX_TOKENS,
            "temperature": 0.7,
            "enable_search": False,
            "enable_web_fetch": True,
            "web_fetch_limit": 15000,
            "search_engine": "google",
            "tavily_api_key": "",
            "jina_api_key": "",
            "web_page_parser": "local",
            "thinking_config": None,
            "output_config": None,
            "api_url": "",
            "enable_code_sandbox": False,
            "code_sandbox_type": "local",
            "file_upload_enabled": True,
            "file_upload_expires_in_seconds": 172800,
            "file_upload_purpose": "user_data",
        }

        if active_platform == "claude":
            params.update(
                api_key=config.get("api_key", ""),
                max_tokens=int(model_config.get("max_tokens", config.get("max_tokens", DEFAULT_MAX_TOKENS))),
                temperature=float(model_config.get("temperature", config.get("temperature", 0.7))),
                enable_search=bool(config.get("enable_web_search", False)),
                enable_web_fetch=bool(config.get("enable_web_fetch", True)),
                web_fetch_limit=int(config.get("web_fetch_limit", 15000)),
                search_engine=config.get("web_search_engine", "google"),
                tavily_api_key=config.get("tavily_api_key", ""),
                jina_api_key=config.get("jina_api_key", ""),
                web_page_parser=config.get("web_page_parser", "local"),
                file_upload_enabled=bool(config.get("claude_file_upload_enabled", True)),
                file_upload_expires_in_seconds=max(
                    3600,
                    min(7776000, int(config.get("claude_file_upload_expires_in_seconds", 172800))),
                ),
            )
            if model_config.get("thinking_enabled", config.get("thinking_enabled", False)):
                thinking_type = model_config.get("thinking_type", config.get("thinking_type", "adaptive"))
                if thinking_type == "adaptive":
                    params["thinking_config"] = {"type": "adaptive"}
                    effort = model_config.get("thinking_level", config.get("thinking_level", "high"))
                    if effort:
                        params["output_config"] = {"effort": effort}
                elif thinking_type == "enabled":
                    budget = model_config.get("thinking_budget", config.get("thinking_budget", 16000))
                    params["thinking_config"] = {"type": "enabled", "budget_tokens": int(budget)}

        elif active_platform == "deepseek":
            params.update(
                api_key=config.get("deepseek_api_key", ""),
                api_url=config.get("deepseek_api_url", "https://api.deepseek.com"),
                max_tokens=int(
                    model_config.get("max_tokens", config.get("deepseek_max_tokens", DEFAULT_MAX_TOKENS))
                ),
                temperature=float(model_config.get("temperature", config.get("deepseek_temperature", 0.7))),
                enable_search=bool(config.get("deepseek_enable_web_search", False)),
                enable_web_fetch=bool(config.get("deepseek_enable_web_fetch", True)),
                web_fetch_limit=int(config.get("deepseek_web_fetch_limit", 15000)),
                search_engine=config.get("deepseek_web_search_engine", "google"),
                tavily_api_key=config.get("deepseek_tavily_api_key", ""),
                jina_api_key=config.get("deepseek_jina_api_key", ""),
                web_page_parser=config.get("deepseek_web_page_parser", "local"),
                file_upload_enabled=bool(config.get("deepseek_file_upload_enabled", True)),
                file_upload_expires_in_seconds=max(
                    3600,
                    min(2592000, int(config.get("deepseek_file_upload_expires_in_seconds", 172800))),
                ),
            )
            if model_config.get("thinking_enabled", config.get("deepseek_thinking_enabled", False)):
                effort = model_config.get("thinking_level", config.get("deepseek_thinking_level", "high"))
                params["thinking_config"] = {"effort": effort}

        elif active_platform == "gemini":
            params.update(
                api_key=config.get("gemini_api_key", ""),
                api_url=config.get("gemini_api_url", ""),
                max_tokens=int(
                    model_config.get("max_tokens", config.get("gemini_max_tokens", DEFAULT_MAX_TOKENS))
                ),
                temperature=float(model_config.get("temperature", config.get("gemini_temperature", 0.7))),
                enable_search=bool(config.get("gemini_enable_web_search", False)),
                enable_code_sandbox=bool(config.get("gemini_enable_code_sandbox", False)),
                code_sandbox_type=config.get("gemini_code_sandbox_type", "local"),
                file_upload_enabled=bool(config.get("gemini_file_upload_enabled", True)),
            )
            if model_config.get("thinking_enabled", config.get("gemini_thinking_enabled", False)):
                params["thinking_config"] = {
                    "enabled": True,
                    "budget_tokens": int(
                        model_config.get("thinking_budget", config.get("gemini_thinking_budget", 1024))
                    ),
                    "effort": model_config.get("thinking_level", config.get("gemini_thinking_level", "high")),
                }

        elif active_platform.startswith("custom:"):
            provider = find_custom_provider(config, active_platform) or {}
            provider_id = custom_platform_id(active_platform)
            params.update(
                api_key=config.get(f"custom_{provider_id}_api_key", "") if provider_id else "",
                api_url=provider.get("api_url", ""),
                max_tokens=int(model_config.get("max_tokens", provider.get("max_tokens", 4096))),
                temperature=float(model_config.get("temperature", provider.get("temperature", 0.7))),
                enable_search=False,
                enable_web_fetch=False,
                file_upload_enabled=bool(provider.get("file_upload_enabled", False)),
                file_upload_purpose=provider.get("file_upload_purpose", "user_data") or "user_data",
                file_upload_expires_in_seconds=max(
                    3600,
                    min(2592000, int(provider.get("file_upload_expires_in_seconds", 172800))),
                ),
            )
            if model_config.get("thinking_enabled", provider.get("thinking_enabled", False)):
                params["thinking_config"] = {
                    "effort": model_config.get("thinking_level", provider.get("thinking_level", "high"))
                }

        return params
