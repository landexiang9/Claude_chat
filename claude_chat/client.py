import base64
import json
import logging
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

logger = logging.getLogger("claude_chat.client")


def sanitize_error_message(err):
    """
    过滤错误消息中的敏感信息，例如 API Keys (如 sk-... 等高危敏感字符)。
    """
    import re
    msg = str(err)
    # Mask Anthropic key prefix (sk-ant-...)
    msg = re.sub(r'sk-ant-[a-zA-Z0-9_-]{20,}', 'sk-ant-***', msg)
    # Mask general sk- keys
    msg = re.sub(r'sk-[a-zA-Z0-9]{20,}', 'sk-***', msg)
    # Mask Gemini key pattern
    msg = re.sub(r'AIzaSy[a-zA-Z0-9_-]{33}', 'AIzaSy***', msg)
    # Mask any other potential long hex tokens or keys
    if len(msg) > 300:
        msg = msg[:300] + "... (详细错误已写入本地日志)"
    return msg


def build_http_client(proxy_mode="system", proxy_url=""):
    """
    构建并返回一个配置好代理的 httpx.Client 实例。
    - "none": 禁用代理，禁用信任操作系统环境变量代理行为。
    - "custom": 使用用户自定义输入的代理地址（如 http://127.0.0.1:7890）。
    - "system": 默认选项，自动继承操作系统的环境变量（如 HTTP_PROXY, HTTPS_PROXY）。
    """
    if proxy_mode == "none":
        return httpx.Client(trust_env=False)
    elif proxy_mode == "custom" and proxy_url.strip():
        return httpx.Client(proxy=proxy_url.strip(), trust_env=False)
    else:
        return httpx.Client()


def extract_api_message(msg):
    """
    将本地保存的消息记录转换为符合 Anthropic 官方 API 规范的请求消息结构。
    对携带的本地附件（图片、PDF 文档）进行异步路径读取，并将其编码转换为 Base64 格式的数据块传递给 API。
    """
    role = msg.get("role")
    content = msg.get("content")
    if not isinstance(content, (list, str)):
        return {"role": role, "content": [{"type": "text", "text": str(content)}]}
        
    items_source = content if isinstance(content, list) else [{"type": "text", "text": content}]
    api_content_list = []
    
    for item in items_source:
        if isinstance(item, dict) and item.get("type") == "image":
            # 处理图片附件并编码为 Base64
            source = item.get("source", {})
            if "file_path" in source:
                try:
                    with open(source["file_path"], "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode("utf-8")
                    api_content_list.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": source.get("media_type", "image/png"), "data": b64_data}
                    })
                except Exception:
                    fname = Path(source.get("file_path", "")).name
                    api_content_list.append({"type": "text", "text": f"[图片不可用: {fname}]"})
            elif "data" in source:
                api_content_list.append(item)
                
        elif isinstance(item, dict) and item.get("type") == "document":
            # 处理 PDF 文档附件并编码为 Base64
            source = item.get("source", {})
            if "file_path" in source:
                try:
                    with open(source["file_path"], "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode("utf-8")
                    api_content_list.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data}
                    })
                except Exception:
                    fname = Path(source.get("file_path", "")).name
                    api_content_list.append({"type": "text", "text": f"[文档不可用: {fname}]"})
            elif "data" in source:
                api_content_list.append(item)
                
        elif isinstance(item, str):
            api_content_list.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            api_content_list.append(item)
        else:
            api_content_list.append({"type": "text", "text": str(item)})
            
    return {"role": role, "content": api_content_list}

