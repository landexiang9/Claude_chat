"""Shared Google Gen AI HTTP options for discovery, generation and OCR."""


def build_gemini_http_options(proxy_mode="system", proxy_url="", base_url="", timeout_ms=120000):
    from google.genai import types

    args = {}
    if proxy_mode == "none":
        args["trust_env"] = False
    elif proxy_mode == "custom":
        if not isinstance(proxy_url, str) or not proxy_url.strip():
            raise ValueError("自定义代理地址不能为空")
        args.update(proxy=proxy_url.strip(), trust_env=False)
    options = {"timeout": timeout_ms, "client_args": dict(args), "async_client_args": dict(args)}
    if isinstance(base_url, str) and base_url.strip():
        options["base_url"] = base_url.strip().rstrip("/")
    return types.HttpOptions(**options)
