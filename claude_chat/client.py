import base64
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError


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


def stream_claude_response(api_key, proxy_mode, proxy_url, messages, model, max_tokens, temperature, thinking_config, streaming_queue, abort_event=None, on_stream_created=None, system=None, output_config=None):
    """
    启动 Anthropic API 消息流式接收。
    通常运行在后台线程中，实时抓取流中的文本块（text_delta）和思考推理块（thinking_delta），
    并将其放入线程安全的队列 `streaming_queue` 中供前端渲染。
    支持通过设置 `abort_event` 中止事件随时强行中断请求。
    """
    try:
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

        # 发起流式请求
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
                elif etype == 'content_block_delta':
                    d = event.delta
                    dtypes = getattr(d, 'type', '')
                    if dtypes == 'text_delta':
                        # 推送生成的回复文本
                        streaming_queue.put(("text", getattr(d, 'text', '')))
                    elif dtypes == 'thinking_delta':
                        # 推送模型实时思考轨迹
                        streaming_queue.put(("thinking", getattr(d, 'thinking', '')))

        # 获取最终完整的 Message 对象并提取 Token 统计信息
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
            
    # API 如果没有明确返回模型能力，则针对已知支持推理的大模型进行硬编码兼容
    if not res["thinking_supported"]:
        mid_lower = mid.lower()
        if "opus-4-7" in mid_lower or "sonnet-4-6" in mid_lower or "opus-4-6" in mid_lower or "3-7-sonnet" in mid_lower or "claude-3-7" in mid_lower:
            res["thinking_supported"] = True
            res["adaptive_supported"] = True
            res["enabled_supported"] = True
            res["effort_levels"] = ["low", "medium", "high", "max"]
        elif "opus-4-5" in mid_lower:
            res["thinking_supported"] = True
            res["adaptive_supported"] = False
            res["enabled_supported"] = True
            res["effort_levels"] = []
            
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
    
    if "opus-4-7" in mid or "sonnet-4-6" in mid or "opus-4-6" in mid or "3-7-sonnet" in mid or "claude-3-7" in mid:
        res["thinking_supported"] = True
        res["adaptive_supported"] = True
        res["enabled_supported"] = True
        res["effort_levels"] = ["low", "medium", "high", "max"]
        if "opus-4-7" in mid:
            res["display_name"] = "Claude Opus 4.7"
        elif "opus-4-6" in mid:
            res["display_name"] = "Claude Opus 4.6"
        elif "sonnet-4-6" in mid:
            res["display_name"] = "Claude Sonnet 4.6"
        elif "3-7-sonnet" in mid or "claude-3-7" in mid:
            res["display_name"] = "Claude 3.7 Sonnet"
    elif "opus-4-5" in mid:
        res["thinking_supported"] = True
        res["adaptive_supported"] = False
        res["enabled_supported"] = True
        res["effort_levels"] = []
        res["display_name"] = "Claude Opus 4.5"
    elif "3-5-sonnet" in mid:
        res["display_name"] = "Claude 3.5 Sonnet"
    elif "3-5-haiku" in mid:
        res["display_name"] = "Claude 3.5 Haiku"
    elif "3-opus" in mid:
        res["display_name"] = "Claude 3 Opus"
        
    return res


def fetch_available_models(api_key, proxy_mode, proxy_url):
    """
    从 Anthropic 官方 API 拉取当前账户所有活跃模型的列表及其详细能力结构。
    如果获取失败，则返回空列表（前端会自动退化使用默认的本地备用列表）。
    """
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
            # 过滤掉已经失效或标明即将弃用的模型
            if hasattr(m, 'deprecation_date') and m.deprecation_date:
                continue
            
            m_cap = get_model_capabilities(m)
            models_data.append(m_cap)
            
            if "opus-4-7" in mid.lower():
                has_opus_47 = True
                
        # 强制将预置模型塞入列表首位以提升体验
        if not has_opus_47:
            models_data.insert(0, get_default_capabilities("claude-opus-4-7"))
            
        return models_data
    except Exception:
        return []
