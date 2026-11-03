import asyncio
import unittest

from app.request_tracker import DiagnosticRequestTracker, QueueFullError, QueueTimeoutError


class DiagnosticRequestTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def occupy_slot(self, tracker, request_id="holder"):
        """슬롯 하나를 붙잡고 있는 요청을 만들고 (해제 이벤트, task) 를 돌려줍니다."""
        entered = asyncio.Event()
        release = asyncio.Event()

        async def holder():
            async with tracker.track(request_id):
                entered.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await entered.wait()
        return release, task

    async def test_reports_waiting_position_and_stage(self):
        tracker = DiagnosticRequestTracker(concurrency=1, initial_service_time=0.5)
        release, holder_task = await self.occupy_slot(tracker)

        async def second_request():
            async with tracker.track("second") as request_id:
                await tracker.set_stage(request_id, "equipment")
                return await tracker.status(request_id)

        second_task = asyncio.create_task(second_request())
        await asyncio.sleep(0)

        self.assertEqual(
            await tracker.status("second"),
            {"stage": "waiting", "queue_position": 1, "estimated_wait_seconds": 0.5},
        )

        release.set()
        self.assertEqual(
            await second_task,
            {"stage": "equipment", "queue_position": None, "estimated_wait_seconds": None},
        )
        await holder_task
        self.assertEqual(await tracker.status("second"), {"stage": "unknown", "queue_position": None, "estimated_wait_seconds": None})

    async def test_rejects_new_requests_when_queue_is_full(self):
        tracker = DiagnosticRequestTracker(concurrency=1, max_waiting=1)
        release, holder_task = await self.occupy_slot(tracker)

        async def waiter():
            async with tracker.track("waiter"):
                pass

        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        self.assertEqual((await tracker.status("waiter"))["queue_position"], 1)

        with self.assertRaises(QueueFullError) as context:
            async with tracker.track("overflow"):
                pass

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "QUEUE_FULL")
        self.assertGreaterEqual(context.exception.retry_after, DiagnosticRequestTracker.RETRY_AFTER_MIN)
        self.assertEqual((await tracker.status("overflow"))["stage"], "unknown")
        self.assertEqual(tracker.snapshot()["waiting"], 1)

        release.set()
        await waiter_task
        await holder_task

    async def test_zero_queue_still_admits_when_a_slot_is_free(self):
        tracker = DiagnosticRequestTracker(concurrency=1, max_waiting=0)

        async with tracker.track("direct") as request_id:
            self.assertEqual((await tracker.status(request_id))["stage"], "character")

    async def test_times_out_while_waiting_and_releases_cleanly(self):
        tracker = DiagnosticRequestTracker(concurrency=1, wait_timeout=0.05)
        release, holder_task = await self.occupy_slot(tracker)

        with self.assertRaises(QueueTimeoutError) as context:
            async with tracker.track("late"):
                pass

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "QUEUE_TIMEOUT")
        self.assertEqual(tracker.snapshot()["waiting"], 0)
        self.assertEqual((await tracker.status("late"))["stage"], "unknown")

        release.set()
        await holder_task

        # 타임아웃된 요청이 슬롯을 망가뜨리지 않아 다음 요청은 바로 들어갑니다.
        async with tracker.track("next") as request_id:
            self.assertEqual((await tracker.status(request_id))["stage"], "character")

    async def test_cancelled_waiter_is_removed_from_queue(self):
        tracker = DiagnosticRequestTracker(concurrency=1)
        release, holder_task = await self.occupy_slot(tracker)

        async def waiter():
            async with tracker.track("cancelled"):
                pass

        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        self.assertEqual(tracker.snapshot()["waiting"], 1)

        waiter_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter_task
        self.assertEqual(tracker.snapshot()["waiting"], 0)

        release.set()
        await holder_task

    async def test_retry_after_follows_service_time_and_is_clamped(self):
        tracker = DiagnosticRequestTracker(concurrency=2, initial_service_time=1.0)

        self.assertEqual(tracker.retry_after(3), 2)  # (3 + 1) × 1.0 ÷ 2
        self.assertEqual(tracker.retry_after(100), DiagnosticRequestTracker.RETRY_AFTER_MAX)
        self.assertEqual(tracker.retry_after(0), DiagnosticRequestTracker.RETRY_AFTER_MIN)

        async with tracker.track("sample"):
            await asyncio.sleep(0.01)

        snapshot = tracker.snapshot()
        self.assertLess(snapshot["service_time_ema"], 1.0)
        self.assertEqual(snapshot["active"], 0)
        self.assertEqual(snapshot["waiting"], 0)


if __name__ == "__main__":
    unittest.main()
