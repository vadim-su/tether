"""Fan-out of session events to every attached frontend."""

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

type Subscriber = Callable[[Any], Awaitable[None] | None]


class Bus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        self._subscribers.append(subscriber)
        return lambda: self._subscribers.remove(subscriber)

    async def publish(self, event: Any) -> None:
        for subscriber in list(self._subscribers):
            try:
                result = subscriber(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # one broken frontend must not stop the others
                log.exception("subscriber %r failed on %s", subscriber, type(event).__name__)
