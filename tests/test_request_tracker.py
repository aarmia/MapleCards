import asyncio
import unittest

from app.request_tracker import DiagnosticRequestTracker


class DiagnosticRequestTrackerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_waiting_position_and_stage(self):
        tracker = DiagnosticRequestTracker(concurrency=1)
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def first_request():
            async with tracker.track("first"):
                first_entered.set()
                await release_first.wait()

        async def second_request():
            async with tracker.track("second") as request_id:
                await tracker.set_stage(request_id, "equipment")
                return await tracker.status(request_id)

        first_task = asyncio.create_task(first_request())
        await first_entered.wait()
        second_task = asyncio.create_task(second_request())
        await asyncio.sleep(0)

        self.assertEqual(
            await tracker.status("second"),
            {"stage": "waiting", "queue_position": 1},
        )

        release_first.set()
        self.assertEqual(
            await second_task,
            {"stage": "equipment", "queue_position": None},
        )
        await first_task


if __name__ == "__main__":
    unittest.main()
