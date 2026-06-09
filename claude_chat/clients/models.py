import base64
import json
import logging
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

logger = logging.getLogger("claude_chat.clients")

from .base import build_http_client

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
        if "3-7-sonnet" in mid_lower or "claude-3-7" in mid_lower:
            res["thinking_supported"] = True
            res["adaptive_supported"] = True
            res["enabled_supported"] = True
            res["effort_levels"] = ["low", "medium", "high", "max"]
            
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
    if "3-7-sonnet" in mid or "claude-3-7" in mid:
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

def fetch_available_models(api_key, proxy_mode, proxy_url, active_platform="claude", platform_api_url=None):
    """
    根据选定的平台类型，拉取对应的活跃模型列表及其详细能力结构。
    """
    if active_platform == "claude":
        if not api_key:
            return []
        try:
            http_client = build_http_client(proxy_mode, proxy_url)
            client = Anthropic(api_key=api_key, http_client=http_client)
            models = client.models.list()
            
            models_data = []
            has_opus_47 = False
            
            for m in models.data:
                mid = m.id
                if hasattr(m, 'deprecation_date') and m.deprecation_date:
                    continue
                
                m_cap = get_model_capabilities(m)
                models_data.append(m_cap)
                if "claude-3-7-sonnet" in mid.lower():
                    has_opus_47 = True
                    
            if not has_opus_47:
                models_data.insert(0, get_default_capabilities("claude-3-7-sonnet-latest"))
                
            return models_data
        except Exception:
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
            return fallback_deepseek
        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=platform_api_url or "https://api.deepseek.com")
            models = client.models.list()
            res = []
            for m in models.data:
                mid = m.id
                is_reasoner = "pro" in mid.lower() or "reasoner" in mid.lower()
                res.append({
                    "id": mid,
                    "display_name": mid,
                    "thinking_supported": is_reasoner,
                    "adaptive_supported": is_reasoner,
                    "enabled_supported": is_reasoner,
                    "effort_levels": []
                })
            return res if res else fallback_deepseek
        except Exception:
            return fallback_deepseek
        
    elif active_platform == "gemini":
        fallback_gemini = [
            {"id": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash", "thinking_supported": True, "adaptive_supported": True, "enabled_supported": True, "effort_levels": []},
            {"id": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro", "thinking_supported": True, "adaptive_supported": True, "enabled_supported": True, "effort_levels": []},
            {"id": "gemini-3.5-flash", "display_name": "Gemini 3.5 Flash (Preview)", "thinking_supported": True, "adaptive_supported": True, "enabled_supported": True, "effort_levels": []},
            {"id": "gemini-1.5-pro", "display_name": "Gemini 1.5 Pro", "thinking_supported": False, "adaptive_supported": False, "enabled_supported": False, "effort_levels": []},
            {"id": "gemini-1.5-flash", "display_name": "Gemini 1.5 Flash", "thinking_supported": False, "adaptive_supported": False, "enabled_supported": False, "effort_levels": []}
        ]
        
        if not api_key:
            return fallback_gemini
            
        try:
            from google import genai
            from google.genai import types
            import httpx
            
            http_options_kwargs = {}
            if platform_api_url and platform_api_url.strip():
                http_options_kwargs["api_endpoint"] = platform_api_url.strip()
            if proxy_mode == "custom" and proxy_url.strip():
                http_options_kwargs["client_args"] = {"transport": httpx.HTTPTransport(proxy=proxy_url.strip())}
                http_options_kwargs["async_client_args"] = {"transport": httpx.AsyncHTTPTransport(proxy=proxy_url.strip())}
            elif proxy_mode == "none":
                http_options_kwargs["client_args"] = {"trust_env": False}
                http_options_kwargs["async_client_args"] = {"trust_env": False}
                
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
                return gemini_models
            return fallback_gemini
        except Exception:
            return fallback_gemini
            
    return []

