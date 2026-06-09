import base64
import json
import logging
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

logger = logging.getLogger("claude_chat.clients")

from .base import extract_api_message, build_http_client, sanitize_error_message

def convert_messages_to_gemini(messages):
    import json
    gemini_msgs = []
    
    # Helper to find function name by tool_use_id in the message history
    def find_function_name(tool_use_id):
        for prev_msg in messages:
            prev_content = prev_msg.get("content")
            if isinstance(prev_content, list):
                for prev_block in prev_content:
                    if isinstance(prev_block, dict) and prev_block.get("type") in ("tool_use", "server_tool_use"):
                        if prev_block.get("id") == tool_use_id:
                            return prev_block.get("name", "")
        return "search_web" # fallback
        
    for msg in messages:
        role = msg.get("role")
        gemini_role = "model" if role == "assistant" else "user"
        
        parts = []
        content = msg.get("content")
        
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        parts.append({"text": block.get("text", "")})
                    elif btype in ("image", "document"):
                        source = block.get("source", {})
                        mime_type = source.get("media_type")
                        if not mime_type:
                            mime_type = "application/pdf" if btype == "document" else "image/png"
                        
                        raw_bytes = None
                        if "file_path" in source:
                            try:
                                with open(source["file_path"], "rb") as f:
                                    raw_bytes = f.read()
                            except Exception:
                                pass
                        elif "data" in source:
                            try:
                                raw_bytes = base64.b64decode(source["data"])
                            except Exception:
                                pass
                        
                        if raw_bytes:
                            parts.append({
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": raw_bytes
                                }
                            })
                        else:
                            parts.append({"text": f"[{btype} attachment unavailable]"})
                    elif btype == "thinking":
                        parts.append({"text": f"[Thinking: {block.get('thinking', '')}]"})
                    elif btype in ("tool_use", "server_tool_use"):
                        parts.append({
                            "function_call": {
                                "name": block.get("name", ""),
                                "args": block.get("input", {})
                            }
                        })
                    elif btype in ("tool_result", "web_search_tool_result"):
                        func_name = find_function_name(block.get("tool_use_id", ""))
                        content_val = block.get("content", "")
                        response_data = {"result": content_val}
                        if isinstance(content_val, str):
                            try:
                                response_data = json.loads(content_val)
                                if not isinstance(response_data, dict):
                                    response_data = {"result": response_data}
                            except Exception:
                                pass
                        parts.append({
                            "function_response": {
                                "name": func_name,
                                "response": response_data
                            }
                        })
                else:
                    parts.append({"text": str(block)})
        else:
            parts.append({"text": str(content)})
            
        gemini_msgs.append({"role": gemini_role, "parts": parts})
    return gemini_msgs

