import base64
import json
import logging
from claude_chat.request_params import generation_params
from claude_chat.clients.file_uploads import prepare_gemini_files, upload_cache_namespace
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
                    elif btype in ("image", "document", "file", "audio", "video"):
                        source = block.get("source", {})
                        mime_type = source.get("media_type")
                        
                        # Use standard mimetypes guess for file path
                        if not mime_type and "file_path" in source:
                            import mimetypes
                            mime_type, _ = mimetypes.guess_type(source["file_path"])
                            
                        if not mime_type:
                            mime_type = "application/pdf" if btype == "document" else "image/png"
                        
                        raw_bytes = None
                        if source.get("file_uri"):
                            parts.append({
                                "file_data": {
                                    "file_uri": source["file_uri"],
                                    "mime_type": mime_type,
                                }
                            })
                            continue
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
                        # Skip thinking blocks in multi-turn history to avoid Gemini API thought_signature verification errors
                        pass
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
            
        if gemini_role == "model":
            for part in parts:
                if isinstance(part, dict):
                    part["thought_signature"] = "skip_thought_signature_validator"
            
        gemini_msgs.append({"role": gemini_role, "parts": parts})
    return gemini_msgs

def stream_gemini_response(api_key, api_url, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_enabled, thinking_budget, thinking_level, streaming_queue, abort_event=None, on_stream_created=None, system=None, enable_search=False, conv_id=None, conv_manager=None, previous_content_blocks=None, enable_code_sandbox=False, code_sandbox_type="local", **kwargs):
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

        # 流式思考标签解析器:gemini-3.5-flash 等 preview 模型不在协议层分离思考,
        # 而是把 <thought>...</thought> 写在正文 text part 里(thought=False)。此处在
        # 正文通道上兜底解析。若 part.thought 已为 True(SDK 原生分离),直接走 thinking,
        # 不经过解析器,不会受影响。见 thinking_tag_parser。
        from .thinking_tag_parser import ThinkingTagStreamParser
        tag_parser = ThinkingTagStreamParser()
        
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
                            elif btype == "thinking":
                                pass
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
            
            generation_config = generation_params(
                "gemini", model, max_tokens, temperature,
                custom=kwargs.get("custom_params"), override=kwargs.get("request_params"))

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
                                    # 正文里可能含 <thought>...</thought>(gemini-3.5-flash
                                    # preview 未在协议层分离时),经解析器兜底拆分
                                    text_part, thinking_part = tag_parser.feed(text)
                                    if thinking_part:
                                        full_reasoning += thinking_part
                                        streaming_queue.put(("thinking", thinking_part))
                                    if text_part:
                                        full_text += text_part
                                        streaming_queue.put(("text", text_part))
                                    
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

            if kwargs.get("file_upload_enabled", True):
                messages = prepare_gemini_files(
                    client,
                    messages,
                    upload_cache_namespace("gemini", api_key, api_url),
                )
            gemini_msgs = convert_messages_to_gemini(messages)
            
            config_args = generation_params(
                "gemini", model, max_tokens, temperature,
                {"enabled": thinking_enabled, "budget_tokens": thinking_budget, "effort": thinking_level},
                custom=kwargs.get("custom_params"), override=kwargs.get("request_params"))
            if system and system.strip():
                config_args["system_instruction"] = system.strip()
            
            # Setup tools (Google search grounding & native cloud execution sandbox)
            tools = []
            if enable_search:
                tools.append(types.Tool(google_search=types.GoogleSearch()))
            if enable_code_sandbox and code_sandbox_type == "cloud":
                tools.append(types.Tool(code_execution=types.CodeExecution()))
                
            if tools:
                config_args["tools"] = tools
                
            gen_config = types.GenerateContentConfig(**config_args)
            
            response_stream = client.models.generate_content_stream(
                model=model,
                contents=gemini_msgs,
                config=gen_config
            )
            if on_stream_created:
                on_stream_created(response_stream)
                
            grounding_meta = None
            
            # Code execution sandbox state tracking variables
            in_code_block = False
            in_result_block = False
            last_code = ""
            last_result = ""
            
            for chunk in response_stream:
                if abort_event and abort_event.is_set():
                    streaming_queue.put(("aborted", {}))
                    return
                    
                if chunk.candidates and chunk.candidates[0].content:
                    parts = chunk.candidates[0].content.parts
                    if parts:
                        for part in parts:
                            executable_code = getattr(part, "executable_code", None)
                            code_execution_result = getattr(part, "code_execution_result", None)
                            text = getattr(part, "text", "")
                            is_thought = getattr(part, "thought", False)
                            inline_data = getattr(part, "inline_data", None)
                            
                            if executable_code:
                                if in_result_block:
                                    streaming_queue.put(("text", "\n```\n"))
                                    in_result_block = False
                                    
                                code = getattr(executable_code, "code", "")
                                if code:
                                    if not in_code_block:
                                        streaming_queue.put(("text", "\n```python\n# [云端沙盒执行]\n"))
                                        in_code_block = True
                                        
                                    if code.startswith(last_code):
                                        new_code = code[len(last_code):]
                                    else:
                                        new_code = code
                                    last_code = code
                                    if new_code:
                                        full_text += new_code
                                        streaming_queue.put(("text", new_code))
                                        
                            elif code_execution_result:
                                if in_code_block:
                                    streaming_queue.put(("text", "\n```\n"))
                                    in_code_block = False
                                    
                                output = getattr(code_execution_result, "output", "")
                                if output:
                                    if not in_result_block:
                                        streaming_queue.put(("text", "\n**[沙盒执行结果]**\n```text\n"))
                                        in_result_block = True
                                        
                                    if output.startswith(last_result):
                                        new_output = output[len(last_result):]
                                    else:
                                        new_output = output
                                    last_result = output
                                    if new_output:
                                        full_text += new_output
                                        streaming_queue.put(("text", new_output))
                                        
                            else:
                                # Normal content blocks (close any active code execution markdown fences)
                                if in_code_block:
                                    streaming_queue.put(("text", "\n```\n"))
                                    in_code_block = False
                                if in_result_block:
                                    streaming_queue.put(("text", "\n```\n"))
                                    in_result_block = False
                                    
                                if inline_data and hasattr(inline_data, "data") and inline_data.data:
                                    b64_data = base64.b64encode(inline_data.data).decode("utf-8")
                                    mime_type = getattr(inline_data, "mime_type", "image/png")
                                    img_md = f"\n\n![Generated Image](data:{mime_type};base64,{b64_data})\n\n"
                                    full_text += img_md
                                    streaming_queue.put(("text", img_md))
                                elif is_thought:
                                    if text:
                                        full_reasoning += text
                                        streaming_queue.put(("thinking", text))
                                else:
                                    if text:
                                        # 正文里可能含 <thought>...</thought>(gemini-3.5-flash
                                        # preview 未在协议层分离时),经解析器兜底拆分
                                        text_part, thinking_part = tag_parser.feed(text)
                                        if thinking_part:
                                            full_reasoning += thinking_part
                                            streaming_queue.put(("thinking", thinking_part))
                                        if text_part:
                                            full_text += text_part
                                            streaming_queue.put(("text", text_part))
                                        
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    um = chunk.usage_metadata
                    if hasattr(um, "prompt_token_count") and um.prompt_token_count:
                        input_tokens = um.prompt_token_count
                    if hasattr(um, "candidates_token_count") and um.candidates_token_count:
                        output_tokens = um.candidates_token_count
 
                if chunk.candidates and chunk.candidates[0].grounding_metadata:
                    grounding_meta = chunk.candidates[0].grounding_metadata
            
            # Safely close any un-fenced markdown code/result block at stream termination
            if in_code_block:
                streaming_queue.put(("text", "\n```\n"))
            if in_result_block:
                streaming_queue.put(("text", "\n```\n"))

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

        # 取走思考标签解析器残余缓冲(模型未写闭标签就被 max_tokens 截断)
        text_part, thinking_part = tag_parser.flush()
        if thinking_part:
            full_reasoning += thinking_part
            streaming_queue.put(("thinking", thinking_part))
        if text_part:
            full_text += text_part
            streaming_queue.put(("text", text_part))

        content_blocks = [{"type": "text", "text": full_text}]
        if full_reasoning:
            content_blocks.insert(0, {
                "type": "thinking",
                "thinking": full_reasoning,
                "signature": "omitted_for_display"
            })
            
        streaming_queue.put(("done", {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "content_blocks": content_blocks,
            "thinking": full_reasoning
        }))
        
    except Exception as e:
        logger.exception(f"Gemini streaming error: {e}")
        streaming_queue.put(("error", f"Gemini 错误: {sanitize_error_message(e)}"))
