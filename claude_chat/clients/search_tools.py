"""Shared client-side search tools for Chat Completions and Responses."""

import json


def search_tools(protocol="deepseek", enable_web_fetch=True):
    definitions = [
        {
            "name": "search_web",
            "description": "Search the web for current information and sources.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    ]
    if enable_web_fetch:
        definitions.append(
            {
                "name": "fetch_webpage",
                "description": "Read the text of a webpage URL.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            }
        )
    if protocol == "responses":
        return [{"type": "function", **definition} for definition in definitions]
    return [{"type": "function", "function": definition} for definition in definitions]


def execute_search_tool(
    name,
    arguments,
    call_id,
    streaming_queue,
    *,
    api_key,
    api_url,
    proxy_mode,
    proxy_url,
    search_engine="google",
    tavily_api_key="",
    jina_api_key="",
    web_page_parser="local",
    web_fetch_limit=15000,
    abort_event=None,
    on_stream_created=None,
):
    from claude_chat.search import fetch_webpage_content, search_web

    from .deepseek_search import search_deepseek

    if abort_event is not None and abort_event.is_set():
        raise InterruptedError("搜索已停止")
    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    if not isinstance(arguments, dict):
        raise ValueError("搜索工具参数必须是 JSON 对象")
    if name == "search_web":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("搜索查询不能为空")
        streaming_queue.put(("search_start", {"query": query, "id": call_id}))
        if search_engine == "deepseek_native":
            results, engine, usage = search_deepseek(
                query,
                api_key,
                api_url,
                proxy_mode,
                proxy_url,
                abort_event,
                on_stream_created,
            )
        else:
            results, engine, usage = search_web(
                query,
                engine=search_engine,
                proxy_mode=proxy_mode,
                proxy_url=proxy_url,
                tavily_api_key=tavily_api_key,
                jina_api_key=jina_api_key,
            )
        if abort_event is not None and abort_event.is_set():
            raise InterruptedError("搜索已停止")
        record = {"id": call_id, "query": query, "results": results, "engine": engine, "usage": usage}
        streaming_queue.put(("search_done", record))
        text = "".join(
            f"[{index + 1}] Title: {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}\n\n"
            for index, r in enumerate(results)
        )
        text = (text or "No results found on the web.") + f"\n[Search Engine: {engine}]"
    elif name == "fetch_webpage":
        url = arguments.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("网页地址不能为空")
        streaming_queue.put(("fetch_start", {"url": url}))
        text, usage = fetch_webpage_content(
            url,
            parser_type=web_page_parser,
            jina_api_key=jina_api_key,
            proxy_mode=proxy_mode,
            proxy_url=proxy_url,
            max_web_fetch_length=web_fetch_limit,
        )
        if abort_event is not None and abort_event.is_set():
            raise InterruptedError("网页读取已停止")
        record = {"url": url, "content_len": len(text), "parser": web_page_parser, "usage": usage}
        streaming_queue.put(("fetch_done", record))
        text += f"\n[Web Reader: {web_page_parser}]"
    else:
        raise ValueError(f"不支持的搜索工具: {name}")
    if usage:
        text += f"\n[Usage: {json.dumps(usage)}]"
    return text, record
