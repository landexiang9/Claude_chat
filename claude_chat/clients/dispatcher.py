import base64
import json
import logging
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

logger = logging.getLogger("claude_chat.clients")

from .claude import stream_claude_response_native
from .deepseek import stream_deepseek_response
from .gemini import stream_gemini_response
from .base import sanitize_error_message

def stream_claude_response(api_key, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_config, streaming_queue, abort_event=None, on_stream_created=None, system=None, output_config=None, enable_search=False, enable_web_fetch=True, web_fetch_limit=15000, search_engine="google", tavily_api_key="", jina_api_key="", web_page_parser="local", conv_id=None, conv_manager=None, **kwargs):
    try:
        active_platform = kwargs.get("active_platform", "claude")
        depth = kwargs.get("depth", 0)
        previous_content_blocks = kwargs.get("previous_content_blocks")
        
        if active_platform == "claude":
            return stream_claude_response_native(
                api_key=api_key,
                proxy_mode=proxy_mode,
                proxy_url=proxy_url,
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_config=thinking_config,
                streaming_queue=streaming_queue,
                abort_event=abort_event,
                on_stream_created=on_stream_created,
                system=system,
                output_config=output_config,
                enable_search=enable_search,
                enable_web_fetch=enable_web_fetch,
                web_fetch_limit=web_fetch_limit,
                search_engine=search_engine,
                tavily_api_key=tavily_api_key,
                jina_api_key=jina_api_key,
                web_page_parser=web_page_parser,
                conv_id=conv_id,
                conv_manager=conv_manager,
                depth=depth
            )
        elif active_platform == "deepseek":
            deepseek_api_key = kwargs.get("deepseek_api_key", "")
            deepseek_api_url = kwargs.get("deepseek_api_url", "https://api.deepseek.com")
            return stream_deepseek_response(
                api_key=deepseek_api_key,
                api_url=deepseek_api_url,
                proxy_mode=proxy_mode,
                proxy_url=proxy_url,
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                streaming_queue=streaming_queue,
                abort_event=abort_event,
                on_stream_created=on_stream_created,
                system=system,
                enable_search=enable_search,
                search_engine=search_engine,
                tavily_api_key=tavily_api_key,
                jina_api_key=jina_api_key,
                web_page_parser=web_page_parser,
                conv_id=conv_id,
                conv_manager=conv_manager,
                previous_content_blocks=previous_content_blocks,
                depth=depth
            )
        elif active_platform == "gemini":
            gemini_api_key = kwargs.get("gemini_api_key", "")
            gemini_api_url = kwargs.get("gemini_api_url", "")
            thinking_enabled = kwargs.get("thinking_enabled", False)
            thinking_budget = kwargs.get("thinking_budget", 1024)
            thinking_level = kwargs.get("thinking_level", "high")
            return stream_gemini_response(
                api_key=gemini_api_key,
                api_url=gemini_api_url,
                proxy_mode=proxy_mode,
                proxy_url=proxy_url,
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
                thinking_level=thinking_level,
                streaming_queue=streaming_queue,
                abort_event=abort_event,
                on_stream_created=on_stream_created,
                system=system,
                enable_search=enable_search,
                conv_id=conv_id,
                conv_manager=conv_manager,
                previous_content_blocks=previous_content_blocks
            )
        else:
            raise ValueError(f"Unknown active platform: {active_platform}")
    except Exception as e:
        logger.exception(f"stream_claude_response 发生错误: {e}")
        streaming_queue.put(("error", f"初始化请求失败: {sanitize_error_message(e)}"))

