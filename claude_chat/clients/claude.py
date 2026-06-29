import base64
import json
import logging
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

logger = logging.getLogger("claude_chat.clients")

from .base import sanitize_error_message, build_http_client, extract_api_message

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
                stream_claude_response_native(
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
                    # M-fix#3: 签名无 active_platform 形参,此 kwarg 会抛 TypeError 被外层吞掉,
                    # 导致每次工具回合都失败、tool_use/tool_result 配对孤立。移除之。
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

