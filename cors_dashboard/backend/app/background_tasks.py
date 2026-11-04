"""Keep a strong reference to scheduled tasks until completion."""

import asyncio
from collections.abc import Coroutine
from typing import Any


_pending: set[asyncio.Task] = set()


def schedule_background(coroutine: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coroutine)
    _pending.add(task)
    task.add_done_callback(_pending.discard)