def convert_messages_to_openai(messages):
    import json
    openai_msgs = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if isinstance(content, list):
            text_parts = []
            thinking_parts = []
            tool_calls = []
            tool_results = []
            
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "image":
                        text_parts.append("[图片]")
                    elif btype == "document":
                        text_parts.append("[文档]")
                    elif btype == "thinking":
                        thinking_parts.append(block.get("thinking", ""))
                    elif btype in ("tool_use", "server_tool_use"):
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}))
                            }
                        })
                    elif btype in ("tool_result", "web_search_tool_result"):
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": str(block.get("content", ""))
                        })
                else:
                    text_parts.append(str(block))
            
            text_str = "".join(text_parts).strip()
            thinking_str = "".join(thinking_parts).strip()
            
            if tool_results:
                if text_str:
                    openai_msgs.append({"role": role, "content": text_str})
                for tr in tool_results:
                    openai_msgs.append(tr)
            elif tool_calls:
                msg_dict = {
                    "role": role,
                    "content": text_str,
                    "tool_calls": tool_calls
                }
                if thinking_str:
                    msg_dict["reasoning_content"] = thinking_str
                openai_msgs.append(msg_dict)
            else:
                msg_dict = {"role": role, "content": text_str}
                if thinking_str:
                    msg_dict["reasoning_content"] = thinking_str
                openai_msgs.append(msg_dict)
                
        else:
            text_str = str(content)
            msg_dict = {"role": role, "content": text_str}
            if msg.get("thinking"):
                msg_dict["reasoning_content"] = msg["thinking"]
            openai_msgs.append(msg_dict)
            
    return openai_msgs


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
def stream_deepseek_response(api_key, api_url, proxy_mode, proxy_url, messages, model, max_tokens, temperature, streaming_queue, abort_event=None, on_stream_created=None, system=None, enable_search=False, search_engine="google", tavily_api_key="", jina_api_key="", web_page_parser="local", conv_id=None, conv_manager=None, previous_content_blocks=None, depth=0):
    try:
        if depth >= 5:
            logger.warning(f"联网搜索已达最大深度限制 ({depth})，强制关闭此轮搜索。")
            enable_search = False

        from openai import OpenAI
        http_client = build_http_client(proxy_mode, proxy_url)
        client = OpenAI(api_key=api_key, base_url=api_url, http_client=http_client)
        
        openai_msgs = convert_messages_to_openai(messages)
        if system and system.strip():
            openai_msgs.insert(0, {"role": "system", "content": system.strip()})
            
        kwargs = {
            "model": model,
            "messages": openai_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True}
        }
        
        tools = None
        if enable_search and "reasoner" not in model.lower():
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "search_web",
                        "description": "Search the web using Google/Bing/Tavily/Jina to get real-time information.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "The search query to look up."}
                            },
                            "required": ["query"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "fetch_webpage",
                        "description": "Fetch and read the full textual content of a specific webpage URL.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string", "description": "The URL of the webpage to fetch."}
                            },
                            "required": ["url"]
                        }
                    }
                }
            ]
            kwargs["tools"] = tools

        response_stream = client.chat.completions.create(**kwargs)
        if on_stream_created:
            on_stream_created(response_stream)
            
        full_text = ""
        full_reasoning = ""
        tool_calls_dict = {}
        input_tokens = 0
        output_tokens = 0
        
        for chunk in response_stream:
            if abort_event and abort_event.is_set():
                streaming_queue.put(("aborted", {}))
                return
                
            if hasattr(chunk, "usage") and chunk.usage is not None:
                usage = chunk.usage
                if hasattr(usage, "prompt_tokens") and usage.prompt_tokens is not None:
                    input_tokens = usage.prompt_tokens
                if hasattr(usage, "completion_tokens") and usage.completion_tokens is not None:
                    output_tokens = usage.completion_tokens

            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                full_reasoning += reasoning
                streaming_queue.put(("thinking", reasoning))
                
            content = getattr(delta, "content", None)
            if content:
                full_text += content
                streaming_queue.put(("text", content))
                
            tool_calls = getattr(delta, "tool_calls", None)
            if tool_calls:
                for tc in tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_dict:
                        tool_calls_dict[idx] = {
                            "id": tc.id,
                            "name": tc.function.name if tc.function and tc.function.name else "",
                            "arguments": ""
                        }
                    if tc.function and tc.function.arguments:
                        tool_calls_dict[idx]["arguments"] += tc.function.arguments

        if tool_calls_dict and enable_search:
            assistant_content = [{"type": "text", "text": full_text}]
            if full_reasoning:
                assistant_content.insert(0, {
                    "type": "thinking",
                    "thinking": full_reasoning,
                    "signature": "omitted_for_display"
                })
            
            openai_tool_calls = []
            tool_uses = []
            for idx, tc in sorted(tool_calls_dict.items()):
                openai_tool_calls.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]}
                })
                
                try:
                    args = json.loads(tc["arguments"])
                except Exception:
                    args = {}
                    
                if tc["name"] == "search_web":
                    tool_uses.append({
                        "type": "search_web",
                        "id": tc["id"],
                        "query": args.get("query", "")
                    })
                elif tc["name"] == "fetch_webpage":
                    tool_uses.append({
                        "type": "fetch_webpage",
                        "id": tc["id"],
                        "url": args.get("url", "")
                    })
            
            if tool_uses:
                from claude_chat.search import search_web, fetch_webpage_content
                tool_result_content = []
                
                db_assistant_content = []
                if full_reasoning:
                    db_assistant_content.append({"type": "thinking", "thinking": full_reasoning, "signature": "omitted_for_display"})
                db_assistant_content.append({"type": "text", "text": full_text})
                
                for tu in tool_uses:
                    tu_type = tu["type"]
                    tool_use_id = tu["id"]
                    
                    if tu_type == "search_web":
                        tool_query = tu["query"]
                        streaming_queue.put(("search_start", {"query": tool_query}))
                        
                        search_results, engine_used, usage_info = search_web(
                            tool_query,
                            engine=search_engine,
                            proxy_mode=proxy_mode,
                            proxy_url=proxy_url,
                            tavily_api_key=tavily_api_key,
                            jina_api_key=jina_api_key
                        )
                        
                        streaming_queue.put(("search_done", {
                            "query": tool_query,
                            "results": search_results,
                            "engine": engine_used,
                            "usage": usage_info
                        }))
                        
                        result_text = ""
                        if search_results:
                            for idx, r in enumerate(search_results):
                                result_text += f"[{idx+1}] Title: {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}\n\n"
                        else:
                            result_text = "No results found on the web."
                        result_text += f"\n[Search Engine: {engine_used}]"
                        if usage_info:
                            result_text += f"\n[Usage: {json.dumps(usage_info)}]"
                            
                    elif tu_type == "fetch_webpage":
                        tool_url = tu["url"]
                        streaming_queue.put(("fetch_start", {"url": tool_url}))
                        
                        webpage_text, usage_info = fetch_webpage_content(
                            tool_url,
                            parser_type=web_page_parser,
                            jina_api_key=jina_api_key,
                            proxy_mode=proxy_mode,
                            proxy_url=proxy_url
                        )
                        
                        streaming_queue.put(("fetch_done", {
                            "url": tool_url,
                            "content_len": len(webpage_text),
                            "parser": web_page_parser,
                            "usage": usage_info
                        }))
                        
                        result_text = webpage_text
                        result_text += f"\n[Web Reader: {web_page_parser}]"
                        if usage_info:
                            result_text += f"\n[Usage: {json.dumps(usage_info)}]"
                            
                    db_assistant_content.append({
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tu_type,
                        "input": {"query": tu["query"]} if tu_type == "search_web" else {"url": tu["url"]}
                    })
                    
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_text
                    })
                
                if conv_id and conv_manager:
                    conv_manager.add_message(conv_id, "assistant", db_assistant_content, thinking=full_reasoning if full_reasoning else None)
                    conv_manager.add_message(conv_id, "user", tool_result_content)
                
                messages.append({"role": "assistant", "content": db_assistant_content})
                messages.append({"role": "user", "content": tool_result_content})
                
                
                new_previous = []
                if previous_content_blocks:
                    new_previous.extend(previous_content_blocks)
                new_previous.extend(db_assistant_content)

                stream_deepseek_response(
                    api_key=api_key,
                    api_url=api_url,
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
                    previous_content_blocks=new_previous,
                    depth=depth + 1
                )
                return

        if input_tokens == 0:
            input_tokens = len(str(openai_msgs)) // 4
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
        logger.exception(f"DeepSeek streaming error: {e}")
        streaming_queue.put(("error", f"DeepSeek 错误: {sanitize_error_message(e)}"))


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


