import json
import logging
from claude_chat.request_params import generation_params
from .search_tools import execute_search_tool, search_tools
from claude_chat.clients.file_uploads import (
    prepare_custom_provider_files,
    prepare_openai_compatible_files,
    upload_cache_namespace,
)
from .base import build_http_client, record_stream_truncation, record_stream_usage, sanitize_error_message

logger = logging.getLogger("claude_chat.clients")


def convert_messages_to_openai(messages):
    import json
    openai_msgs = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        
        if isinstance(content, list):
            text_parts = []
            thinking_parts = []
            file_parts = []
            tool_calls = []
            tool_results = []
            
            for block in content:
                if isinstance(block, dict):
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "image":
                        source = block.get("source", {})
                        if source.get("file_id"):
                            file_parts.append({"type": "file", "file_id": source["file_id"]})
                        elif source.get("type") == "base64" and source.get("data"):
                            media_type = source.get("media_type", "image/png")
                            file_parts.append({
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{source['data']}"},
                            })
                        else:
                            text_parts.append("[图片不可用]")
                    elif btype in ("document", "file"):
                        source = block.get("source", {})
                        if source.get("file_id"):
                            file_parts.append({"type": "file", "file_id": source["file_id"]})
                        elif source.get("type") == "openrouter_file" and source.get("file_data"):
                            file_parts.append({
                                "type": "file",
                                "file": {
                                    "filename": source.get("filename", "document.pdf"),
                                    "file_data": source["file_data"],
                                },
                            })
                        else:
                            text_parts.append("[文档不可用]")
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
                if file_parts:
                    content_parts = []
                    if text_str:
                        content_parts.append({"type": "text", "text": text_str})
                    content_parts.extend(file_parts)
                    msg_dict = {"role": role, "content": content_parts}
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

