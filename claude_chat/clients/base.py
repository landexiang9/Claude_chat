import base64
import json
import logging
from pathlib import Path
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

logger = logging.getLogger("claude_chat.clients")

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
    H5 修复：所有客户端统一配置 30s 超时（10s 连接超时），防止 API 端点无响应时永久阻塞。
    """
    timeout = httpx.Timeout(30.0, connect=10.0)
    if proxy_mode == "none":
        return httpx.Client(trust_env=False, timeout=timeout)
    elif proxy_mode == "custom" and isinstance(proxy_url, str) and proxy_url.strip():
        return httpx.Client(proxy=proxy_url.strip(), trust_env=False, timeout=timeout)
    else:
        return httpx.Client(timeout=timeout)

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