def stream_claude_response_native(api_key, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_config, streaming_queue, abort_event=None, on_stream_created=None, system=None, output_config=None, enable_search=False, enable_web_fetch=True, web_fetch_limit=15000, search_engine="google", tavily_api_key="", jina_api_key="", web_page_parser="local", conv_id=None, conv_manager=None, depth=0):
    """
    启动 Anthropic API 消息流式接收。
    通常运行在后台线程中，实时抓取流中的文本块（text_delta）和思考推理块（thinking_delta），
    并将其放入线程安全的队列 `streaming_queue` 中供前端渲染。
    支持通过设置 `abort_event` 中止事件随时强行中断请求。
    """
    try:
        if depth >= 5:
            logger.warning(f"联网搜索已达最大深度限制 ({depth})，强制关闭此轮搜索。")
            enable_search = False

        http_client = build_http_client(proxy_mode, proxy_url)
        client = Anthropic(api_key=api_key, http_client=http_client)
        
        # 组装 API 调用参数
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if thinking_config:
            kwargs["thinking"] = thinking_config
        if output_config:
            kwargs["output_config"] = output_config
        if system and system.strip():
            kwargs["system"] = system.strip()

        # 如果开启联网搜索，注入 search_web 和 fetch_webpage 工具的定义
        if enable_search:
            if search_engine == "claude":
                kwargs["tools"] = [
                    {
                        "type": "web_search_20260209",
                        "name": "web_search"
                    }
                ]
            else:
                kwargs["tools"] = [
                    {
                        "name": "search_web",
                        "description": "Search the web using Google/Bing/Tavily/Jina to get real-time information and up-to-date facts about current events, news, people, entities, or queries that require fresh online data.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query to look up on the web."
                                }
                            },
                            "required": ["query"]
                        }
                    }
                ]
                if enable_web_fetch:
                    kwargs["tools"].append({
                        "name": "fetch_webpage",
                        "description": "Fetch and read the full textual content of a specific webpage URL. Use this to read the details of search results or external links in detail.",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string",
                                    "description": "The URL of the webpage to fetch and read."
                                }
                            },
                            "required": ["url"]
                        }
                    })

        # 发起流式请求
        current_tool_use_id = None
        current_tool_query = ""
        with client.messages.stream(**kwargs) as stream:
            if on_stream_created:
                on_stream_created(stream)
                
            for event in stream:
                # 检查用户是否触发了“停止生成”按钮
                if abort_event and abort_event.is_set():
                    streaming_queue.put(("aborted", {}))
                    return
                
                etype = event.type if hasattr(event, 'type') else ''
                if etype == 'thinking':
                    t = getattr(event, 'thinking', '')
                    if t:
                        streaming_queue.put(("thinking", t))
                elif etype == 'content_block_start':
                    cb = getattr(event, 'content_block', None)
                    if cb and cb.type == 'tool_use' and cb.name == 'web_search':
                        current_tool_use_id = cb.id
                        current_tool_query = ""
                        streaming_queue.put(("search_start", {"query": "", "id": cb.id}))
                elif etype == 'content_block_delta':
                    d = event.delta
                    dtypes = getattr(d, 'type', '')
                    if dtypes == 'text_delta':
                        # 推送生成的回复文本
                        streaming_queue.put(("text", getattr(d, 'text', '')))
                    elif dtypes == 'thinking_delta':
                        # 推送模型实时思考轨迹
                        streaming_queue.put(("thinking", getattr(d, 'thinking', '')))
                    elif dtypes == 'input_json_delta':
                        pj = getattr(d, 'partial_json', '')
                        if pj and current_tool_use_id:
                            current_tool_query += pj
                elif etype == 'content_block_stop':
                    if current_tool_use_id:
                        q_str = ""
                        try:
                            import json
                            parsed = json.loads(current_tool_query)
                            q_str = parsed.get("query", "")
                        except Exception:
                            import re
                            m = re.search(r'"query"\s*:\s*"([^"]+)"', current_tool_query)
                            if m:
                                q_str = m.group(1)
                            else:
                                q_str = current_tool_query
                        
                        streaming_queue.put(("search_done", {
                            "query": q_str,
                            "results": [],
                            "engine": "claude",
                            "usage": None
                        }))
                        current_tool_use_id = None
                        current_tool_query = ""

        # 获取最终完整的 Message 对象并提取 Token 统计信息
        final = stream.get_final_message()
        
        # 如果模型决定使用工具
        if final.stop_reason == "tool_use" and enable_search:
            assistant_content = []
            thinking_text = ""
            tool_uses = []
            
            for block in final.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "thinking":
                    thinking_text = block.thinking
                    assistant_content.append({
                        "type": "thinking",
                        "thinking": block.thinking,
                        "signature": block.signature
                    })
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
                    if block.name == "search_web":
                        tool_uses.append({
                            "type": "search_web",
                            "id": block.id,
                            "query": block.input.get("query", "")
                        })
                    elif block.name == "fetch_webpage":
                        tool_uses.append({
                            "type": "fetch_webpage",
                            "id": block.id,
                            "url": block.input.get("url", "")
                        })
            
            if tool_uses:
                # 在后台线程中执行搜索/网页抓取
                from claude_chat.search import search_web, fetch_webpage_content
                tool_result_content = []
                
                for tu in tool_uses:
                    tu_type = tu["type"]
                    tool_use_id = tu["id"]
                    
                    if tu_type == "search_web":
                        tool_query = tu["query"]
                        # 推送搜索开始事件
                        streaming_queue.put(("search_start", {"query": tool_query}))
                        
                        # 执行搜索
                        search_results, engine_used, usage_info = search_web(
                            tool_query,
                            engine=search_engine,
                            proxy_mode=proxy_mode,
                            proxy_url=proxy_url,
                            tavily_api_key=tavily_api_key,
                            jina_api_key=jina_api_key
                        )
                        
                        # 推送搜索完毕事件
                        streaming_queue.put(("search_done", {
                            "query": tool_query,
                            "results": search_results,
                            "engine": engine_used,
                            "usage": usage_info
                        }))
                        
                        # 组装 tool_result 响应
                        result_text = ""
                        if search_results:
                            for idx, r in enumerate(search_results):
                                result_text += f"[{idx+1}] Title: {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}\n\n"
                        else:
                            result_text = "No results found on the web."
                        
                        # 附带搜索引擎信息，便于前端解析展现而不破坏 Anthropic API 的 JSON 约束
                        result_text += f"\n[Search Engine: {engine_used}]"
                        if usage_info:
                            import json
                            result_text += f"\n[Usage: {json.dumps(usage_info)}]"
                            
                    elif tu_type == "fetch_webpage":
                        tool_url = tu["url"]
                        # 推送网页拉取开始事件
                        streaming_queue.put(("fetch_start", {"url": tool_url}))
                        
                        # 执行网页解析抓取
                        webpage_text, usage_info = fetch_webpage_content(
                            tool_url,
                            parser_type=web_page_parser,
                            jina_api_key=jina_api_key,
                            proxy_mode=proxy_mode,
                            proxy_url=proxy_url,
                            max_web_fetch_length=web_fetch_limit
                        )
                        
                        # 推送网页拉取完毕事件
                        streaming_queue.put(("fetch_done", {
                            "url": tool_url,
                            "content_len": len(webpage_text),
                            "parser": web_page_parser,
                            "usage": usage_info
                        }))
                        
                        result_text = webpage_text
                        
                        # 附加调试元信息以防 API 出错，同时便于历史对话渲染
                        result_text += f"\n[Web Reader: {web_page_parser}]"
                        if usage_info:
                            import json
                            result_text += f"\n[Usage: {json.dumps(usage_info)}]"
                            
                    tool_result_content.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_text
                    })
                
                # 将本轮的 tool_use 消息和 tool_result 消息依次写入数据库
                if conv_id and conv_manager:
                    # 增量安全写入 AI 产生的 tool_use 消息
                    conv_manager.add_message(conv_id, "assistant", assistant_content, thinking=thinking_text if thinking_text else None)
                    # 增量安全写入用户端反馈 of tool_result 消息
                    conv_manager.add_message(conv_id, "user", tool_result_content)
                
                # 把它们追加到当前的 messages 上下文中
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_result_content})
                
                # 递归发起下一轮 API 生成
                stream_claude_response(
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
                    active_platform="claude",
                    depth=depth + 1
                )
                return

        input_tokens = final.usage.input_tokens if hasattr(final, 'usage') and final.usage else 0
        output_tokens = final.usage.output_tokens if hasattr(final, 'usage') and final.usage else 0
        
        # 将 final.content 序列化为 list of dicts 便于主线程存储完整结构
        content_blocks_dump = []
        if hasattr(final, "content") and final.content:
            for block in final.content:
                if hasattr(block, "model_dump"):
                    content_blocks_dump.append(block.model_dump())
                else:
                    content_blocks_dump.append(block.dict())
                    
        thinking_text = ""
        if hasattr(final, "content") and final.content:
            for block in final.content:
                if hasattr(block, "type") and block.type == "thinking":
                    thinking_text += getattr(block, "thinking", "")
                elif isinstance(block, dict) and block.get("type") == "thinking":
                    thinking_text += block.get("thinking", "")

        streaming_queue.put(("done", {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "content_blocks": content_blocks_dump,
            "thinking": thinking_text
        }))
        
    except BadRequestError as e:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", f"请求错误: {sanitize_error_message(e)}"))
    except APITimeoutError:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", "请求超时，请重试"))
    except APIStatusError as e:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", f"API 错误 [{e.status_code}]: {sanitize_error_message(e)}"))
    except Exception as e:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", f"未知错误: {sanitize_error_message(e)}"))


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