def stream_deepseek_response(api_key, api_url, proxy_mode, proxy_url, messages, model, max_tokens, temperature, streaming_queue, abort_event=None, on_stream_created=None, system=None, enable_search=False, search_engine="google", tavily_api_key="", jina_api_key="", web_page_parser="local", web_fetch_limit=15000, conv_id=None, conv_manager=None, previous_content_blocks=None, depth=0, thinking_config=None, accumulated_input_tokens=0, accumulated_output_tokens=0, custom_params=None, request_params=None, file_upload_enabled=True, file_upload_purpose="user_data", file_upload_image_only=True, file_upload_expires_in_seconds=172800, file_upload_adapter=None, enable_web_fetch=True, memory_session=None):
    client = http_client = None
    response_stream = None  # M-fix#10: 保证 finally 中一定可关闭,避免 abort/异常路径泄漏 HTTP 连接
    try:
        if depth >= 5:
            logger.warning(f"联网搜索已达最大深度限制 ({depth})，强制关闭此轮搜索。")
            enable_search = False

        from openai import OpenAI
        http_client = build_http_client(proxy_mode, proxy_url)
        client = OpenAI(api_key=api_key, base_url=api_url, http_client=http_client)
        if file_upload_adapter:
            messages = prepare_custom_provider_files(
                client,
                messages,
                upload_cache_namespace("custom", api_key, api_url),
                file_upload_adapter,
                purpose=file_upload_purpose,
                expires_after_seconds=file_upload_expires_in_seconds,
            )
        elif file_upload_enabled:
            messages = prepare_openai_compatible_files(
                client,
                messages,
                upload_cache_namespace("openai-compatible", api_key, api_url),
                purpose=file_upload_purpose,
                image_only=file_upload_image_only,
                expires_after_seconds=file_upload_expires_in_seconds,
            )
        
        openai_msgs = convert_messages_to_openai(messages)
        if system and system.strip():
            openai_msgs.insert(0, {"role": "system", "content": system.strip()})
            
        effective = generation_params("deepseek", model, max_tokens, temperature, thinking_config,
                                      custom=custom_params, override=request_params)
        kwargs = {"model": model, "messages": openai_msgs, "stream": True,
                  "stream_options": {"include_usage": True}}
        if effective:
            kwargs["extra_body"] = effective

        if enable_search:
            kwargs["tools"] = search_tools(enable_web_fetch=enable_web_fetch)

        from claude_chat.memory_tools import TOOL_NAMES
        if memory_session and depth < 5:
            memory_definitions = memory_session.tools("openai")
            if memory_definitions:
                kwargs["tools"] = [*kwargs.get("tools", []), *memory_definitions]

        response_stream = client.chat.completions.create(**kwargs)
        if on_stream_created:
            on_stream_created(response_stream)

        full_text = ""
        full_reasoning = ""
        tool_calls_dict = {}
        input_tokens = 0
        output_tokens = 0
        reported_usage = {}

        # 流式思考标签解析器:部分 OpenAI 兼容聚合服务(如 opencode go)不在协议层分离思考,
        # 而是把 <thought>...</thought> 写在正文 content 里。这里在 content 通道上兜底解析,
        # 标签内文本路由到思考通道,标签外文本路由到正文通道。若模型已用 reasoning_content
        # 原生分离,正文不含该标签,解析器直通放行,不会影响既有逻辑(见 thinking_tag_parser)。
        from .thinking_tag_parser import ThinkingTagStreamParser
        tag_parser = ThinkingTagStreamParser()

        for chunk in response_stream:
            if hasattr(chunk, "usage") and chunk.usage is not None:
                usage = chunk.usage
                if hasattr(usage, "prompt_tokens") and usage.prompt_tokens is not None:
                    input_tokens = usage.prompt_tokens
                if hasattr(usage, "completion_tokens") and usage.completion_tokens is not None:
                    output_tokens = usage.completion_tokens
                reported_usage.update({
                    key: value for key, value in (
                        ("input_tokens", getattr(usage, "prompt_tokens", None)),
                        ("output_tokens", getattr(usage, "completion_tokens", None)),
                    ) if value is not None
                })
                record_stream_usage(streaming_queue, reported_usage)

            if abort_event and abort_event.is_set():
                streaming_queue.put(("aborted", {}))
                return

            if not chunk.choices:
                continue
            record_stream_truncation(streaming_queue, getattr(chunk.choices[0], "finish_reason", None))
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                full_reasoning += reasoning
                streaming_queue.put(("thinking", reasoning))

            content = getattr(delta, "content", None)
            if content:
                # 兜底解析正文里的 <thought>...</thought> 思考标签
                text_part, thinking_part = tag_parser.feed(content)
                if thinking_part:
                    full_reasoning += thinking_part
                    streaming_queue.put(("thinking", thinking_part))
                if text_part:
                    full_text += text_part
                    streaming_queue.put(("text", text_part))
                
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

        # Flush a possible partial tag before deciding whether this is a tool round.
        # Previously this happened only on the terminal round, dropping buffered text
        # whenever a tool call was emitted in the same response.
        text_part, thinking_part = tag_parser.flush()
        if thinking_part:
            full_reasoning += thinking_part
            streaming_queue.put(("thinking", thinking_part))
        if text_part:
            full_text += text_part
            streaming_queue.put(("text", text_part))

        if input_tokens == 0:
            input_tokens = len(str(openai_msgs)) // 4
        if output_tokens == 0:
            output_tokens = len(full_text) // 4

        if tool_calls_dict and (enable_search or memory_session):
            if memory_session and any(tc["name"] in TOOL_NAMES for tc in tool_calls_dict.values()):
                memory_session.begin_round()
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
                    
                if memory_session and tc["name"] in TOOL_NAMES:
                    tool_uses.append({"type": tc["name"], "id": tc["id"], "arguments": tc["arguments"]})
                elif tc["name"] == "search_web" and enable_search:
                    tool_uses.append({
                        "type": "search_web",
                        "id": tc["id"],
                        "query": args.get("query", "")
                    })
                elif tc["name"] == "fetch_webpage" and enable_search:
                    tool_uses.append({
                        "type": "fetch_webpage",
                        "id": tc["id"],
                        "url": args.get("url", "")
                    })
            
            if tool_uses:
                tool_result_content = []
                
                db_assistant_content = []
                if full_reasoning:
                    db_assistant_content.append({"type": "thinking", "thinking": full_reasoning, "signature": "omitted_for_display"})
                db_assistant_content.append({"type": "text", "text": full_text})
                
                for tu in tool_uses:
                    tu_type = tu["type"]
                    tool_use_id = tu["id"]
                    
                    if tu_type == "fetch_webpage" and not enable_web_fetch:
                        raise RuntimeError("网页读取已关闭")
                    if tu_type in TOOL_NAMES:
                        try:
                            arguments = json.loads(tu["arguments"])
                        except ValueError:
                            arguments = {}
                        result_text = memory_session.execute(tu_type, tu["arguments"], tool_use_id)
                        record = {}
                    else:
                        arguments = {"query": tu["query"]} if tu_type == "search_web" else {"url": tu["url"]}
                        result_text, record = execute_search_tool(
                            tu_type, arguments, tool_use_id, streaming_queue,
                            api_key=api_key, api_url=api_url, proxy_mode=proxy_mode, proxy_url=proxy_url,
                            search_engine=search_engine, tavily_api_key=tavily_api_key, jina_api_key=jina_api_key,
                            web_page_parser=web_page_parser, web_fetch_limit=web_fetch_limit,
                            abort_event=abort_event, on_stream_created=on_stream_created,
                        )
                    if record.get("engine") == "deepseek_native":
                        search_usage = record.get("usage") or {}
                        input_tokens += search_usage.get("input_tokens") or 0
                        output_tokens += search_usage.get("output_tokens") or 0

                    db_assistant_content.append({
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": tu_type,
                        "input": arguments
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
                    request_params=request_params,
                    custom_params=custom_params,
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
                    enable_web_fetch=enable_web_fetch,
                    search_engine=search_engine,
                    tavily_api_key=tavily_api_key,
                    jina_api_key=jina_api_key,
                    web_page_parser=web_page_parser,
                    web_fetch_limit=web_fetch_limit,
                    conv_id=conv_id,
                    conv_manager=conv_manager,
                    previous_content_blocks=new_previous,
                    depth=depth + 1,
                    thinking_config=thinking_config,
                    accumulated_input_tokens=accumulated_input_tokens + input_tokens,
                    accumulated_output_tokens=accumulated_output_tokens + output_tokens,
                    file_upload_enabled=file_upload_enabled,
                    file_upload_purpose=file_upload_purpose,
                    file_upload_image_only=file_upload_image_only,
                    file_upload_expires_in_seconds=file_upload_expires_in_seconds,
                    file_upload_adapter=file_upload_adapter,
                    memory_session=memory_session,
                )
                return

        content_blocks = [{"type": "text", "text": full_text}]
        if full_reasoning:
            content_blocks.insert(0, {
                "type": "thinking",
                "thinking": full_reasoning,
                "signature": "omitted_for_display"
            })
            
        streaming_queue.put(("done", {
            "usage_source": "reported" if len(reported_usage) == 2 else "estimated",
            "input_tokens": accumulated_input_tokens + input_tokens,
            "output_tokens": accumulated_output_tokens + output_tokens,
            "content_blocks": content_blocks,
            "thinking": full_reasoning
        }))
        
    except Exception as e:
        if abort_event is not None and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            message = sanitize_error_message(str(e).replace(api_key, "***") if api_key else str(e))
            logger.error("DeepSeek streaming error: %s", message)
            streaming_queue.put(("error", f"DeepSeek 错误: {message}"))
    finally:
        for resource in (response_stream, client, http_client):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
