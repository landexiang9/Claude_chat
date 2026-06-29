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

def parse_markdown_images_to_blocks(text):
    import re
    
    # Matches markdown base64 images: ![alt](data:image/png;base64,...)
    pattern = re.compile(
        r'!\[.*?\]\(data:(image/[a-zA-Z0-9\-\+\.]+);base64,([a-zA-Z0-9\/\+=\s\n\r]+)\)'
    )
    
    blocks = []
    last_end = 0
    text_str = str(text)
    
    for match in pattern.finditer(text_str):
        start, end = match.span()
        # Add preceding text block
        if start > last_end:
            preceding = text_str[last_end:start]
            if preceding:
                blocks.append({"type": "text", "text": preceding})
                
        mime_type = match.group(1)
        # Clean up whitespace/newlines from base64 data
        b64_data = match.group(2).strip().replace("\n", "").replace("\r", "").replace(" ", "")
        
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": b64_data
            }
        })
        
        last_end = end
        
    if last_end < len(text_str):
        remaining = text_str[last_end:]
        if remaining:
            blocks.append({"type": "text", "text": remaining})
            
    # If no blocks were created (no image matched), return list with single text block if not empty, or original text
    if not blocks:
        return [{"type": "text", "text": text_str}]
        
    return blocks

def preprocess_message_content(content):
    if isinstance(content, list):
        new_blocks = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if "![" in text and "data:image/" in text and ";base64," in text:
                    parsed = parse_markdown_images_to_blocks(text)
                    new_blocks.extend(parsed)
                else:
                    new_blocks.append(block)
            else:
                new_blocks.append(block)
        return new_blocks
    elif isinstance(content, str):
        if "![" in content and "data:image/" in content and ";base64," in content:
            return parse_markdown_images_to_blocks(content)
        return content
    return content

def stream_claude_response(api_key, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_config, streaming_queue, abort_event=None, on_stream_created=None, system=None, output_config=None, enable_search=False, enable_web_fetch=True, web_fetch_limit=15000, search_engine="google", tavily_api_key="", jina_api_key="", web_page_parser="local", conv_id=None, conv_manager=None, **kwargs):
    try:
        active_platform = kwargs.get("active_platform", "claude")

        # M4: 仅 Claude 平台做 base64 图片块预处理，DeepSeek/Custom 平台不做转换，
        # 否则 Anthropic 格式 image block 会被 convert_messages_to_openai 替换为 "[图片]"，
        # 既丢失图片数据也丢失原始 markdown 文本。
        processed_messages = []
        for msg in messages:
            content = msg.get("content")
            if active_platform == "claude":
                processed_content = preprocess_message_content(content)
            else:
                processed_content = content
            processed_msg = dict(msg)
            processed_msg["content"] = processed_content
            processed_messages.append(processed_msg)
        messages = processed_messages

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
                web_fetch_limit=web_fetch_limit,
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
            enable_code_sandbox = kwargs.get("gemini_enable_code_sandbox", False)
            code_sandbox_type = kwargs.get("gemini_code_sandbox_type", "local")
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
                enable_code_sandbox=enable_code_sandbox,
                code_sandbox_type=code_sandbox_type,
                streaming_queue=streaming_queue,
                abort_event=abort_event,
                on_stream_created=on_stream_created,
                system=system,
                enable_search=enable_search,
                conv_id=conv_id,
                conv_manager=conv_manager,
                previous_content_blocks=previous_content_blocks
            )
        elif active_platform.startswith("custom:"):
            # 自定义 OpenAI 兼容提供商：复用 DeepSeek (OpenAI SDK) 客户端
            custom_api_key = kwargs.get("custom_api_key", "")
            custom_api_url = kwargs.get("custom_api_url", "")
            return stream_deepseek_response(
                api_key=custom_api_key,
                api_url=custom_api_url,
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
                enable_search=False,
                search_engine="google",
                tavily_api_key="",
                jina_api_key="",
                web_page_parser="local",
                conv_id=conv_id,
                conv_manager=conv_manager,
                previous_content_blocks=previous_content_blocks,
                depth=depth
            )
        else:
            raise ValueError(f"Unknown active platform: {active_platform}")
    except Exception as e:
        logger.exception(f"stream_claude_response 发生错误: {e}")
        streaming_queue.put(("error", f"初始化请求失败: {sanitize_error_message(e)}"))

