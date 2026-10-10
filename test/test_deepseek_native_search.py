"""Offline real-HTTP-client regressions for native search and both chat protocols."""

import copy
import json
import queue
import threading
import unittest
from unittest.mock import patch

import httpx

from claude_chat.clients.deepseek import stream_deepseek_response
from claude_chat.clients.deepseek_search import parse_search_response, search_deepseek, search_endpoint
from claude_chat.clients.responses import convert_messages_to_responses, stream_responses_response


def search_payload():
    source = {"type": "web_search_result", "url": "https://example.test/source", "title": "Source", "page_age": "today"}
    return {
        "content": [
            {"type": "web_search_tool_result", "tool_use_id": "search1", "content": [source, copy.deepcopy(source)]},
            {
                "type": "text",
                "text": "Untrusted provider answer",
                "citations": [{"url": source["url"], "cited_text": "Evidence"}],
            },
        ],
        "usage": {"input_tokens": 100, "output_tokens": 20, "server_tool_use": {"web_search_requests": 1}},
    }


def sse(events, *, chat=False):
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    if chat:
        body += "data: [DONE]\n\n"
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)


def terminal(output, in_tokens=10, out_tokens=5):
    return {
        "type": "response.completed",
        "response": {
            "id": "r1",
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
        },
    }


def answer():
    return {
        "id": "m1",
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "Answer", "annotations": []}],
    }


