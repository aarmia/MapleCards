import asyncio
import math
from contextlib import asynccontextmanager
from uuid import uuid4

from app.errors import AppError


class QueueFullError(AppError):
    def __init__(self, retry_after: int):
        super().__init__(
            "진단 요청이 몰려 있습니다. 잠시 후 다시 시도해주세요.",
            status_code=503,
            code="QUEUE_FULL",
            retry_after=retry_after,
        )


class QueueTimeoutError(AppError):
    def __init__(self, retry_after: int):
        super().__init__(
            "진단 대기 시간이 길어 요청을 취소했습니다. 잠시 후 다시 시도해주세요.",
            status_code=503,
            code="QUEUE_TIMEOUT",
            retry_after=retry_after,
        )


class DiagnosticRequestTracker:
    """동시 진단 수를 제한하고 요청별 진행 단계를 기록합니다. 워커 프로세스 1개를 전제로 메모리에만 둡니다.

    - concurrency: 동시에 Nexon API 를 호출하는 진단 수.
    - max_waiting: 슬롯이 모두 찼을 때 줄을 설 수 있는 최대 수. 넘치면 QueueFullError(503).
    - wait_timeout: 줄에서 기다리는 최대 초. 넘으면 QueueTimeoutError(503).
    슬롯 점유 시간의 지수이동평균으로 예상 대기와 Retry-After 를 계산합니다.
    """

    RETRY_AFTER_MIN = 2
    RETRY_AFTER_MAX = 30
    EMA_WEIGHT = 0.2

    def __init__(
        self,
        concurrency: int = 5,
        max_waiting: int = 40,
        wait_timeout: float = 8.0,
        initial_service_time: float = 0.5,
    ):
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self.concurrency = concurrency
        self.max_waiting = max_waiting
        self.wait_timeout = wait_timeout
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._states: dict[str, str] = {}
        self._waiting: list[str] = []
        self._active = 0
        self._service_time = float(initial_service_time)
        self._samples = 0

    def estimate_wait(self, queue_position: int) -> float:
        """queue_position 번째 대기자가 슬롯을 받기까지의 예상 초."""
        return queue_position * self._service_time / self.concurrency

    def retry_after(self, queue_length: int) -> int:
        """대기열 길이가 queue_length 일 때 새 요청에 안내할 재시도 간격(초)."""
        estimate = math.ceil(self.estimate_wait(queue_length + 1))
        return max(self.RETRY_AFTER_MIN, min(self.RETRY_AFTER_MAX, estimate))

    def snapshot(self) -> dict:
        return {
            "active": self._active,
            "waiting": len(self._waiting),
            "service_time_ema": round(self._service_time, 3),
        }

    @asynccontextmanager
    async def track(self, request_id: str | None = None):
        request_id = request_id or uuid4().hex
        loop = asyncio.get_running_loop()

        async with self._lock:
            if self._semaphore.locked() and len(self._waiting) >= self.max_waiting:
                raise QueueFullError(self.retry_after(len(self._waiting)))
            self._states[request_id] = "waiting"
            self._waiting.append(request_id)

        acquired = False
        started = 0.0
        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self.wait_timeout)
            except TimeoutError:
                async with self._lock:
                    position = self._waiting.index(request_id) + 1 if request_id in self._waiting else 1
                raise QueueTimeoutError(self.retry_after(position)) from None
            acquired = True
            started = loop.time()
            async with self._lock:
                if request_id in self._waiting:
                    self._waiting.remove(request_id)
                self._states[request_id] = "character"
                self._active += 1
            yield request_id
        finally:
            if acquired:
                self._semaphore.release()
            async with self._lock:
                if request_id in self._waiting:
                    self._waiting.remove(request_id)
                self._states.pop(request_id, None)
                if acquired:
                    self._active -= 1
                    self._record_service_time(loop.time() - started)

    def _record_service_time(self, elapsed: float) -> None:
        if self._samples == 0:
            self._service_time = elapsed
        else:
            self._service_time = (1 - self.EMA_WEIGHT) * self._service_time + self.EMA_WEIGHT * elapsed
        self._samples += 1

    async def set_stage(self, request_id: str, stage: str):
        async with self._lock:
            if request_id in self._states:
                self._states[request_id] = stage

    async def status(self, request_id: str) -> dict:
        async with self._lock:
            stage = self._states.get(request_id, "unknown")
            queue_position = None
            estimated_wait = None
            if stage == "waiting" and request_id in self._waiting:
                queue_position = self._waiting.index(request_id) + 1
                estimated_wait = round(self.estimate_wait(queue_position), 1)
            return {
                "stage": stage,
                "queue_position": queue_position,
                "estimated_wait_seconds": estimated_wait,
            }