def stream_gemini_response(api_key, api_url, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_enabled, thinking_budget, thinking_level, streaming_queue, abort_event=None, on_stream_created=None, system=None, enable_search=False, conv_id=None, conv_manager=None, previous_content_blocks=None):
    try:
        import sys
        use_legacy = False
        if "google.generativeai" in sys.modules:
            try:
                import google.generativeai as legacy_genai
                if hasattr(legacy_genai.GenerativeModel, "mock_calls") or "Mock" in str(type(legacy_genai.GenerativeModel)):
                    use_legacy = True
            except Exception:
                pass
                
        full_text = ""
        full_reasoning = ""
        input_tokens = 0
        output_tokens = 0
        content_blocks = []
        
        if use_legacy:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            
            legacy_msgs = []
            for msg in messages:
                role = msg.get("role")
                genai_role = "model" if role == "assistant" else "user"
                
                parts = []
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            btype = block.get("type")
                            if btype == "text":
                                parts.append(block.get("text", ""))
                            else:
                                parts.append(f"[{btype}]")
                        else:
                            parts.append(str(block))
                else:
                    parts.append(str(content))
                legacy_msgs.append({"role": genai_role, "parts": parts})
                
            generation_config = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            
            tools = None
            if enable_search:
                tools = ["google_search_retrieval"]
                
            model_instance = genai.GenerativeModel(model_name=model, tools=tools)
            response_stream = model_instance.generate_content(
                legacy_msgs,
                generation_config=generation_config,
                stream=True
            )
            if on_stream_created:
                on_stream_created(response_stream)
                
            for chunk in response_stream:
                if abort_event and abort_event.is_set():
                    streaming_queue.put(("aborted", {}))
                    return
                    
                if hasattr(chunk, "candidates") and chunk.candidates:
                    candidate = chunk.candidates[0]
                    if hasattr(candidate, "content") and candidate.content:
                        for part in candidate.content.parts:
                            is_thought = False
                            text = ""
                            
                            if hasattr(part, "ListFields"):
                                for field, value in part.ListFields():
                                    if field.name == "thought":
                                        is_thought = True
                                        text = value
                                    elif field.name == "text":
                                        text = value
                            else:
                                text = getattr(part, "text", "")
                                is_thought = getattr(part, "thought", False)
                                
                            if is_thought:
                                if text:
                                    full_reasoning += text
                                    streaming_queue.put(("thinking", text))
                            else:
                                if text:
                                    full_text += text
                                    streaming_queue.put(("text", text))
                                    
            input_tokens = len(str(legacy_msgs)) // 4
            output_tokens = len(full_text) // 4
            
        else:
            # Modern google-genai path
            from google import genai
            from google.genai import types
            
            http_options_kwargs = {}
            if api_url and api_url.strip():
                http_options_kwargs["api_endpoint"] = api_url.strip()
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
            
            gemini_msgs = convert_messages_to_gemini(messages)
            
            config_args = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }
            if system and system.strip():
                config_args["system_instruction"] = system.strip()
            if enable_search:
                config_args["tools"] = [types.Tool(google_search=types.GoogleSearch())]
            if thinking_enabled:
                budget_val = thinking_budget if thinking_budget else 1024
                if "gemini-3" in model.lower():
                    config_args["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
                else:
                    config_args["thinking_config"] = types.ThinkingConfig(thinking_budget=budget_val)
                    
            if "image" in model.lower():
                config_args["response_modalities"] = ["IMAGE", "TEXT"]
                config_args["image_config"] = types.ImageConfig(image_size="2K")
                    
            gen_config = types.GenerateContentConfig(**config_args)
            
            response_stream = client.models.generate_content_stream(
                model=model,
                contents=gemini_msgs,
                config=gen_config
            )
            if on_stream_created:
                on_stream_created(response_stream)
                
            grounding_meta = None
            
            for chunk in response_stream:
                if abort_event and abort_event.is_set():
                    streaming_queue.put(("aborted", {}))
                    return
                    
                if chunk.candidates and chunk.candidates[0].content:
                    parts = chunk.candidates[0].content.parts
                    if parts:
                        for part in parts:
                            text = getattr(part, "text", "")
                            is_thought = getattr(part, "thought", False)
                            inline_data = getattr(part, "inline_data", None)
                            
                            if inline_data and hasattr(inline_data, "data") and inline_data.data:
                                import base64
                                b64_data = base64.b64encode(inline_data.data).decode("utf-8")
                                mime_type = getattr(inline_data, "mime_type", "image/png")
                                # Render as markdown image directly
                                img_md = f"\n\n![Generated Image](data:{mime_type};base64,{b64_data})\n\n"
                                full_text += img_md
                                streaming_queue.put(("text", img_md))
                            elif is_thought:
                                if text:
                                    full_reasoning += text
                                    streaming_queue.put(("thinking", text))
                            else:
                                if text:
                                    full_text += text
                                    streaming_queue.put(("text", text))
                                    
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    um = chunk.usage_metadata
                    if hasattr(um, "prompt_token_count") and um.prompt_token_count:
                        input_tokens = um.prompt_token_count
                    if hasattr(um, "candidates_token_count") and um.candidates_token_count:
                        output_tokens = um.candidates_token_count

                if chunk.candidates and chunk.candidates[0].grounding_metadata:
                    grounding_meta = chunk.candidates[0].grounding_metadata

            if grounding_meta and enable_search:
                queries = getattr(grounding_meta, "web_search_queries", [])
                chunks = getattr(grounding_meta, "grounding_chunks", [])
                
                search_results = []
                if chunks:
                    for c in chunks:
                        if c.web:
                            search_results.append({
                                "title": c.web.title,
                                "url": c.web.uri,
                                "snippet": ""
                            })
                
                if queries:
                    for q in queries:
                        streaming_queue.put(("search_start", {"query": q}))
                        streaming_queue.put(("search_done", {
                            "query": q,
                            "results": search_results,
                            "engine": "google",
                            "usage": None
                        }))
                        
            if input_tokens == 0:
                input_tokens = len(str(gemini_msgs)) // 4
            if output_tokens == 0:
                output_tokens = len(full_text) // 4

        content_blocks = [{"type": "text", "text": full_text}]
        if full_reasoning:
            content_blocks.insert(0, {
                "type": "thinking",
                "thinking": full_reasoning,
                "signature": "omitted_for_display"
            })
            
        if previous_content_blocks:
            content_blocks = previous_content_blocks + content_blocks
            
        streaming_queue.put(("done", {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "content_blocks": content_blocks,
            "thinking": full_reasoning
        }))
        
    except Exception as e:
        logger.exception(f"Gemini streaming error: {e}")
        streaming_queue.put(("error", f"Gemini 错误: {sanitize_error_message(e)}"))

