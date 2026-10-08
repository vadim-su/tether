"""Frontend discovery: built-in `headless` plus the `tether.frontends` entry point group."""

import asyncio
import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from tether.api.frontend import Frontend
from tether.host.session import Session

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "tether.frontends"

type FrontendFactory = Callable[..., Frontend]


def available() -> dict[str, Callable[[], FrontendFactory]]:
    """Frontend names mapped to lazy loaders, so listing them imports nothing heavy."""

    def headless() -> FrontendFactory:
        from tether.frontends.headless import Headless

        return Headless

    found: dict[str, Callable[[], FrontendFactory]] = {"headless": headless}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        found[ep.name] = ep.load
    return found


def create(name: str, options: dict[str, Any] | None = None) -> Frontend:
    loaders = available()
    if name not in loaders:
        hint = f"; install tether-plugin-{name}?" if name != "headless" else ""
        raise LookupError(f"unknown frontend {name!r} (available: {', '.join(sorted(loaders))}){hint}")
    frontend = loaders[name]()(**(options or {}))
    if not isinstance(frontend, Frontend):
        raise TypeError(f"frontend {name!r} has no `async run(session)`")
    return frontend


async def run_frontends(session: Session, frontends: list[Frontend]) -> None:
    """Run every frontend on the session; when the first one exits, stop the rest."""
    tasks = [asyncio.create_task(f.run(session), name=f"frontend:{type(f).__name__}") for f in frontends]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for task in done:
        task.result()  # re-raise a frontend's crash