class DeepSeekSearchTests(unittest.TestCase):
    def test_endpoint_uses_configured_origin_and_prefix(self):
        for value, expected in (
            ("https://api.deepseek.com", "https://api.deepseek.com/anthropic/v1/messages"),
            ("https://api.deepseek.com/v1", "https://api.deepseek.com/anthropic/v1/messages"),
            ("https://gateway.test/prefix/v1/responses", "https://gateway.test/prefix/anthropic/v1/messages"),
            ("https://gateway.test/anthropic/v1/messages", "https://gateway.test/anthropic/v1/messages"),
        ):
            with self.subTest(value=value):
                self.assertEqual(search_endpoint(value), expected)
        with self.assertRaises(ValueError):
            search_endpoint("https://user:secret@example.test")

    def test_structured_results_join_citations_deduplicate_and_include_usage(self):
        results, engine, usage = parse_search_response(search_payload())
        self.assertEqual(engine, "deepseek_native")
        self.assertEqual(
            results,
            [{"title": "Source", "url": "https://example.test/source", "snippet": "Evidence", "page_age": "today"}],
        )
        self.assertEqual(usage, {"input_tokens": 100, "output_tokens": 20, "web_search_requests": 1})
        for payload in (
            {"content": [{"type": "text", "text": "I searched and found results"}]},
            {
                "content": [
                    {
                        "type": "web_search_tool_result",
                        "content": {"type": "web_search_tool_result_error", "error_code": "unavailable"},
                    }
                ]
            },
        ):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                parse_search_response(payload)
        self.assertEqual(parse_search_response({"content": [{"type": "web_search_tool_result", "content": []}]})[0], [])

    def test_request_headers_tool_and_no_redirect_credential_leak(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json=search_payload())

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch("claude_chat.clients.deepseek_search.build_http_client", return_value=client):
            results, _, _ = search_deepseek("query", "test-key", "https://example.test", "none")
        self.assertTrue(client.is_closed)
        self.assertEqual(len(results), 1)
        request = requests[0]
        self.assertEqual(str(request.url), "https://example.test/anthropic/v1/messages")
        self.assertEqual(request.headers["x-api-key"], "test-key")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        self.assertEqual(
            json.loads(request.content)["tools"], [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
        )
        requests.clear()

        def redirect(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "https://other.test/messages"})

        client = httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True)
        with (
            patch("claude_chat.clients.deepseek_search.build_http_client", return_value=client),
            self.assertRaisesRegex(RuntimeError, "HTTP 302"),
        ):
            search_deepseek("query", "test-key", "https://example.test")
        self.assertEqual(len(requests), 1)

    def run_chat(
        self,
        protocol,
        *,
        search_status=200,
        payload=None,
        abort_on_search=False,
        engine="deepseek_native",
        enable=True,
        fetch=False,
    ):
        captured, clients = [], []
        abort = threading.Event()

        def respond(request):
            body = json.loads(request.content)
            captured.append((str(request.url), body))
            if request.url.path.endswith("/anthropic/v1/messages"):
                if abort_on_search:
                    abort.set()
                return httpx.Response(search_status, json=payload if payload is not None else search_payload())
            count = sum("/anthropic/" not in url for url, _ in captured)
            name, arguments = (
                ("fetch_webpage", {"url": "https://example.test/source"})
                if fetch
                else ("search_web", {"query": "news"})
            )
            if protocol == "responses":
                output = (
                    [
                        {
                            "id": "f1",
                            "type": "function_call",
                            "call_id": "c1",
                            "name": name,
                            "arguments": json.dumps(arguments),
                            "status": "completed",
                        }
                    ]
                    if count == 1 and enable
                    else [answer()]
                )
                return sse([terminal(output)])
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
                if count == 1 and enable
                else {"content": "Answer"}
            )
            return sse(
                [
                    {
                        "id": "chat1",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": delta,
                                "finish_reason": "tool_calls" if "tool_calls" in delta else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    }
                ],
                chat=True,
            )

        def build(*args):
            client = httpx.Client(transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        events = queue.Queue()
        options = dict(
            api_key="test-key",
            api_url="https://example.test",
            proxy_mode="none",
            proxy_url="",
            messages=[{"role": "user", "content": "Search"}],
            model="deepseek-flash",
            max_tokens=1000,
            temperature=0.7,
            streaming_queue=events,
            enable_search=enable,
            search_engine=engine,
            file_upload_enabled=False,
            abort_event=abort,
        )
        target = stream_responses_response if protocol == "responses" else stream_deepseek_response
        if protocol == "responses":
            options["deepseek_native"] = True
        with (
            patch(
                f"claude_chat.clients.{protocol if protocol == 'responses' else 'deepseek'}.build_http_client",
                side_effect=build,
            ),
            patch("claude_chat.clients.deepseek_search.build_http_client", side_effect=build),
        ):
            target(**options)
        self.assertTrue(all(client.is_closed for client in clients))
        return captured, list(events.queue)

    def test_both_protocols_search_then_continue_with_sources_and_correct_total_usage(self):
        for protocol in ("deepseek", "responses"):
            with self.subTest(protocol=protocol):
                captured, events = self.run_chat(protocol)
                self.assertEqual(len(captured), 3)
                self.assertEqual(captured[1][0], "https://example.test/anthropic/v1/messages")
                tools = captured[0][1]["tools"]
                self.assertTrue(all(tool["type"] == "function" for tool in tools))
                self.assertIn("Evidence", json.dumps(captured[2][1]))
                self.assertNotIn("Untrusted provider answer", json.dumps(captured[2][1]))
                self.assertEqual(sum(kind == "search_start" for kind, _ in events), 1)
                search = next(data for kind, data in events if kind == "search_done")
                self.assertEqual(len(search["results"]), 1)
                self.assertEqual(events[-1][0], "done")
                done = events[-1][1]
                self.assertEqual((done["input_tokens"], done["output_tokens"]), (120, 30))
                if protocol == "responses":
                    content = done["content_blocks"]
                    native = content[0]["_responses"]
                    self.assertEqual(native["searches"], [search])
                    reloaded = json.loads(json.dumps([{"role": "assistant", "content": content}]))
                    self.assertEqual(convert_messages_to_responses(reloaded, native["scope"]), native["output"])
                    self.assertEqual(
                        [item["type"] for item in native["output"]],
                        ["function_call", "function_call_output", "message"],
                    )

    def test_missing_result_block_http_failure_and_cancellation_are_not_success(self):
        for protocol in ("deepseek", "responses"):
            for options, expected in (
                ({"payload": {"content": [{"type": "text", "text": "I searched"}]}}, "error"),
                ({"search_status": 401}, "error"),
                ({"abort_on_search": True}, "aborted"),
            ):
                with self.subTest(protocol=protocol, options=options):
                    captured, events = self.run_chat(protocol, **options)
                    self.assertEqual(len(captured), 2)
                    self.assertEqual(events[-1][0], expected)
                    self.assertFalse(any(kind in {"search_done", "done"} for kind, _ in events))

    def test_switch_off_and_existing_engine_do_not_call_official_search(self):
        for protocol in ("deepseek", "responses"):
            with self.subTest(protocol=protocol):
                captured, events = self.run_chat(protocol, enable=False)
                self.assertEqual(len(captured), 1)
                self.assertNotIn("tools", captured[0][1])
                with patch(
                    "claude_chat.search.search_web",
                    return_value=(
                        [{"title": "Other", "url": "https://example.test", "snippet": "Other evidence"}],
                        "google",
                        None,
                    ),
                ) as fallback:
                    captured, events = self.run_chat(protocol, engine="google")
                self.assertEqual(len(captured), 2)
                fallback.assert_called_once()
                self.assertEqual(events[-1][1]["input_tokens"], 20)

    def test_responses_fetch_result_is_persisted(self):
        with patch("claude_chat.search.fetch_webpage_content", return_value=("Page body", None)):
            captured, events = self.run_chat("responses", fetch=True)
        self.assertEqual(len(captured), 2)
        self.assertIn("Page body", json.dumps(captured[-1][1]))
        native = events[-1][1]["content_blocks"][0]["_responses"]
        self.assertEqual(native["fetches"][0]["content_len"], 9)


if __name__ == "__main__":
    unittest.main()
