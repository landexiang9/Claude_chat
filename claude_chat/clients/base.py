import logging

import httpx

logger = logging.getLogger("claude_chat.clients")


def extract_final_response_text(done_data, fallback=""):
    """Extract only the terminal assistant text from a streaming ``done`` event."""
    blocks = done_data.get("content_blocks") if isinstance(done_data, dict) else None
    if not isinstance(blocks, list):
        return fallback
    text_parts = []
    found_text_block = False
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            found_text_block = True
            text_parts.append(str(block.get("text", "")))
    return "".join(text_parts) if found_text_block else fallback

def sanitize_error_message(err, max_length=300):
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
    if max_length is not None and len(msg) > max_length:
        msg = msg[:max_length] + "... (详细错误已写入本地日志)"
    return msg

def build_http_client(proxy_mode="system", proxy_url=""):
    """
    构建并返回一个配置好代理的 httpx.Client 实例。
    - "none": 禁用代理，禁用信任操作系统环境变量代理行为。
    - "custom": 使用用户自定义输入的代理地址（如 http://127.0.0.1:7890）。
    - "system": 默认选项，自动继承操作系统的环境变量（如 HTTP_PROXY, HTTPS_PROXY）。
    H5 修复：所有客户端统一配置 30s 超时（10s 连接超时），防止 API 端点无响应时永久阻塞。
    """
    timeout = httpx.Timeout(30.0, connect=10.0)
    if proxy_mode == "none":
        return httpx.Client(trust_env=False, timeout=timeout)
    elif proxy_mode == "custom" and isinstance(proxy_url, str) and proxy_url.strip():
        return httpx.Client(proxy=proxy_url.strip(), trust_env=False, timeout=timeout)
    else:
        return httpx.Client(timeout=timeout)


def build_anthropic_http_client(proxy_mode="system", proxy_url=""):
    """Build the ``httpx2`` client required by recent Anthropic SDK releases."""
    import httpx2

    timeout = httpx2.Timeout(30.0, connect=10.0)
    if proxy_mode == "none":
        return httpx2.Client(trust_env=False, timeout=timeout)
    if proxy_mode == "custom" and isinstance(proxy_url, str) and proxy_url.strip():
        return httpx2.Client(proxy=proxy_url.strip(), trust_env=False, timeout=timeout)
    return httpx2.Client(timeout=timeout)

def extract_api_message(msg, preserve_file_paths=False):
    """
    将本地保存的消息记录转换为供应商客户端可处理的内部消息结构。

    正式发送链路暂时保留受管文件路径，随后由各供应商客户端通过官方
    Files API 上传并替换为远端 ID/URI。调试预览默认只显示占位 ID，避免
    暴露服务器路径。
    """
    role = msg.get("role")
    content = msg.get("content")
    if not isinstance(content, (list, str)):
        return {"role": role, "content": [{"type": "text", "text": str(content)}]}
        
    items_source = content if isinstance(content, list) else [{"type": "text", "text": content}]
    api_content_list = []
    
    for item in items_source:
        if isinstance(item, dict):
            item = {key: value for key, value in item.items() if key != "_attachment"}
            source = item.get("source")
            if isinstance(source, dict) and source.get("file_path") and not preserve_file_paths:
                item["source"] = {
                    "type": "file",
                    "file_id": "[发送时由官方 Files API 返回]",
                    "media_type": source.get("media_type", "application/octet-stream"),
                }

        if isinstance(item, str):
            api_content_list.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            api_content_list.append(item)
        else:
            api_content_list.append({"type": "text", "text": str(item)})
            
    return {"role": role, "content": api_content_list}

