import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4


class DiagnosticRequestTracker:
    def __init__(self, concurrency: int = 5):
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._states = {}
        self._waiting = []

    @asynccontextmanager
    async def track(self, request_id: str | None = None):
        request_id = request_id or uuid4().hex
        async with self._lock:
            self._states[request_id] = "waiting"
            self._waiting.append(request_id)

        acquired = False
        try:
            await self._semaphore.acquire()
            acquired = True
            async with self._lock:
                if request_id in self._waiting:
                    self._waiting.remove(request_id)
                self._states[request_id] = "character"
            yield request_id
        finally:
            if acquired:
                self._semaphore.release()
            async with self._lock:
                if request_id in self._waiting:
                    self._waiting.remove(request_id)
                self._states.pop(request_id, None)

    async def set_stage(self, request_id: str, stage: str):
        async with self._lock:
            if request_id in self._states:
                self._states[request_id] = stage

    async def status(self, request_id: str) -> dict:
        async with self._lock:
            stage = self._states.get(request_id, "unknown")
            queue_position = None
            if stage == "waiting" and request_id in self._waiting:
                queue_position = self._waiting.index(request_id) + 1
            return {"stage": stage, "queue_position": queue_position}
