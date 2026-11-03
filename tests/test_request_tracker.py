import asyncio
import unittest

from app.request_tracker import DiagnosticRequestTracker, QueueFullError, QueueTimeoutError


async def occupy_slot(tracker):
    """슬롯 하나를 붙잡는 요청을 띄우고 (해제 이벤트, task) 를 돌려줍니다."""
    entered, release = asyncio.Event(), asyncio.Event()

    async def holder():
        async with tracker.track("holder"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    return release, task


class DiagnosticRequestTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_queue_position_then_stage(self):
        tracker = DiagnosticRequestTracker(concurrency=1, initial_service_time=0.5)
        release, holder = await occupy_slot(tracker)

        async def second():
            async with tracker.track("second") as request_id:
                await tracker.set_stage(request_id, "equipment")
                return await tracker.status(request_id)

        task = asyncio.create_task(second())
        await asyncio.sleep(0)
        self.assertEqual(
            await tracker.status("second"),
            {"stage": "waiting", "queue_position": 1, "estimated_wait_seconds": 0.5},
        )

        release.set()
        self.assertEqual((await task)["stage"], "equipment")
        await holder
        self.assertEqual((await tracker.status("second"))["stage"], "unknown")

    async def test_full_queue_is_rejected_with_retry_after(self):
        tracker = DiagnosticRequestTracker(concurrency=1, max_waiting=0)
        release, holder = await occupy_slot(tracker)

        with self.assertRaises(QueueFullError) as context:
            async with tracker.track("overflow"):
                pass

        self.assertEqual((context.exception.status_code, context.exception.code), (503, "QUEUE_FULL"))
        self.assertGreaterEqual(context.exception.retry_after, 2)
        release.set()
        await holder

    async def test_wait_timeout_releases_cleanly(self):
        tracker = DiagnosticRequestTracker(concurrency=1, wait_timeout=0.05)
        release, holder = await occupy_slot(tracker)

        with self.assertRaises(QueueTimeoutError) as context:
            async with tracker.track("late"):
                pass

        self.assertEqual(context.exception.code, "QUEUE_TIMEOUT")
        self.assertEqual(tracker.snapshot()["waiting"], 0)
        release.set()
        await holder
        async with tracker.track("next") as request_id:
            self.assertEqual((await tracker.status(request_id))["stage"], "character")


if __name__ == "__main__":
    unittest.main()
