import urllib.parse
import re
import logging
import html
import ipaddress
import socket
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("claude_chat")


def _is_safe_web_url(url):
    """
    SSRF 防护:校验 http(s) URL 不指向私有/环回/链路本地/保留地址。
    用于 fetch_webpage_content 等对 LLM 可控 URL 的出站抓取。
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local or
                ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True

def build_search_client(proxy_mode="system", proxy_url=""):
    """
    构建配置了代理且支持自动重定向的 httpx 客户端实例。
    """
    if proxy_mode == "none":
        return httpx.Client(trust_env=False, follow_redirects=True)
    elif proxy_mode == "custom" and proxy_url.strip():
        return httpx.Client(proxy=proxy_url.strip(), trust_env=False, follow_redirects=True)
    else:
        return httpx.Client(trust_env=True, follow_redirects=True)

def get_browser_headers():
    """
    生成模拟现代 Chrome 浏览器的核心 Headers，包含 Client Hints 和 Fetch Metadata 属性，
    能够大幅降低在 Google/Bing 等搜索引擎检索时被直接判定为自动化 Bot 拦截的概率。
    """
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }

def fetch_google(query, client):
    logger.info(f"正在通过 Google 搜索: {query}")
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    
    response = client.get(url, headers=get_browser_headers(), timeout=12.0)
    if response.status_code != 200:
        raise Exception(f"Google 返回 HTTP 状态码: {response.status_code}")
        
    text = response.text
    results = []
    
    soup = BeautifulSoup(text, 'html.parser')
    for a in soup.find_all('a', href=True):
        if a.find('h3'):
            title = a.get_text(strip=True)
            href = a['href']
            url = href
            if "/url?q=" in href:
                url = href.split("/url?q=")[1].split("&")[0]
                url = urllib.parse.unquote(url)
            
            if "google.com" in url and ("/search" in url or "/preferences" in url or "/settings" in url or "accounts.google" in url):
                continue
                
            if title and url.startswith("http"):
                results.append({"title": title, "url": url, "snippet": ""})
                
    return results

def fetch_bing(query, client):
    logger.info(f"正在通过 Bing 搜索: {query}")
    url = f"https://bing.com/search?q={urllib.parse.quote(query)}"
    
    response = client.get(url, headers=get_browser_headers(), timeout=12.0)
    if response.status_code != 200:
        raise Exception(f"Bing 返回 HTTP 状态码: {response.status_code}")
        
    text = response.text
    results = []
    
    soup = BeautifulSoup(text, 'html.parser')
    for h2 in soup.find_all('h2'):
        a = h2.find('a', href=True)
        if a:
            url = a['href']
            if url.startswith("javascript:") or url.startswith("#") or "bing.com" in url:
                continue
            title = a.get_text(strip=True)
            
            snippet = ""
            parent_li = h2.find_parent('li', class_=lambda c: c and 'b_algo' in c)
            if parent_li:
                p_tag = parent_li.find('p')
                if p_tag:
                    snippet = p_tag.get_text(strip=True)
            
            if not snippet:
                next_p = h2.find_next('p')
                if next_p:
                    snippet = next_p.get_text(strip=True)
                
            if title and url.startswith("http"):
                results.append({"title": title, "url": url, "snippet": snippet})
                
    return results

def fetch_duckduckgo(query, client):
    logger.info(f"正在通过 DuckDuckGo 搜索: {query}")
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
    }
    data = {"q": query}
    
    response = client.post(url, data=data, headers=headers, timeout=15.0)
    if response.status_code != 200:
        raise Exception(f"DuckDuckGo 返回 HTTP 状态码: {response.status_code}")
        
    text = response.text
    results = []
    
    soup = BeautifulSoup(text, 'html.parser')
    results_divs = soup.find_all('div', class_=lambda c: c and 'result' in c and 'results_links' in c)
    for block in results_divs:
        a_tag = block.find('a', class_=lambda c: c and 'result__a' in c)
        if not a_tag or not a_tag.has_attr('href'):
            continue
            
        title = a_tag.get_text(strip=True)
        raw_url = a_tag['href']
        
        url = raw_url
        if "/l/?uddg=" in raw_url:
            uddg_part = raw_url.split("/l/?uddg=")[1].split("&")[0]
            url = urllib.parse.unquote(uddg_part)
        elif "uddg=" in raw_url:
            parts = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
            if 'uddg' in parts:
                url = parts['uddg'][0]
                
        snippet = ""
        snippet_a = block.find('a', class_=lambda c: c and 'result__snippet' in c)
        if snippet_a:
            snippet = snippet_a.get_text(strip=True)
            
        if title and url.startswith("http"):
            results.append({"title": title, "url": url, "snippet": snippet})
            
    return results

def fetch_tavily(query, api_key, client):
    logger.info(f"正在通过 Tavily API 搜索: {query}")
    if not api_key:
        raise Exception("Tavily API key is missing")
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": 6
    }
    response = client.post(url, json=payload, timeout=15.0)
    if response.status_code != 200:
        raise Exception(f"Tavily 返回 HTTP 状态码 {response.status_code}: {response.text}")
    
    data = response.json()
    results = []
    for item in data.get("results", []):
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        snippet = item.get("content", "").strip()
        if title and url.startswith("http"):
            results.append({"title": title, "url": url, "snippet": snippet})
    return results

def fetch_tavily_usage(api_key, client):
    """
    异步拉取 Tavily API key 的使用量额度
    """
    try:
        url = "https://api.tavily.com/usage"
        headers = {"Authorization": f"Bearer {api_key}"}
        response = client.get(url, headers=headers, timeout=5.0)
        if response.status_code == 200:
            data = response.json()
            key_data = data.get("key", {})
            if "usage" in key_data and "limit" in key_data:
                return {
                    "used": key_data["usage"],
                    "limit": key_data["limit"]
                }
    except Exception as e:
        logger.warning(f"获取 Tavily 额度失败: {e}")
    return None

def fetch_jina_search(query, api_key, client):
    """
    使用 Jina Search API (s.jina.ai) 进行搜索并返回结构化数据及 API 额度
    """
    logger.info(f"正在通过 Jina Search API 搜索: {query}")
    url = f"https://s.jina.ai/{urllib.parse.quote(query)}"
    headers = {
        "Accept": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    response = client.get(url, headers=headers, timeout=15.0)
    if response.status_code != 200:
        raise Exception(f"Jina Search 返回 HTTP 状态码 {response.status_code}: {response.text}")
        
    # 解析额度
    usage_info = {}
    rem_req = response.headers.get("x-ratelimit-remaining-requests")
    rem_tok = response.headers.get("x-ratelimit-remaining-tokens")
    if rem_req is not None:
        usage_info["remaining_requests"] = rem_req
    if rem_tok is not None:
        usage_info["remaining_tokens"] = rem_tok
        
    data = response.json()
    items = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    
    results = []
    for item in items:
        title = item.get("title", "").strip()
        url = item.get("url", "").strip()
        snippet = item.get("content", "").strip()
        if title and url.startswith("http"):
            if len(snippet) > 300:
                snippet = snippet[:300] + "..."
            results.append({"title": title, "url": url, "snippet": snippet})
            
    return results, usage_info

def search_web(query, engine="google", proxy_mode="system", proxy_url="", tavily_api_key="", jina_api_key=""):
    """
    提供统一的联网搜索接口。
    若选择的主引擎获取失败，则自动降级执行 Fallback。
    返回值: (results_list, engine_used, usage_info)
    """
    engines_chain = []
    
    # 根据用户选择的主引擎编排降级顺序
    if engine == "jina":
        engines_chain = ["jina", "tavily", "google", "bing", "duckduckgo"]
    elif engine == "tavily":
        engines_chain = ["tavily", "google", "bing", "duckduckgo"]
    elif engine == "google":
        engines_chain = ["google", "bing", "duckduckgo"]
    elif engine == "bing":
        engines_chain = ["bing", "google", "duckduckgo"]
    else:
        engines_chain = ["duckduckgo", "google", "bing"]
        
    try:
        with build_search_client(proxy_mode, proxy_url) as client:
            for eng in engines_chain:
                try:
                    usage_info = None
                    if eng == "jina":
                        res, usage_info = fetch_jina_search(query, jina_api_key, client)
                    elif eng == "tavily":
                        if not tavily_api_key.strip():
                            raise Exception("Tavily API key is empty, skipping...")
                        res = fetch_tavily(query, tavily_api_key, client)
                        usage_info = fetch_tavily_usage(tavily_api_key, client)
                    elif eng == "google":
                        res = fetch_google(query, client)
                    elif eng == "bing":
                        res = fetch_bing(query, client)
                    else:
                        res = fetch_duckduckgo(query, client)
                        
                    if res:
                        logger.info(f"搜索引擎 {eng} 成功抓取到 {len(res)} 条结果。")
                        return res[:6], eng, usage_info
                    else:
                        logger.warning(f"搜索引擎 {eng} 抓取结果为空，尝试降级...")
                except Exception as e:
                    logger.warning(f"搜索引擎 {eng} 执行失败: {e}，尝试降级...")
    except Exception as outer_err:
        logger.error(f"构建或使用 HTTP 客户端出错: {outer_err}")
            
    logger.error("所有备选搜索引擎均未成功抓取到结果！")
    return [], "none", None

def fetch_webpage_content(url, parser_type="local", jina_api_key="", proxy_mode="system", proxy_url="", max_web_fetch_length=15000):
    """
    抓取并提取指定网页 URL 的文本正文内容，支持使用 Local HTML Extractor 或 Jina Reader API。
    返回值: (clean_text, usage_info)
    """
    logger.info(f"开始抓取网页内容: {url}, 解析器类型: {parser_type}")
    usage_info = None

    # SSRF 防护:URL 由 LLM 工具(input)提供,可能经 prompt 注入指向内网/元数据地址。
    # 校验后再放行,本地直连与 Jina 代理两种路径均受保护。
    if not _is_safe_web_url(url):
        logger.warning(f"拒绝抓取不安全的网页 URL: {url}")
        return f"Error: URL rejected for security reasons (internal/private host not allowed): {url}", None

    try:
        with build_search_client(proxy_mode, proxy_url) as client:
            if parser_type == "jina":
                jina_url = f"https://r.jina.ai/{url}"
                headers = {
                    "Accept": "text/plain"
                }
                if jina_api_key:
                    headers["Authorization"] = f"Bearer {jina_api_key}"
                
                response = client.get(jina_url, headers=headers, timeout=18.0)
                if response.status_code == 200:
                    # 捕获额度响应头
                    rem_req = response.headers.get("x-ratelimit-remaining-requests")
                    rem_tok = response.headers.get("x-ratelimit-remaining-tokens")
                    if rem_req is not None:
                        usage_info = usage_info or {}
                        usage_info["remaining_requests"] = rem_req
                    if rem_tok is not None:
                        usage_info = usage_info or {}
                        usage_info["remaining_tokens"] = rem_tok
                    
                    text = response.text.strip()
                    # 长度裁剪以保护 token 上下文
                    if max_web_fetch_length and len(text) > max_web_fetch_length:
                        text = text[:max_web_fetch_length] + "\n\n... (content truncated due to length limits) ..."
                    return text, usage_info
                else:
                    logger.warning(f"Jina Reader API 返回 HTTP 状态码 {response.status_code}，将降级使用本地解析器。")
            
            # 本地解析器提取模式
            headers = get_browser_headers()
            response = client.get(url, headers=headers, timeout=15.0)
            if response.status_code != 200:
                return f"Error: Failed to fetch the webpage. HTTP status code: {response.status_code}", None
                
            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type and "application/xhtml+xml" not in content_type:
                return f"Error: Unsupported content type ({content_type}) for textual webpage fetching.", None
                
            html_text = response.text
            
            if parser_type == "local_raw":
                clean_text = html_text
            else:
                # 使用轻量正则匹配和清除无用标签
                html_text = re.sub(r'<(script|style|noscript|header|footer|nav|head)[^>]*>([\s\S]*?)</\1>', ' ', html_text, flags=re.IGNORECASE)
                html_text = re.sub(r'<!--([\s\S]*?)-->', ' ', html_text)
                
                # 提取正文文本并解码 HTML 实体
                text = re.sub(r'<[^>]+>', ' ', html_text)
                text = html.unescape(text)
                
                # 去除多余空行和前后空格
                lines = [line.strip() for line in text.splitlines()]
                chunks = [phrase.strip() for line in lines for phrase in line.split("  ")]
                clean_text = '\n'.join(chunk for chunk in chunks if chunk)
            
            if max_web_fetch_length and len(clean_text) > max_web_fetch_length:
                clean_text = clean_text[:max_web_fetch_length] + "\n\n... (content truncated due to length limits) ..."
            
            if not clean_text.strip():
                return "The webpage content is empty or contains no readable text.", None
                
            return clean_text, None
            
    except Exception as e:
        logger.error(f"抓取网页 {url} 失败: {e}")
        return f"Error: Failed to read webpage content due to connection/parsing error: {str(e)}", None
