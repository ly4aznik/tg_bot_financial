from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from time import monotonic
from typing import Any

import httpx
from telegram.ext import Application
from telegram.request import HTTPXRequest


LOGGER = logging.getLogger(__name__)


class MonitoredPollingRequest(HTTPXRequest):
    """A getUpdates client that avoids stale proxy connections and exposes liveness."""

    def __init__(self) -> None:
        super().__init__(
            connection_pool_size=1,
            connect_timeout=10.0,
            read_timeout=20.0,
            write_timeout=10.0,
            pool_timeout=5.0,
            http_version="1.1",
            httpx_kwargs={
                # Long-lived CONNECT tunnels through Xray have repeatedly ended
                # up stuck in CLOSE_WAIT. Do not reuse them for the next poll.
                "limits": httpx.Limits(
                    max_connections=1,
                    max_keepalive_connections=0,
                ),
            },
        )
        self.last_success_monotonic = monotonic()

    async def do_request(self, *args: Any, **kwargs: Any) -> tuple[int, bytes]:
        result = await super().do_request(*args, **kwargs)
        self.last_success_monotonic = monotonic()
        return result


async def watch_polling(
    application: Application,
    request: MonitoredPollingRequest,
    *,
    stale_after: float = 120.0,
    check_interval: float = 15.0,
) -> None:
    """Stop the application when getUpdates has not completed successfully."""

    while True:
        await asyncio.sleep(check_interval)
        stale_for = monotonic() - request.last_success_monotonic
        if stale_for <= stale_after:
            continue
        LOGGER.critical(
            "Telegram polling has been unhealthy for %.1f seconds; stopping for Docker restart",
            stale_for,
        )
        application.stop_running()
        return


async def cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
