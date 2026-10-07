"""Isolate collection as well as test execution from personal app state."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# These are manual integration scripts with module-level execution, not tests.
collect_ignore = [
    "test_ds.py",
    "test_ds2.py",
    "test_ds3.py",
    "test_ds_search.py",
    "test_gem_search.py",
    "test_save_config.py",
]


def pytest_configure(config):
    from contextlib import ExitStack

    import keyring

    import claude_chat.config as settings
    import claude_chat.db as database

    stack = ExitStack()
    config._claude_chat_test_isolation = stack
    root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="claude-chat-tests-")))
    for module, paths in (
        (
            settings,
            {
                "CONFIG_PATH": root / "config.json",
                "LOG_PATH": root / "app.log",
                "CONVERSATIONS_DIR": root / "conversations",
                "ATTACHMENT_STORE_DIR": root / "attachments",
            },
        ),
        (
            database,
            {
                "DB_PATH": root / "chat.db",
                "CONVERSATIONS_DIR": root / "conversations",
                "BACKUP_DIR": root / "conversations_backup",
            },
        ),
    ):
        for name, path in paths.items():
            stack.enter_context(patch.object(module, name, path))
    # Never read or overwrite the real Windows credential store during tests.
    credentials = {}
    stack.enter_context(
        patch.object(keyring, "get_password", side_effect=lambda service, name: credentials.get((service, name)))
    )
    stack.enter_context(
        patch.object(
            keyring,
            "set_password",
            side_effect=lambda service, name, value: credentials.__setitem__((service, name), value),
        )
    )
    stack.enter_context(
        patch.object(
            keyring, "delete_password", side_effect=lambda service, name: credentials.pop((service, name), None)
        )
    )


def pytest_unconfigure(config):
    stack = getattr(config, "_claude_chat_test_isolation", None)
    if stack is not None:
        stack.close()
