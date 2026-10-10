"""OpenAI Responses streaming adapter using locally managed conversation history."""

from __future__ import annotations

import copy
import json
import logging

from claude_chat.request_params import generation_params

from .base import build_http_client, record_stream_usage, sanitize_error_message
from .file_uploads import prepare_inline_files, prepare_openai_compatible_files, upload_cache_namespace
from .search_tools import execute_search_tool, search_tools

logger = logging.getLogger("claude_chat.clients")


def _as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    return {}


def _responses_base_url(api_url):
    url = (api_url or "https://api.openai.com/v1").strip().rstrip("/")
    if url.endswith("/responses"):
        url = url[: -len("/responses")]
    return url


def convert_messages_to_responses(messages, context_scope=""):
    """Convert app blocks, preserving native output only for its original endpoint/model."""
    result = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if role == "tool":
            result.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id", ""),
                    "output": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
                }
            )
            continue
        if not isinstance(content, list):
            result.append({"role": role, "content": str(content or "")})
            continue

        text = "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
        native = next(
            (
                block.get("_responses")
                for block in content
                if isinstance(block, dict) and isinstance(block.get("_responses"), dict)
            ),
            None,
        )
        if (
            role == "assistant"
            and context_scope
            and native
            and native.get("scope") == context_scope
            and native.get("text") == text
            and isinstance(native.get("output"), list)
            and native["output"]
            and all(isinstance(block, dict) and block.get("type") == "text" for block in content)
        ):
            result.extend(copy.deepcopy(native["output"]))
            continue

        parts = []

        def flush():
            if parts:
                result.append({"role": role, "content": list(parts)})
                parts.clear()

        for block in content:
            if not isinstance(block, dict):
                parts.append({"type": "input_text", "text": str(block)})
                continue
            kind = block.get("type")
            source = block.get("source") or {}
            if kind == "text":
                parts.append({"type": "input_text", "text": block.get("text", "")})
            elif kind == "image":
                part = {"type": "input_image"}
                if source.get("file_id"):
                    part["file_id"] = source["file_id"]
                elif source.get("type") == "base64" and source.get("data"):
                    part["image_url"] = f"data:{source.get('media_type', 'image/png')};base64,{source['data']}"
                elif source.get("url"):
                    part["image_url"] = source["url"]
                else:
                    raise ValueError("Responses 图片附件不可用，请重新添加附件")
                parts.append(part)
            elif kind in {"document", "file"}:
                if source.get("file_id"):
                    parts.append({"type": "input_file", "file_id": source["file_id"]})
                elif source.get("file_data"):
                    parts.append(
                        {
                            "type": "input_file",
                            "filename": source.get("filename", "document.pdf"),
                            "file_data": source["file_data"],
                        }
                    )
                elif source.get("type") == "base64" and source.get("data"):
                    parts.append(
                        {
                            "type": "input_file",
                            "filename": block.get("title") or "document.pdf",
                            "file_data": f"data:{source.get('media_type', 'application/pdf')};base64,{source['data']}",
                        }
                    )
                else:
                    raise ValueError("Responses 文档附件不可用，请重新添加附件")
            elif kind in {"tool_use", "server_tool_use"}:
                flush()
                result.append(
                    {
                        "type": "function_call",
                        "call_id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    }
                )
            elif kind in {"tool_result", "web_search_tool_result"}:
                flush()
                output = block.get("content", "")
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": block.get("tool_use_id", ""),
                        "output": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                    }
                )
            # Other providers' thinking blocks are not Responses reasoning items.
        flush()
    return result


class _ResponseText:
    """Reconcile deltas and final snapshots without emitting text twice."""

    def __init__(self, queue):
        self.queue = queue
        self.parts = {}

    def add(self, channel, item_id, index, text, *, snapshot=False):
        if not text:
            return
        key = (channel, item_id, index)
        previous = self.parts.get(key, "")
        delta = text
        if snapshot:
            if not text.startswith(previous):
                return
            delta = text[len(previous) :]
        self.parts[key] = previous + delta
        if delta:
            self.queue.put((channel, delta))

    def item(self, item):
        item_id = item.get("id", "")
        if item.get("type") == "message":
            for index, part in enumerate(item.get("content", [])):
                if part.get("type") in {"output_text", "refusal"}:
                    self.add("text", item_id, index, part.get("text") or part.get("refusal", ""), snapshot=True)
        elif item.get("type") == "reasoning":
            for index, part in enumerate(item.get("content") or item.get("summary", [])):
                self.add("thinking", item_id, index, part.get("text", ""), snapshot=True)

    def text(self, channel):
        return "".join(value for (kind, _, _), value in self.parts.items() if kind == channel)


