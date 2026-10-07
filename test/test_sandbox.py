"""Standalone tests for the cross-platform sandbox policy helpers."""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_chat import sandbox


class SandboxTests(unittest.TestCase):
    def test_execution_timeout_is_bounded(self):
        self.assertEqual(sandbox.normalize_execution_timeout(None), 30)
        self.assertEqual(sandbox.normalize_execution_timeout("45"), 45)
        self.assertEqual(sandbox.normalize_execution_timeout(0), 1)
        self.assertEqual(sandbox.normalize_execution_timeout(9999), 600)
        self.assertEqual(sandbox.normalize_execution_timeout(True), 30)

    def test_language_normalization_is_allowlist_only(self):
        self.assertEqual(sandbox.normalize_language("Python"), "python")
        self.assertEqual(sandbox.normalize_language("js"), "javascript")
        self.assertIsNone(sandbox.normalize_language("html"))
        self.assertIsNone(sandbox.normalize_language("python; calc.exe"))

    def test_linux_missing_docker_is_fail_closed(self):
        with patch.object(sandbox.platform, "system", return_value="Linux"), patch.object(
            sandbox.shutil, "which", return_value=None
        ):
            status = sandbox.check_sandbox_environment()
        self.assertFalse(status["ready"])
        self.assertEqual(status["backend"], "docker")
        self.assertTrue(status["download_url"].startswith("https://"))

    def test_linux_environment_requires_both_runtime_images(self):
        def fake_run(command, timeout=10.0):
            if command[1] == "version":
                return subprocess.CompletedProcess(command, 0, "27.0.0\n", "")
            image = command[-1]
            return subprocess.CompletedProcess(
                command,
                0 if image == sandbox.PYTHON_IMAGE else 1,
                "sha256:test-python\n" if image == sandbox.PYTHON_IMAGE else "",
                "" if image == sandbox.PYTHON_IMAGE else "missing",
            )

        with patch.object(sandbox.platform, "system", return_value="Linux"), patch.object(
            sandbox.shutil, "which", return_value="/usr/bin/docker"
        ), patch.object(sandbox, "_run_checked", side_effect=fake_run):
            status = sandbox.check_sandbox_environment()
        self.assertFalse(status["ready"])
        self.assertTrue(status["can_install"])
        node = next(item for item in status["components"] if item["id"] == "node")
        self.assertFalse(node["ready"])

    def test_docker_launch_has_security_limits_and_no_code_in_argv(self):
        ready = {
            "platform": "linux",
            "backend": "docker",
            "ready": True,
            "components": [
                {"id": "docker", "ready": True},
                {"id": "python", "ready": True},
                {"id": "node", "ready": True},
            ],
        }
        code = "print('secret-value')"
        launch = None
        with patch.object(sandbox.platform, "system", return_value="Linux"), patch.object(
            sandbox, "check_sandbox_environment", return_value=ready
        ), patch.object(sandbox.shutil, "which", return_value="/usr/bin/docker"):
            launch = sandbox.prepare_linux_docker_launch(code, "python")
        try:
            command = launch.command
            self.assertIn("none", command)
            self.assertIn("--read-only", command)
            self.assertIn("--cap-drop", command)
            self.assertIn("no-new-privileges=true", command)
            self.assertIn("--memory", command)
            self.assertNotIn(code, command)
            self.assertEqual(launch.temp_dir.joinpath("input", "main.py").read_text(encoding="utf-8"), code)
        finally:
            if launch:
                shutil.rmtree(launch.temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
