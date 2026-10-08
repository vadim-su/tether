"""tether-plugin-tui: a terminal UI for tether on Textual.

Installed next to tether, it registers the `tui` frontend:

    tether chat --ui tui

or, in tether.yaml:

    host:
      frontends: [tui]
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tether.host.session import Session

__all__ = ["TuiFrontend"]
__version__ = "0.1.0"


class TuiFrontend:
    """The `tui` frontend. Options come from the `host.tui:` section of tether.yaml."""

    def __init__(self, **options: Any) -> None:
        self.options = options

    async def run(self, session: Session) -> None:
        from tether_plugin_tui.app import TetherApp

        app = TetherApp(session, **self.options)
        with _logs_to(app):
            await app.run_async()


class _NotifyHandler(logging.Handler):
    def __init__(self, app: Any) -> None:
        super().__init__(logging.WARNING)
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            severity = "error" if record.levelno >= logging.ERROR else "warning"
            self.app.notify(record.getMessage(), severity=severity)
        except Exception:
            pass


class _logs_to:
    """While the TUI owns the terminal, show log warnings as notifications instead of writing to stderr."""

    def __init__(self, app: Any) -> None:
        self.handler = _NotifyHandler(app)
        self.saved: list[logging.Handler] = []

    def __enter__(self) -> None:
        root = logging.getLogger()
        self.saved = list(root.handlers)
        for handler in self.saved:
            root.removeHandler(handler)
        root.addHandler(self.handler)

    def __exit__(self, *exc: object) -> None:
        root = logging.getLogger()
        root.removeHandler(self.handler)
        for handler in self.saved:
            root.addHandler(handler)