class _NativeSearch:
    """Translate server search items into UI events, retaining opaque native context."""

    def __init__(self, queue):
        self.queue = queue
        self.started = set()
        self.records = {}

    def item(self, item, *, done=False):
        if item.get("type") != "web_search_call":
            return
        item_id = item.get("id", "")
        action = item.get("action") or {}
        query = (
            action.get("query") or "、".join(action.get("queries") or []) or action.get("url") or "DeepSeek 官方搜索"
        )
        if item_id not in self.started:
            self.started.add(item_id)
            self.queue.put(("search_start", {"id": item_id, "query": query}))
        if done and item_id not in self.records:
            results = [
                {"url": source["url"], "title": source.get("title") or source["url"], "snippet": ""}
                for source in action.get("sources", [])
                if isinstance(source, dict) and source.get("url")
            ]
            record = {"id": item_id, "query": query, "results": results, "engine": "deepseek_native", "usage": None}
            self.records[item_id] = record
            self.queue.put(("search_done", record))


def stream_responses_response(
    api_key,
    api_url,
    proxy_mode,
    proxy_url,
    messages,
    model,
    max_tokens,
    temperature,
    streaming_queue,
    abort_event=None,
    on_stream_created=None,
    system=None,
    thinking_config=None,
    custom_params=None,
    request_params=None,
    file_upload_purpose="user_data",
    file_upload_expires_in_seconds=172800,
    file_upload_enabled=True,
    deepseek_native=False,
    enable_search=False,
    search_engine="google",
    enable_web_fetch=True,
    tavily_api_key="",
    jina_api_key="",
    web_page_parser="local",
    web_fetch_limit=15000,
    memory_session=None,
):
    response_stream = client = http_client = None

    def aborted():
        return abort_event is not None and abort_event.is_set()

    try:
        if aborted():
            streaming_queue.put(("aborted", {}))
            return
        from openai import OpenAI

        api_url = _responses_base_url(api_url)
        effective = generation_params(
            "responses",
            model,
            max_tokens,
            temperature,
            thinking_config,
            custom=custom_params,
            override=request_params,
        )
        # Local history remains authoritative (including edits, retries and branches).
        if deepseek_native:
            if enable_search:
                effective["tools"] = search_tools("responses", enable_web_fetch)
        else:
            effective.setdefault("store", False)
            effective["include"] = list(dict.fromkeys([*effective.get("include", []), "reasoning.encrypted_content"]))
        from claude_chat.memory_tools import TOOL_NAMES
        base_tools = list(effective.get("tools", []))
        http_client = build_http_client(proxy_mode, proxy_url)
        client = OpenAI(api_key=api_key, base_url=api_url, http_client=http_client)
        scope = upload_cache_namespace(f"responses:{model}", api_key, api_url)
        if file_upload_enabled:
            prepared = prepare_openai_compatible_files(
                client,
                messages,
                upload_cache_namespace("responses-files", api_key, api_url),
                purpose=file_upload_purpose,
                expires_after_seconds=file_upload_expires_in_seconds,
            )
        else:
            prepared = prepare_inline_files(messages, openrouter_documents=True)
        if aborted():
            streaming_queue.put(("aborted", {}))
            return
        kwargs = {"model": model, "input": convert_messages_to_responses(prepared, scope), "stream": True}
        if system and system.strip():
            kwargs["instructions"] = system.strip()
        text = _ResponseText(streaming_queue)
        search = _NativeSearch(streaming_queue)
        all_output = []
        searches = []
        fetches = []
        input_tokens = output_tokens = 0
        for round_index in range(6):
            if memory_session:
                tools = [*base_tools, *memory_session.tools("responses")]
                if tools:
                    effective["tools"] = tools
                else:
                    effective.pop("tools", None)
            if aborted():
                streaming_queue.put(("aborted", {}))
                return
            response_stream = client.responses.create(**kwargs, extra_body=effective)
            if on_stream_created:
                on_stream_created(response_stream)

            for event in response_stream:
                event = _as_dict(event)
                kind = event.get("type", "")
                if kind in {"response.completed", "response.incomplete", "response.failed"}:
                    record_stream_usage(streaming_queue, (event.get("response") or {}).get("usage") or {})
                if aborted():
                    streaming_queue.put(("aborted", {}))
                    return
                if kind in {
                    "response.output_text.delta",
                    "response.refusal.delta",
                    "response.reasoning_summary_text.delta",
                    "response.reasoning_text.delta",
                }:
                    channel = "thinking" if "reasoning" in kind else "text"
                    index = (
                        event.get("summary_index", 0) if "reasoning_summary" in kind else event.get("content_index", 0)
                    )
                    text.add(channel, event.get("item_id", ""), index, event.get("delta", ""))
                elif kind in {
                    "response.output_text.done",
                    "response.refusal.done",
                    "response.reasoning_summary_text.done",
                    "response.reasoning_text.done",
                }:
                    channel = "thinking" if "reasoning" in kind else "text"
                    index = (
                        event.get("summary_index", 0) if "reasoning_summary" in kind else event.get("content_index", 0)
                    )
                    text.add(
                        channel,
                        event.get("item_id", ""),
                        index,
                        event.get("text") or event.get("refusal", ""),
                        snapshot=True,
                    )
                elif kind in {"response.output_item.added", "response.output_item.done"}:
                    item = event.get("item") or {}
                    if kind.endswith(".done"):
                        text.item(item)
                    if deepseek_native:
                        search.item(item, done=kind.endswith(".done"))
                elif deepseek_native and kind.startswith("response.web_search_call."):
                    # The completed status event has no action; wait for output_item.done.
                    search.item({"type": "web_search_call", "id": event.get("item_id", "")})
                elif kind in {"response.completed", "response.incomplete", "response.failed"}:
                    response = event.get("response") or {}
                    output = response.get("output") or []
                    for item in output:
                        text.item(item)
                        if deepseek_native:
                            search.item(item, done=True)
                    if kind != "response.completed":
                        record_usage = getattr(streaming_queue, "record_usage", None)
                        if callable(record_usage):
                            record_usage(response.get("usage") or {})
                        error = response.get("error") or {}
                        reason = (response.get("incomplete_details") or {}).get("reason", "unknown")
                        message = error.get("message") or (
                            "已达到输出 token 上限，请提高 Max Output Tokens 后重试"
                            if reason == "max_output_tokens"
                            else f"生成未完成: {reason}"
                        )
                        raise RuntimeError(message)
                    usage = response.get("usage") or {}
                    input_tokens += usage.get("input_tokens") or 0
                    output_tokens += usage.get("output_tokens") or 0
                    all_output.extend(output)
                    calls = [item for item in output if item.get("type") == "function_call"]
                    if calls and ((deepseek_native and enable_search) or
                                  (memory_session and any(call.get("name") in TOOL_NAMES for call in calls))):
                        if memory_session and any(call.get("name") in TOOL_NAMES for call in calls):
                            memory_session.begin_round()
                        response_stream.close()
                        response_stream = None
                        if round_index >= 5:
                            raise RuntimeError("联网搜索已达到最大轮数，请缩小查询范围后重试")
                        kwargs["input"].extend(output)
                        for call in calls:
                            if call.get("name") == "fetch_webpage" and not enable_web_fetch:
                                raise RuntimeError("网页读取已关闭")
                            if memory_session and call.get("name") in TOOL_NAMES:
                                result_text = memory_session.execute(call.get("name"), call.get("arguments", "{}"),
                                                                     call.get("call_id", ""))
                                record = {}
                            else:
                                result_text, record = execute_search_tool(
                                call.get("name"),
                                call.get("arguments", "{}"),
                                call.get("call_id", ""),
                                streaming_queue,
                                api_key=api_key,
                                api_url=api_url,
                                proxy_mode=proxy_mode,
                                proxy_url=proxy_url,
                                search_engine=search_engine,
                                tavily_api_key=tavily_api_key,
                                jina_api_key=jina_api_key,
                                web_page_parser=web_page_parser,
                                web_fetch_limit=web_fetch_limit,
                                abort_event=abort_event,
                                on_stream_created=on_stream_created,
                            )
                            if call.get("name") == "search_web":
                                searches.append(record)
                                if record.get("engine") == "deepseek_native":
                                    tool_usage = record.get("usage") or {}
                                    input_tokens += tool_usage.get("input_tokens") or 0
                                    output_tokens += tool_usage.get("output_tokens") or 0
                            elif call.get("name") == "fetch_webpage":
                                fetches.append(record)
                            result = {
                                "type": "function_call_output",
                                "call_id": call.get("call_id", ""),
                                "output": result_text,
                            }
                            kwargs["input"].append(result)
                            all_output.append(result)
                        # Continue the same generation with tool results in local history.
                        break
                    full_text = text.text("text")
                    block = {"type": "text", "text": full_text}
                    # Keep native message items beside encrypted reasoning for stateless continuation.
                    native_output = [
                        item
                        for item in all_output
                        if item.get("type") == "message"
                        or (item.get("type") == "reasoning" and item.get("encrypted_content"))
                        or (
                            (deepseek_native or memory_session)
                            and item.get("type")
                            in {"reasoning", "web_search_call", "function_call", "function_call_output"}
                        )
                    ]
                    if native_output:
                        block["_responses"] = {"scope": scope, "text": full_text, "output": native_output}
                        if search.records or searches:
                            block["_responses"]["searches"] = [*search.records.values(), *searches]
                        if fetches:
                            block["_responses"]["fetches"] = fetches
                    streaming_queue.put(
                        (
                            "done",
                            {
                                "usage_source": (
                                    "reported" if all(k in usage for k in ("input_tokens", "output_tokens"))
                                    else "unknown"
                                ),
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "content_blocks": [block],
                                "thinking": text.text("thinking"),
                            },
                        )
                    )
                    return
                elif kind == "error":
                    raise RuntimeError(event.get("message") or "Responses 返回错误")
            else:
                if aborted():
                    streaming_queue.put(("aborted", {}))
                    return
                else:
                    raise RuntimeError("连接在生成完成前中断，请重试")
    except Exception as exc:
        if aborted():
            streaming_queue.put(("aborted", {}))
        else:
            message = sanitize_error_message(exc).replace(api_key, "***") if api_key else sanitize_error_message(exc)
            logger.error("Responses streaming error: %s", message)
            streaming_queue.put(("error", f"Responses 错误: {message}"))
    finally:
        for resource in (response_stream, client, http_client):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
