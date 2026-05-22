import base64
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError


def build_http_client(proxy_mode="system", proxy_url=""):
    """
    Builds httpx.Client configuring the appropriate proxy mode.
    """
    if proxy_mode == "none":
        return httpx.Client(trust_env=False)
    elif proxy_mode == "custom" and proxy_url.strip():
        return httpx.Client(proxy=proxy_url.strip(), trust_env=False)
    else:
        return httpx.Client()


def extract_api_message(msg):
    """
    Converts conversation history message into Anthropic API compliant message,
    resolving and reading attached files to Base64 data blocks.
    """
    role = msg.get("role")
    content = msg.get("content")
    if not isinstance(content, (list, str)):
        return {"role": role, "content": [{"type": "text", "text": str(content)}]}
        
    items_source = content if isinstance(content, list) else [{"type": "text", "text": content}]
    api_content_list = []
    
    for item in items_source:
        if isinstance(item, dict) and item.get("type") == "image":
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


def stream_claude_response(api_key, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_config, streaming_queue, abort_event=None, on_stream_created=None, system=None):
    """
    Initiates Anthropic stream in background thread and pushes events into streaming_queue.
    """
    try:
        http_client = build_http_client(proxy_mode, proxy_url)
        client = Anthropic(api_key=api_key, http_client=http_client)
        
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if thinking_config:
            kwargs["thinking"] = thinking_config
        if system and system.strip():
            kwargs["system"] = system.strip()

        with client.messages.stream(**kwargs) as stream:
            if on_stream_created:
                on_stream_created(stream)
                
            for event in stream:
                if abort_event and abort_event.is_set():
                    streaming_queue.put(("aborted", {}))
                    return
                etype = event.type if hasattr(event, 'type') else ''
                if etype == 'thinking':
                    t = getattr(event, 'thinking', '')
                    if t:
                        streaming_queue.put(("thinking", t))
                elif etype == 'content_block_delta':
                    d = event.delta
                    dtypes = getattr(d, 'type', '')
                    if dtypes == 'text_delta':
                        streaming_queue.put(("text", getattr(d, 'text', '')))
                    elif dtypes == 'thinking_delta':
                        streaming_queue.put(("thinking", getattr(d, 'thinking', '')))

        final = stream.get_final_message()
        input_tokens = final.usage.input_tokens if hasattr(final, 'usage') and final.usage else 0
        output_tokens = final.usage.output_tokens if hasattr(final, 'usage') and final.usage else 0
        streaming_queue.put(("done", {"input_tokens": input_tokens, "output_tokens": output_tokens}))
        
    except BadRequestError as e:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", f"请求错误: {e}"))
    except APITimeoutError:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", "请求超时，请重试"))
    except APIStatusError as e:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", f"API 错误 [{e.status_code}]: {e}"))
    except Exception as e:
        if abort_event and abort_event.is_set():
            streaming_queue.put(("aborted", {}))
        else:
            streaming_queue.put(("error", f"未知错误: {e}"))


def fetch_available_models(api_key, proxy_mode, proxy_url):
    """
    Fetches the list of active models from Anthropic API.
    """
    if not api_key:
        return []
    try:
        http_client = build_http_client(proxy_mode, proxy_url)
        client = Anthropic(api_key=api_key, http_client=http_client)
        models = client.models.list()
        
        model_ids = []
        has_opus_47 = False
        
        for m in models.data:
            mid = m.id
            if hasattr(m, 'deprecation_date') and m.deprecation_date:
                continue
            model_ids.append(mid)
            if "opus-4-7" in mid.lower():
                has_opus_47 = True
                
        if not has_opus_47:
            model_ids.insert(0, "claude-opus-4-7")
            
        return model_ids
    except Exception:
        return []
