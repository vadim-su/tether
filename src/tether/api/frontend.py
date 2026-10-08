"""Frontends: whatever puts a session in front of a person (a terminal UI, a web page, stdout).

A frontend is any object with `async def run(session)`: it subscribes to `session.bus`,
feeds user input to `session.handle(...)`, answers requests with `session.answer(...)`,
and returns when the user is done. Package one as a plugin with an entry point:

```toml
[project.entry-points."tether.frontends"]
tui = "tether_plugin_tui:TuiFrontend"
```

The entry point names a factory (usually the class); it is called with the options
from the `host.<name>:` section of tether.yaml as keyword arguments.
"""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tether.host.session import Session


@runtime_checkable
class Frontend(Protocol):
    async def run(self, session: Session) -> Any:
        """Drive the session until the user quits."""
        ...
