class AppService:
    """Base class for services operating on one ClaudeChat application."""

    def __init__(self, app):
        self._app = app
