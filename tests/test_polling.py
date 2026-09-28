from __future__ import annotations

import asyncio

import pytest

from expense_bot.polling import MonitoredPollingRequest, cancel_task, watch_polling


class FakeApplication:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop_running(self) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_watch_polling_stops_application_when_polling_is_stale() -> None:
    application = FakeApplication()
    request = MonitoredPollingRequest()
    request.last_success_monotonic = 0.0

    await watch_polling(
        application,  # type: ignore[arg-type]
        request,
        stale_after=0.0,
        check_interval=0.0,
    )

    assert application.stop_calls == 1
    await request.shutdown()


@pytest.mark.asyncio
async def test_cancel_task_cancels_running_watchdog() -> None:
    task = asyncio.create_task(asyncio.sleep(60))

    await cancel_task(task)  # type: ignore[arg-type]

    assert task.cancelled()
