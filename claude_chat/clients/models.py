import base64
import json
import logging
import sys
import threading
import time
from pathlib import Path

import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

from .base import build_http_client

logger = logging.getLogger("claude_chat.clients")

_REGISTRY_CACHE = None
_REGISTRY_LAST_FETCH = 0
_REGISTRY_LOCK = threading.RLock()

_FETCHING_REGISTRY = False

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
    BUNDLED_BASE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).parent.parent.parent
    BUNDLED_BASE_DIR = BASE_DIR

_REGISTRY_FILE = BASE_DIR / "models_registry.json"
_BUNDLED_REGISTRY_FILE = BUNDLED_BASE_DIR / "models_registry.json"

def _do_fetch_registry():
    global _REGISTRY_CACHE, _REGISTRY_LAST_FETCH, _FETCHING_REGISTRY
    try:
        response = httpx.get(
            "https://models.dev/models.json",
            headers={"User-Agent": "ClaudeChat"},
            timeout=15.0,
            follow_redirects=True
        )
        response.raise_for_status()
        data = response.json()
            
        registry = {}
        for mid, m in data.items():
            limit = m.get("limit", {})
            context_length = limit.get("context", 0)
            max_output = limit.get("output", 0)
            is_reasoner = m.get("reasoning", False)
            
            entry = {
                "max_context": context_length,
                "max_output": max_output,
                "thinking_supported": is_reasoner
            }
            registry[mid] = entry
            if "/" in mid:
                base_name = mid.split("/", 1)[1]
                if base_name not in registry:
                    registry[base_name] = entry

        with _REGISTRY_LOCK:
            _REGISTRY_CACHE = registry
            _REGISTRY_LAST_FETCH = time.time()
            
        # 异步保存到本地文件
        try:
            with open(_REGISTRY_FILE, "w", encoding="utf-8") as f:
                json.dump(registry, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save models registry to local file: {e}")
            
        return registry
    except Exception as e:
        logger.warning(f"Failed to fetch model registry from models.dev: {e}")
        return None
    finally:
        _FETCHING_REGISTRY = False

def fetch_model_registry():
    global _REGISTRY_CACHE, _REGISTRY_LAST_FETCH, _FETCHING_REGISTRY
    with _REGISTRY_LOCK:
        now = time.time()
        
        # 如果内存缓存为空，尝试先从本地文件加载兜底数据
        if _REGISTRY_CACHE is None:
            registry_candidates = list(dict.fromkeys((_REGISTRY_FILE, _BUNDLED_REGISTRY_FILE)))
            for registry_file in registry_candidates:
                if not registry_file.exists():
                    continue
                try:
                    with open(registry_file, "r", encoding="utf-8") as f:
                        _REGISTRY_CACHE = json.load(f)
                    break
                except Exception as e:
                    logger.warning(f"Failed to load model registry {registry_file}: {e}")
            if _REGISTRY_CACHE is None:
                _REGISTRY_CACHE = {}
        
        # 每次启动应用（或缓存过期）异步拉取更新一次
        if not _FETCHING_REGISTRY and (now - _REGISTRY_LAST_FETCH > 3600):
            _FETCHING_REGISTRY = True
            threading.Thread(target=_do_fetch_registry, daemon=True).start()
            
        return _REGISTRY_CACHE

def enrich_with_registry(model_list, user_model_configs=None):
    registry = fetch_model_registry()
    user_model_configs = user_model_configs or {}
    
    for m in model_list:
        mid = m["id"]
        reg_entry = registry.get(mid)
        # If not found, try lowercase
        if not reg_entry:
            reg_entry = registry.get(mid.lower())
        
        # User defined overrides take precedence for thinking
        user_config = user_model_configs.get(mid, {})
        user_thinking = user_config.get("user_defined_thinking_supported")
        
        if reg_entry:
            m["max_context"] = reg_entry.get("max_context", 0)
            m["max_output"] = reg_entry.get("max_output", 0)
            # If not already supported natively, rely on registry
            if not m.get("thinking_supported"):
                m["thinking_supported"] = reg_entry.get("thinking_supported", False)
                m["adaptive_supported"] = m["thinking_supported"]
                m["enabled_supported"] = m["thinking_supported"]
        else:
            m["max_context"] = m.get("max_context", 0)
            m["max_output"] = m.get("max_output", 0)

        # Apply user override if it exists
        if user_thinking is not None:
            m["thinking_supported"] = bool(user_thinking)
            m["adaptive_supported"] = bool(user_thinking)
            m["enabled_supported"] = bool(user_thinking)
            m["user_overridden"] = True
            
        if m["thinking_supported"] and not m["effort_levels"]:
             # Standard fallback effort levels for reasoning models
             m["effort_levels"] = ["low", "medium", "high"]
             
    return model_list

def get_model_capabilities(m):
    """
    解析 API 返回的模型对象，识别该模型是否支持思维推理能力（Extended Thinking/Thinking Mode）
    以及所支持的具体思考力度等级等级（effort levels: low, medium, high 等）
    """
    mid = m.id
    res = {
        "id": mid,
        "display_name": getattr(m, "display_name", mid),
        "thinking_supported": False,
        "adaptive_supported": False,
        "enabled_supported": False,
        "effort_levels": []
    }
    
    caps = getattr(m, "capabilities", None)
    if caps:
        thinking = getattr(caps, "thinking", None)
        if thinking and getattr(thinking, "supported", False):
            res["thinking_supported"] = True
            types = getattr(thinking, "types", None)
            if types:
                adaptive = getattr(types, "adaptive", None)
                if adaptive and getattr(adaptive, "supported", False):
                    res["adaptive_supported"] = True
                enabled = getattr(types, "enabled", None)
                if enabled and getattr(enabled, "supported", False):
                    res["enabled_supported"] = True
        
        effort = getattr(caps, "effort", None)
        if effort and getattr(effort, "supported", False):
            levels = []
            for lvl in ["low", "medium", "high", "xhigh", "max"]:
                lvl_support = getattr(effort, lvl, None)
                if lvl_support and getattr(lvl_support, "supported", False):
                    levels.append(lvl)
            res["effort_levels"] = levels
            
    if not res["thinking_supported"]:
        mid_lower = mid.lower()
        if (
            "3-7-sonnet" in mid_lower
            or "claude-3-7" in mid_lower
            or mid_lower.startswith("claude-sonnet-4")
            or mid_lower.startswith("claude-opus-4")
        ):
            res["thinking_supported"] = True
            res["adaptive_supported"] = True
            res["enabled_supported"] = True
            res["effort_levels"] = ["low", "medium", "high", "max"]
        elif mid_lower.startswith("claude-haiku-4"):
            res["thinking_supported"] = True
            res["enabled_supported"] = True
            
    return res

def get_default_capabilities(model_id):
    """
    针对未联网拉取到最新列表时使用的本地缓存备用模型列表，初始化其默认的能力字典结构
    """
    mid = model_id.lower()
    res = {
        "id": model_id,
        "display_name": model_id,
        "thinking_supported": False,
        "adaptive_supported": False,
        "enabled_supported": False,
        "effort_levels": []
    }
    if mid.startswith("claude-sonnet-4") or mid.startswith("claude-opus-4"):
        res["thinking_supported"] = True
        res["adaptive_supported"] = True
        res["enabled_supported"] = True
        res["effort_levels"] = ["low", "medium", "high", "max"]
        res["display_name"] = model_id.replace("claude-", "Claude ").replace("-", " ").title()
    elif mid.startswith("claude-haiku-4"):
        res["thinking_supported"] = True
        res["enabled_supported"] = True
        res["display_name"] = model_id.replace("claude-", "Claude ").replace("-", " ").title()
    elif "3-7-sonnet" in mid or "claude-3-7" in mid:
        res["thinking_supported"] = True
        res["adaptive_supported"] = True
        res["enabled_supported"] = True
        res["effort_levels"] = ["low", "medium", "high", "max"]
        res["display_name"] = "Claude 3.7 Sonnet"
    elif "3-5-sonnet" in mid:
        res["display_name"] = "Claude 3.5 Sonnet"
    elif "3-5-haiku" in mid:
        res["display_name"] = "Claude 3.5 Haiku"
    elif "3-opus" in mid:
        res["display_name"] = "Claude 3 Opus"
        
    return res

def fetch_available_models(api_key, proxy_mode, proxy_url, active_platform="claude", platform_api_url=None, custom_fallback_models=None, custom_models_api_url=None, user_model_configs=None):
    """
    根据选定的平台类型，拉取对应的活跃模型列表及其详细能力结构。
    """
    if active_platform == "claude":
        if not api_key:
            return []
        try:
            http_client = build_http_client(proxy_mode, proxy_url)
            client = Anthropic(api_key=api_key, http_client=http_client, timeout=30.0)
            models = client.models.list()
            
            models_data = []
            
            for m in models.data:
                mid = m.id
                if hasattr(m, 'deprecation_date') and m.deprecation_date:
                    continue
                
                m_cap = get_model_capabilities(m)
                models_data.append(m_cap)
                
            return enrich_with_registry(models_data, user_model_configs)
        except Exception as e:
            logger.warning(f"拉取 Claude 模型列表失败: {e}")
            return []
            
    elif active_platform == "deepseek":
        fallback_deepseek = [
            {
                "id": "deepseek-chat",
                "display_name": "DeepSeek V3 (deepseek-chat)",
                "thinking_supported": False,
                "adaptive_supported": False,
                "enabled_supported": False,
                "effort_levels": []
            },
            {
                "id": "deepseek-reasoner",
                "display_name": "DeepSeek R1 (deepseek-reasoner)",
                "thinking_supported": True,
                "adaptive_supported": True,
                "enabled_supported": True,
                "effort_levels": []
            }
        ]
        if not api_key:
            return enrich_with_registry(fallback_deepseek, user_model_configs)
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=platform_api_url or "https://api.deepseek.com", timeout=30.0)
            models = client.models.list()
            res = []
            for m in models.data:
                mid = m.id
                is_reasoner = "deepseek-reasoner" in mid.lower() or "reasoner" in mid.lower()
                res.append({
                    "id": mid,
                    "display_name": mid,
                    "thinking_supported": is_reasoner,
                    "adaptive_supported": is_reasoner,
                    "enabled_supported": is_reasoner,
                    "effort_levels": []
                })
            return enrich_with_registry(res if res else fallback_deepseek, user_model_configs)
        except Exception as e:
            logger.warning(f"拉取 DeepSeek 模型列表失败: {e}")
            return enrich_with_registry(fallback_deepseek, user_model_configs)
        
    elif active_platform == "gemini":
        fallback_gemini = [
            {"id": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash", "thinking_supported": True, "adaptive_supported": True, "enabled_supported": True, "effort_levels": []},
            {"id": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro", "thinking_supported": True, "adaptive_supported": True, "enabled_supported": True, "effort_levels": []},
            {"id": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash (Preview)", "thinking_supported": True, "adaptive_supported": True, "enabled_supported": True, "effort_levels": []},
            {"id": "gemini-1.5-pro", "display_name": "Gemini 1.5 Pro", "thinking_supported": False, "adaptive_supported": False, "enabled_supported": False, "effort_levels": []},
            {"id": "gemini-1.5-flash", "display_name": "Gemini 1.5 Flash", "thinking_supported": False, "adaptive_supported": False, "enabled_supported": False, "effort_levels": []}
        ]
        
        if not api_key:
            return enrich_with_registry(fallback_gemini, user_model_configs)
            
        try:
            from google import genai
            from google.genai import types
            import httpx
            
            http_options_kwargs = {}
            if platform_api_url and isinstance(platform_api_url, str) and platform_api_url.strip():
                http_options_kwargs["api_endpoint"] = platform_api_url.strip()
            if proxy_mode == "custom" and isinstance(proxy_url, str) and proxy_url.strip():
                http_options_kwargs["client_args"] = {"transport": httpx.HTTPTransport(proxy=proxy_url.strip(), timeout=30.0)}
                http_options_kwargs["async_client_args"] = {"transport": httpx.AsyncHTTPTransport(proxy=proxy_url.strip(), timeout=30.0)}
            elif proxy_mode == "none":
                http_options_kwargs["client_args"] = {"trust_env": False, "timeout": httpx.Timeout(30.0, connect=10.0)}
                http_options_kwargs["async_client_args"] = {"trust_env": False, "timeout": httpx.Timeout(30.0, connect=10.0)}
                
            client_kwargs = {"api_key": api_key}
            if http_options_kwargs:
                client_kwargs["http_options"] = types.HttpOptions(**http_options_kwargs)
                
            client = genai.Client(**client_kwargs)
            models_list = client.models.list()
            
            gemini_models = []
            for m in models_list:
                actions = getattr(m, "supported_actions", [])
                if actions and "generateContent" not in actions:
                    continue
                    
                mid = m.name
                clean_id = mid.replace("models/", "")
                display = m.display_name if getattr(m, "display_name", None) else clean_id
                
                is_thinking_supported = False
                lower_id = clean_id.lower()
                if "gemini-2.0" in lower_id or "gemini-2.5" in lower_id or "gemini-3" in lower_id:
                    is_thinking_supported = True
                    
                gemini_models.append({
                    "id": clean_id,
                    "display_name": display,
                    "thinking_supported": is_thinking_supported,
                    "adaptive_supported": is_thinking_supported,
                    "enabled_supported": is_thinking_supported,
                    "effort_levels": []
                })
                
            if gemini_models:
                return enrich_with_registry(gemini_models, user_model_configs)
            return enrich_with_registry(fallback_gemini, user_model_configs)
        except Exception as e:
            logger.warning(f"拉取 Gemini 模型列表失败: {e}")
            return enrich_with_registry(fallback_gemini, user_model_configs)

    elif active_platform.startswith("custom:"):
        # 自定义 OpenAI 兼容提供商：用户可在设置中预置模型列表作为回退
        fallback_custom = []
        if custom_fallback_models:
            for mid in custom_fallback_models:
                if isinstance(mid, str) and mid.strip():
                    fallback_custom.append({
                        "id": mid.strip(),
                        "display_name": mid.strip(),
                        "thinking_supported": False,
                        "adaptive_supported": False,
                        "enabled_supported": False,
                        "effort_levels": []
                    })
        if not api_key:
            return enrich_with_registry(fallback_custom, user_model_configs)
        try:
            from openai import OpenAI
            # 模型列表获取地址：优先使用单独配置的 models_api_url，否则回退到聊天 api_url
            models_base_url = custom_models_api_url.strip() if custom_models_api_url and custom_models_api_url.strip() else platform_api_url
            client = OpenAI(api_key=api_key, base_url=models_base_url, timeout=30.0)
            models = client.models.list()
            res = []
            for m in models.data:
                mid = m.id
                is_reasoner = False # Will be determined by registry or user config
                res.append({
                    "id": mid,
                    "display_name": mid,
                    "thinking_supported": is_reasoner,
                    "adaptive_supported": is_reasoner,
                    "enabled_supported": is_reasoner,
                    "effort_levels": []
                })
            return enrich_with_registry(res if res else fallback_custom, user_model_configs)
        except Exception as e:
            logger.warning(f"拉取自定义提供商模型列表失败: {e}")
            return enrich_with_registry(fallback_custom, user_model_configs)

    return []

