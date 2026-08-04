"""Static checks for service, HTTP adapter, and platform-settings boundaries."""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_chat.http_router import HttpApiRouter


def class_methods(path, class_name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


class ServiceArchitectureTests(unittest.TestCase):
    def test_webapi_is_a_small_compatibility_facade(self):
        path = ROOT / "claude_chat" / "api_bridge.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WebAPI")
        self.assertEqual(
            [base.id for base in cls.bases],
            ["ConfigService", "ConversationService", "FileService", "ExecutionService", "ModelService"],
        )
        self.assertLess(path.stat().st_size, 5_000)

    def test_each_domain_service_owns_its_expected_operations(self):
        services = ROOT / "claude_chat" / "services"
        expected = {
            "ConfigService": {"get_config", "save_config", "check_parsers", "get_logs", "clear_logs"},
            "ConversationService": {
                "load_conversations", "load_conversation", "new_conversation", "delete_conversation",
                "get_message_packet", "send_message", "edit_and_resend", "retry_message",
                "branch_conversation", "abort_generation", "_start_stream_generation",
            },
            "FileService": {
                "paste_from_clipboard", "select_attachments", "save_code_block", "save_image",
                "upload_dropped_file", "get_attachment_preview",
            },
            "ExecutionService": {
                "check_code_sandbox_environment", "install_code_sandbox_environment", "start_code_execution",
                "send_console_input", "kill_console_process",
            },
            "ModelService": {
                "update_model_registry", "fetch_models", "list_custom_providers", "add_custom_provider",
                "update_custom_provider", "remove_custom_provider", "_provider_view",
            },
        }
        files = {
            "ConfigService": "config_service.py",
            "ConversationService": "conversation_service.py",
            "FileService": "file_service.py",
            "ExecutionService": "execution_service.py",
            "ModelService": "model_service.py",
        }
        for class_name, methods in expected.items():
            self.assertTrue(methods.issubset(class_methods(services / files[class_name], class_name)))

    def test_server_delegates_api_routes(self):
        server = (ROOT / "claude_chat" / "server.py").read_text(encoding="utf-8")
        router = (ROOT / "claude_chat" / "http_router.py").read_text(encoding="utf-8")
        self.assertNotIn("def handle_api_get", server)
        self.assertNotIn("def handle_api_post", server)
        self.assertIn("HttpApiRouter(self).dispatch_get(path)", server)
        self.assertIn("def dispatch_get", router)
        self.assertIn("def dispatch_post", router)
        self.assertIn("def dispatch_delete", router)

    def test_platform_settings_are_registered_before_orchestrator(self):
        ui = ROOT / "claude_chat" / "ui"
        html = (ui / "index.html").read_text(encoding="utf-8")
        for platform in ("claude", "deepseek", "gemini"):
            component = f"settings_{platform}.js"
            source = (ui / component).read_text(encoding="utf-8")
            self.assertIn(f'PlatformSettings.register("{platform}"', source)
            self.assertLess(html.index(f'src="{component}"'), html.index('src="settings.js"'))
        settings = (ui / "settings.js").read_text(encoding="utf-8")
        self.assertIn("PlatformSettings.loadAll(config)", settings)
        self.assertIn("PlatformSettings.saveAll(config)", settings)
        self.assertNotIn("updateThinkingSettingsUI()", settings)

    def test_router_dispatches_query_and_validates_message_index(self):
        class FakeApi:
            def __init__(self):
                self.platform = None

            def fetch_models(self, platform):
                self.platform = platform
                return [{"id": "model-a"}]

        class FakeHandler:
            def __init__(self):
                self.path = "/api/models?platform=gemini"
                self.server = SimpleNamespace(api=FakeApi())
                self.responses = []
                self.errors = []

            def send_json_response(self, data, status=200):
                self.responses.append((status, data))

            def send_error(self, status, message=None):
                self.errors.append((status, message))

        handler = FakeHandler()
        router = HttpApiRouter(handler)
        router.dispatch_get("/api/models")
        self.assertEqual(handler.server.api.platform, "gemini")
        self.assertEqual(handler.responses, [(200, [{"id": "model-a"}])])

        router.dispatch_get("/api/message_packet/conversation-a/not-a-number")
        self.assertEqual(handler.errors[-1][0], 400)

    def test_router_dispatches_delete_without_business_logic(self):
        class FakeApi:
            def __init__(self):
                self.deleted = None

            def delete_conversation(self, conversation_id):
                self.deleted = conversation_id
                return True

        class FakeHandler:
            def __init__(self):
                self.server = SimpleNamespace(api=FakeApi())
                self.response = None

            def send_json_response(self, data, status=200):
                self.response = (status, data)

            def send_error(self, status, message=None):
                raise AssertionError((status, message))

        handler = FakeHandler()
        HttpApiRouter(handler).dispatch_delete("/api/conversation/conversation-a")
        self.assertEqual(handler.server.api.deleted, "conversation-a")
        self.assertEqual(handler.response, (200, {"success": True}))


if __name__ == "__main__":
    unittest.main()
