"""DeepSeek native search via its Anthropic-compatible Messages endpoint."""

import json
from urllib.parse import urlsplit, urlunsplit

import httpx

from .base import build_http_client, sanitize_error_message


def search_endpoint(api_url):
    """Keep credentials on the configured provider origin, including proxy prefixes."""
    url = urlsplit((api_url or "https://api.deepseek.com").strip().rstrip("/"))
    if (
        url.scheme not in {"http", "https"}
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("DeepSeek API 地址必须是有效的 HTTP(S) 基础地址")
    path = url.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses", "/messages"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path.endswith("/v1"):
        path = path[:-3]
    if not path.endswith("/anthropic"):
        path += "/anthropic"
    return urlunsplit((url.scheme, url.netloc, path + "/v1/messages", "", ""))


def parse_search_response(response):
    blocks = response.get("content") or []
    result_blocks = [block for block in blocks if block.get("type") == "web_search_tool_result"]
    if not result_blocks:
        raise RuntimeError("DeepSeek 未返回官方搜索结果，当前端点或模型可能不支持搜索")
    snippets = {}
    for block in blocks:
        if block.get("type") == "text":
            for cite in block.get("citations") or []:
                if cite.get("url") and cite.get("cited_text"):
                    snippets.setdefault(cite["url"], cite["cited_text"])
    results, seen = [], set()
    for block in result_blocks:
        content = block.get("content")
        if isinstance(content, dict):
            raise RuntimeError(f"DeepSeek 官方搜索失败: {content.get('error_code', 'unknown')}")
        for item in content or []:
            if item.get("type") != "web_search_result" or not item.get("url") or item["url"] in seen:
                continue
            seen.add(item["url"])
            results.append(
                {
                    "title": item.get("title") or item["url"],
                    "url": item["url"],
                    "snippet": snippets.get(item["url"], ""),
                    "page_age": item.get("page_age", ""),
                }
            )
    usage = response.get("usage") or {}
    return (
        results[:6],
        "deepseek_native",
        {
            "input_tokens": usage.get("input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
            "web_search_requests": (usage.get("server_tool_use") or {}).get("web_search_requests") or 0,
        },
    )


def search_deepseek(
    query, api_key, api_url, proxy_mode="system", proxy_url="", abort_event=None, on_stream_created=None
):
    def check_abort():
        if abort_event is not None and abort_event.is_set():
            raise InterruptedError("搜索已停止")

    check_abort()
    if not api_key:
        raise ValueError("DeepSeek 官方搜索需要 DeepSeek API Key")
    # Match the official harness's search model and native tool contract.
    body = {
        "model": "deepseek-v4-flash",
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"Perform a web search for the query: {query}"}]}
        ],
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    }
    try:
        with build_http_client(proxy_mode, proxy_url) as client:
            with client.stream(
                "POST",
                search_endpoint(api_url),
                json=body,
                headers={"x-api-key": api_key, "Authorization": f"Bearer {api_key}", "anthropic-version": "2023-06-01"},
                timeout=httpx.Timeout(90.0, connect=10.0),
                follow_redirects=False,
            ) as response:
                if on_stream_created:
                    on_stream_created(response)
                chunks = []
                for chunk in response.iter_bytes():
                    check_abort()
                    chunks.append(chunk)
                check_abort()
                if not response.is_success:
                    raise RuntimeError(f"DeepSeek 官方搜索请求失败 (HTTP {response.status_code})")
                return parse_search_response(json.loads(b"".join(chunks)))
    except Exception as exc:
        check_abort()
        message = sanitize_error_message(str(exc).replace(api_key, "***"))
        raise RuntimeError(message) from None
