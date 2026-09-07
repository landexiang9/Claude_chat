"""Stable JS-to-Python facade composed from focused domain services.

The public method names on :class:`WebAPI` are intentionally unchanged because
pywebview exposes them directly under ``window.pywebview.api`` and the HTTP
adapter calls the same facade.
"""

from claude_chat.platform_params import PlatformParamMapper
from claude_chat.services import (
    AppService,
    ConfigService,
    ConversationService,
    ExecutionService,
    FileService,
    ModelService,
)
from claude_chat.services.conversation_service import get_mime_type, prepare_attachment_content, read_text_file


class WebAPI(
    ConfigService,
    ConversationService,
    FileService,
    ExecutionService,
    ModelService,
):
    """Compatibility facade shared by pywebview and the HTTP server."""

    def __init__(self, app):
        AppService.__init__(self, app)


__all__ = ["PlatformParamMapper", "WebAPI", "get_mime_type", "prepare_attachment_content", "read_text_file"]
