"""Responses wire protocol, local history, provider selection and failure regressions."""

import copy
import json
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from claude_chat.clients.dispatcher import stream_claude_response
from claude_chat.clients.responses import convert_messages_to_responses, stream_responses_response
from claude_chat.platform_params import PlatformParamMapper
from claude_chat.provider_adapters import adapter_accepts_attachment, normalize_custom_provider_adapter
from claude_chat.request_params import generation_params, preview_generation_params, validate_request_params
from claude_chat.services.config_service import ConfigService
from claude_chat.services.conversation_service import messages_for_api


def message_item(text="Hello"):
    return {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def completed(output=None):
    return {
        "type": "response.completed",
        "response": {
            "id": "resp_1",
            "status": "completed",
            "output": output if output is not None else [message_item()],
            "usage": {"input_tokens": 21, "output_tokens": 8},
        },
    }


class ResponsesTests(unittest.TestCase):
    def test_deepseek_legacy_search_reasoning_and_stateless_replay(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_1",
            "content": [{"type": "reasoning_text", "text": "Check sources"}],
        }
        searches = [
            {
                "type": "web_search_call",
                "id": "ws_1",
                "status": "completed",
                "action": {"type": "search", "queries": ["news today"]},
            },
            {
                "type": "web_search_call",
                "id": "ws_2",
                "status": "completed",
                "action": {"type": "open_page", "url": "https://example.test/news"},
            },
        ]
        output = [reasoning, *searches, message_item()]
        wire = [
            {"type": "response.reasoning_text.delta", "item_id": "rs_1", "content_index": 0, "delta": "Check "},
            {"type": "response.reasoning_text.done", "item_id": "rs_1", "content_index": 0, "text": "Check sources"},
        ]
        for item in searches:
            wire.extend(
                [
                    {"type": "response.output_item.added", "item": {**item, "status": "in_progress"}},
                    {"type": "response.web_search_call.searching", "item_id": item["id"]},
                    {"type": "response.web_search_call.completed", "item_id": item["id"]},
                    {"type": "response.output_item.done", "item": item},
                ]
            )
        captured, events = self.call(
            [*wire, completed(output)], deepseek_native=True, enable_search=True, model="deepseek-v4-flash"
        )
        body = captured[0][1]
        self.assertTrue(all(tool["type"] == "function" for tool in body["tools"]))
        self.assertEqual(body["tools"][0]["name"], "search_web")
        self.assertNotIn("include", body)
        self.assertNotIn("store", body)
        self.assertEqual("".join(data for kind, data in events if kind == "thinking"), "Check sources")
        self.assertEqual(sum(kind == "search_start" for kind, _ in events), 2)
        finished = [data for kind, data in events if kind == "search_done"]
        self.assertEqual([item["query"] for item in finished], ["news today", "https://example.test/news"])
        self.assertEqual(events[-1][0], "done")
        content = events[-1][1]["content_blocks"]
        native = content[0]["_responses"]
        self.assertEqual(native["searches"], finished)
        self.assertEqual(native["output"][0]["content"], reasoning["content"])
        self.assertEqual(native["output"][1:3], searches)
        # Round-trip through JSON as persisted history, then make the next request.
        history = json.loads(
            json.dumps([{"role": "assistant", "content": content}, {"role": "user", "content": "More?"}])
        )
        captured, _ = self.call(
            [completed()], deepseek_native=True, enable_search=False, model="deepseek-v4-flash", messages=history
        )
        self.assertEqual(captured[0][1]["input"][:4], native["output"])
        self.assertNotIn("tools", captured[0][1])
        # Terminal-only responses also restore search and reasoning UI events.
        _, terminal_events = self.call([completed(output)], deepseek_native=True)
        self.assertEqual(sum(kind == "search_done" for kind, _ in terminal_events), 2)

    def test_deepseek_toggle_routes_and_restores_protocol_parameters(self):
        chat_params = {"max_tokens": 2048, "reasoning_effort": "medium", "frequency_penalty": 0.4}
        response_params = {"max_output_tokens": 3072, "reasoning": {"effort": "low"}, "text": {"verbosity": "low"}}
        config = {
            "deepseek_api_key": "deepseek-secret",
            "deepseek_api_url": "https://example.test/v1",
            "model_configs": {
                "deepseek-chat": {
                    "request_protocol": "responses",
                    "request_params": response_params,
                    "request_params_by_protocol": {"deepseek": chat_params, "responses": response_params},
                }
            },
        }
        for enabled, expected, client_name in (
            (False, chat_params, "stream_deepseek_response"),
            (True, response_params, "stream_responses_response"),
            (False, chat_params, "stream_deepseek_response"),
        ):
            with self.subTest(enabled=enabled):
                config["deepseek_use_responses"] = enabled
                mapped = PlatformParamMapper.map_params("deepseek", config, "deepseek-chat")
                self.assertEqual(preview_generation_params("deepseek", "deepseek-chat", config), expected)
                with patch(f"claude_chat.clients.dispatcher.{client_name}") as client:
                    stream_claude_response(
                        api_key="",
                        proxy_mode="none",
                        proxy_url="",
                        messages=[],
                        model="deepseek-chat",
                        max_tokens=mapped["max_tokens"],
                        temperature=mapped["temperature"],
                        thinking_config=mapped["thinking_config"],
                        streaming_queue=queue.Queue(),
                        active_platform="deepseek",
                        provider_adapter=mapped["provider_adapter"],
                        deepseek_api_key=mapped["api_key"],
                        deepseek_api_url=mapped["api_url"],
                        request_params=mapped["request_params"],
                        file_upload_enabled=False,
                        enable_search=True,
                    )
                self.assertEqual(client.call_args.kwargs["api_key"], "deepseek-secret")
                self.assertEqual(client.call_args.kwargs["api_url"], "https://example.test/v1")
                self.assertEqual(client.call_args.kwargs["request_params"], expected)
                self.assertFalse(client.call_args.kwargs["file_upload_enabled"])
                self.assertTrue(client.call_args.kwargs["enable_search"])
                if enabled:
                    self.assertTrue(client.call_args.kwargs["deepseek_native"])

        # Draft previews honor an unsaved toggle without mutating the live setting.
        app = SimpleNamespace(lock=threading.RLock(), config=SimpleNamespace(data=config))
        service = ConfigService(app)
        preview = service.preview_model_request(
            {
                "model": "deepseek-chat",
                "platform": "deepseek",
                "deepseek_use_responses": True,
                "model_config": {"request_params": response_params, "request_protocol": "responses"},
            }
        )
        self.assertEqual(preview["request_protocol"], "responses")
        self.assertEqual(preview["params"], response_params)
        self.assertFalse(config["deepseek_use_responses"])
        # Old configs still use Chat Completions and keep their existing JSON.
        old = {"model_configs": {"deepseek-chat": {"request_params": chat_params}}}
        self.assertEqual(preview_generation_params("deepseek", "deepseek-chat", old), chat_params)
        old["deepseek_use_responses"] = True
        self.assertNotIn("frequency_penalty", preview_generation_params("deepseek", "deepseek-chat", old))

    def test_responses_file_upload_switch_off_uses_inline_image(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.png"
            image.write_bytes(b"image")
            messages = [
                {
                    "role": "user",
                    "content": [{"type": "image", "source": {"file_path": str(image), "media_type": "image/png"}}],
                }
            ]
            with patch("openai.resources.files.Files.create") as upload:
                captured, events = self.call([completed()], messages=messages, file_upload_enabled=False)
            upload.assert_not_called()
            self.assertEqual(
                captured[0][1]["input"][0]["content"][0],
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                },
            )
            self.assertEqual(events[-1][0], "done")

    def test_gui_and_http_consumers_persist_native_context_in_sqlite(self):
        import tempfile
        from pathlib import Path

        from claude_chat.app import ClaudeChatApp
        from claude_chat.db import DatabaseManager
        from claude_chat.server import ClaudeChatHTTPHandler
        from claude_chat.stream_protocol import StreamEventQueue, StreamTask, StreamTaskState

        reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "persisted-ciphertext"}
        output = [reasoning, message_item()]
        _, events = self.call([completed(output)])
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("claude_chat.db.DB_PATH", Path(directory) / "history.db"),
                patch.object(DatabaseManager, "migrate_json_files"),
            ):
                database = DatabaseManager()
                for transport in ("gui", "http"):
                    with self.subTest(transport=transport):
                        conversation = database.new_conversation()
                        conv_id = conversation["id"]
                        database.add_message(conv_id, "user", "Hi")
                        app = SimpleNamespace(
                            conv_manager=database,
                            current_conv=conversation,
                            lock=threading.RLock(),
                            set_streaming_done=MagicMock(),
                            _push_stream_event=MagicMock(),
                            _persist_stream_failure=MagicMock(),
                        )

                        def enqueue(event_queue):
                            app.stream_task = StreamTask(conv_id, event_queue)
                            for event in events:
                                event_queue.put(event)

                        if transport == "gui":
                            enqueue(StreamEventQueue(conversation_id=conv_id))
                            ClaudeChatApp._process_sending_stream(app, app.stream_task)
                        else:

                            def send(*args, custom_queue):
                                enqueue(custom_queue)
                                return True

                            handler = SimpleNamespace(
                                server=SimpleNamespace(api=SimpleNamespace(_app=app, send_message=send)),
                                send_response=MagicMock(),
                                send_header=MagicMock(),
                                end_headers=MagicMock(),
                                _send_cors_headers=MagicMock(),
                                _write_stream_line=MagicMock(),
                            )
                            ClaudeChatHTTPHandler.handle_streaming_generation(
                                handler, "send_message", conv_id, "Hi", []
                            )
                        app._persist_stream_failure.assert_not_called()
                        app.set_streaming_done.assert_called_once_with(StreamTaskState.COMPLETED, app.stream_task)
                        reloaded = database.load_conversation(conv_id)
                        self.assertEqual(reloaded["input_tokens"], 21)
                        self.assertEqual(reloaded["messages"][-1]["content"][0]["_responses"]["output"], output)
                        sent, _ = self.call([completed()], messages=messages_for_api(reloaded["messages"]))
                        self.assertEqual(sent[0][1]["input"][1:], output)

    def call(self, events, **overrides):
        """Use the real SDK with an offline HTTP transport, including its SSE parser."""
        captured = []

        def respond(request):
            captured.append((request.url, json.loads(request.content)))
            body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)

        http_client = httpx.Client(transport=httpx.MockTransport(respond))
        events_queue = queue.Queue()
        options = dict(
            api_key="test",
            api_url="https://example.test/v1",
            proxy_mode="none",
            proxy_url="",
            messages=[{"role": "user", "content": "Hi"}],
            model="gpt-5",
            max_tokens=1024,
            temperature=0.7,
            streaming_queue=events_queue,
            system="Be helpful",
        )
        options.update(overrides)
        with patch("claude_chat.clients.responses.build_http_client", return_value=http_client):
            stream_responses_response(**options)
        self.assertTrue(http_client.is_closed)
        return captured, list(events_queue.queue)

    def test_wire_format_streaming_summary_and_usage(self):
        reasoning = {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "Consider"}],
            "encrypted_content": "opaque-ciphertext",
        }
        events = [
            {
                "type": "response.reasoning_summary_text.delta",
                "item_id": "rs_1",
                "summary_index": 0,
                "delta": "Consider",
            },
            {"type": "response.output_text.delta", "item_id": "msg_1", "content_index": 0, "delta": "Hel"},
            {"type": "response.output_text.delta", "item_id": "msg_1", "content_index": 0, "delta": "lo"},
            {"type": "response.output_text.done", "item_id": "msg_1", "content_index": 0, "text": "Hello"},
            {"type": "response.output_item.done", "item": message_item()},
            completed([reasoning, message_item()]),
        ]
        captured, received = self.call(events, thinking_config={"effort": "high"})
        url, body = captured[0]
        self.assertEqual(url.path, "/v1/responses")
        self.assertEqual(body["input"], [{"role": "user", "content": "Hi"}])
        self.assertEqual(body["instructions"], "Be helpful")
        self.assertEqual(body["max_output_tokens"], 1024)
        self.assertEqual(body["reasoning"], {"effort": "high", "summary": "auto"})
        self.assertFalse(body["store"])
        self.assertIn("reasoning.encrypted_content", body["include"])
        for key in ("messages", "max_tokens", "reasoning_effort", "stream_options", "temperature"):
            self.assertNotIn(key, body)
        self.assertEqual("".join(text for kind, text in received if kind == "text"), "Hello")
        self.assertEqual("".join(text for kind, text in received if kind == "thinking"), "Consider")
        self.assertEqual(received[-1][0], "done")
        done = received[-1][1]
        self.assertEqual((done["input_tokens"], done["output_tokens"]), (21, 8))
        self.assertEqual(done["thinking"], "Consider")
        history = messages_for_api([{"role": "assistant", "content": done["content_blocks"], "thinking": "Consider"}])
        captured, _ = self.call([completed()], messages=history + [{"role": "user", "content": "Continue"}])
        self.assertEqual(captured[0][1]["input"][:2], [reasoning, message_item()])
        for changed in ({"api_url": "https://other.test/v1"}, {"api_key": "other"}, {"model": "gpt-4.1"}):
            captured, _ = self.call([completed()], messages=history, **changed)
            self.assertNotIn("opaque-ciphertext", json.dumps(captured[0][1]))
            self.assertIn("Hello", json.dumps(captured[0][1]))
        edited = copy.deepcopy(history)
        edited[0]["content"][0]["text"] = "Edited"
        captured, _ = self.call([completed()], messages=edited)
        self.assertNotIn("opaque-ciphertext", json.dumps(captured[0][1]))
        self.assertIn("Edited", json.dumps(captured[0][1]))

    def test_final_snapshot_without_deltas_and_refusal(self):
        refused = message_item()
        refused["content"] = [{"type": "refusal", "refusal": "Cannot answer"}]
        _, received = self.call([completed([refused])], api_url="https://example.test/v1/responses/")
        self.assertEqual(received[0], ("text", "Cannot answer"))
        self.assertEqual(received[-1][0], "done")

    def test_file_inputs_and_tool_history(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Compare"},
                    {"type": "image", "source": {"file_id": "file_image"}},
                    {"type": "document", "source": {"file_id": "file_pdf"}},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}},
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Other provider thinking"},
                    {"type": "tool_use", "id": "call_1", "name": "search_web", "input": {"query": "x"}},
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "Found"}]},
        ]
        original = copy.deepcopy(messages)
        result = convert_messages_to_responses(messages)
        self.assertEqual(
            result[0]["content"],
            [
                {"type": "input_text", "text": "Compare"},
                {"type": "input_image", "file_id": "file_image"},
                {"type": "input_file", "file_id": "file_pdf"},
                {"type": "input_image", "image_url": "data:image/png;base64,YWJj"},
            ],
        )
        self.assertEqual(result[1]["type"], "function_call")
        self.assertEqual(result[2], {"type": "function_call_output", "call_id": "call_1", "output": "Found"})
        self.assertEqual(messages, original)
        with self.assertRaisesRegex(ValueError, "附件不可用"):
            convert_messages_to_responses([{"role": "user", "content": [{"type": "image", "source": {}}]}])

    def test_local_upload_uses_native_input_types(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "image.png"
            image.write_bytes(b"png")
            document = Path(directory) / "document.pdf"
            document.write_bytes(b"pdf")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"file_path": str(image), "media_type": "image/png"}},
                        {"type": "document", "source": {"file_path": str(document), "media_type": "application/pdf"}},
                    ],
                }
            ]
            with patch(
                "openai.resources.files.Files.create",
                side_effect=[SimpleNamespace(id="img"), SimpleNamespace(id="pdf")],
            ) as upload:
                captured, received = self.call([completed()], messages=messages)
            self.assertEqual(upload.call_count, 2)
            self.assertEqual(upload.call_args.kwargs["purpose"], "user_data")
            self.assertEqual(
                captured[0][1]["input"][0]["content"],
                [
                    {"type": "input_image", "file_id": "img"},
                    {"type": "input_file", "file_id": "pdf"},
                ],
            )
            self.assertNotIn(directory, json.dumps(captured[0][1]))
            self.assertEqual(received[-1][0], "done")

    def test_error_incomplete_and_disconnected_stream_do_not_report_success(self):
        cases = [
            ([{"type": "error", "message": "bad request"}], "bad request"),
            ([{"type": "response.failed", "response": {"error": {"message": "rejected"}}}], "rejected"),
            (
                [
                    {
                        "type": "response.incomplete",
                        "response": {
                            "incomplete_details": {"reason": "max_output_tokens"},
                            "output": [message_item("Partial")],
                        },
                    }
                ],
                "token 上限",
            ),
            ([], "中断"),
        ]
        for events, expected in cases:
            with self.subTest(expected=expected):
                _, received = self.call(events)
                self.assertEqual(received[-1][0], "error")
                self.assertIn(expected, received[-1][1])
                self.assertNotIn("done", [kind for kind, _ in received])

    def test_cancel_closes_stream_and_reports_aborted(self):
        aborted = threading.Event()
        streams = []

        def created(stream):
            streams.append(stream)
            aborted.set()
            stream.close()

        _, received = self.call([completed()], abort_event=aborted, on_stream_created=created)
        self.assertEqual(received, [("aborted", {})])
        self.assertTrue(streams[0].response.is_closed)
        events = queue.Queue()
        with patch("claude_chat.clients.responses.build_http_client") as builder:
            stream_responses_response("x", "", "none", "", [], "test", 100, 0.7, events, abort_event=aborted)
        builder.assert_not_called()
        self.assertEqual(events.get_nowait(), ("aborted", {}))

    def test_provider_configuration_preview_and_dispatch(self):
        config = {
            "custom_providers": [
                {
                    "id": "test",
                    "provider_adapter": "responses",
                    "max_tokens": 2048,
                    "api_url": "https://example.test/v1",
                    "thinking_enabled": True,
                }
            ],
            "custom_test_api_key": "secret",
        }
        mapped = PlatformParamMapper.map_params("custom:test", config, "gpt-5")
        self.assertEqual(mapped["provider_adapter"], "responses")
        self.assertTrue(adapter_accepts_attachment("responses", ".pdf"))
        self.assertTrue(adapter_accepts_attachment("responses", ".png"))
        self.assertFalse(adapter_accepts_attachment("responses", ".docx"))
        self.assertEqual(normalize_custom_provider_adapter("openai_responses"), "responses")
        preview = preview_generation_params("custom:test", "gpt-5", config)
        self.assertEqual(preview["max_output_tokens"], 2048)
        self.assertNotIn("temperature", preview)
        app = SimpleNamespace(lock=threading.RLock(), config=SimpleNamespace(data=config))
        result = ConfigService(app).preview_model_request({"platform": "custom:test", "model": "gpt-5"})
        self.assertEqual(result["request_protocol"], "responses")
        self.assertEqual(result["params"], preview)
        options = dict(
            api_key="",
            proxy_mode="none",
            proxy_url="",
            messages=[],
            model="gpt-5",
            max_tokens=2048,
            temperature=0.7,
            thinking_config=None,
            streaming_queue=queue.Queue(),
            active_platform="custom:test",
            custom_api_key="secret",
            custom_api_url="https://example.test/v1",
            provider_adapter="responses",
        )
        with patch("claude_chat.clients.dispatcher.stream_responses_response") as responses:
            stream_claude_response(**options)
        self.assertEqual(responses.call_args.kwargs["api_key"], "secret")
        self.assertEqual(responses.call_args.kwargs["api_url"], "https://example.test/v1")
        options["messages"] = [
            {"role": "assistant", "content": [{"type": "text", "text": "Hello", "_responses": {"output": []}}]}
        ]
        options["provider_adapter"] = "anthropic"
        with patch("claude_chat.clients.dispatcher.stream_claude_response_native") as anthropic:
            stream_claude_response(**options)
        self.assertNotIn("_responses", anthropic.call_args.kwargs["messages"][0]["content"][0])
        self.assertIn("_responses", options["messages"][0]["content"][0])

    def test_parameter_migration_override_and_validation(self):
        old = {"max_tokens": 2048, "reasoning_effort": "medium", "top_p": 0.9}
        result = generation_params("responses", "test", 4096, 0.7, custom={"temperature": 0.5}, override=old)
        self.assertEqual(result, {"max_output_tokens": 2048, "reasoning": {"effort": "medium"}, "top_p": 0.9})
        self.assertIn("max_tokens", old)
        captured, _ = self.call([completed()], request_params=old)
        for key, value in result.items():
            self.assertEqual(captured[0][1][key], value)
        self.assertNotIn("temperature", captured[0][1])
        self.assertEqual(generation_params("responses", "test", 4096, 0.7, override={}), {})
        for params in (
            {"reasoning": []},
            {"reasoning": {"effort": False}},
            {"include": "x"},
            {"store": "false"},
            {"input": []},
            {"instructions": "override"},
            {"previous_response_id": "x"},
            {"background": True},
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_request_params(params, "responses")


if __name__ == "__main__":
    unittest.main()
