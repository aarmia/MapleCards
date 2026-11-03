"""IP 단위 슬라이딩 윈도우 레이트리밋. 워커 프로세스 1개를 전제로 메모리에만 둡니다.

기준은 "사람은 절대 닿지 않는 수치"입니다. 화면이 진행 중 중복 요청을 막고 1건에 0.5초 이상 걸리므로
사람은 분당 30건을 넘기 어렵습니다. 기본값(10분 150건, 1시간 600건)은 스크립트만 막습니다.
"""

import hashlib
import math
import time
from collections import deque

from app.errors import AppError

# Cloudflare 가 앞단에 있을 때 넣어 주는 헤더. 프록시가 덮어쓰므로 클라이언트가 위조할 수 없습니다.
_TRUSTED_CLIENT_IP_HEADERS = ("cf-connecting-ip", "true-client-ip")


class RateLimitedError(AppError):
    def __init__(self, retry_after: int):
        super().__init__(
            f"같은 네트워크에서 진단 요청이 너무 많습니다. {retry_after}초 후 다시 시도해주세요.",
            status_code=429,
            code="RATE_LIMITED",
            retry_after=retry_after,
        )


def client_ip(headers, fallback: str | None, xff_position: str = "last") -> tuple[str, str]:
    """(클라이언트 IP, 출처) 를 돌려줍니다. 출처는 로그로 남겨 어떤 헤더가 쓰였는지 확인하는 용도입니다.

    X-Forwarded-For 는 프록시가 뒤에 붙이므로 기본은 맨 오른쪽 값을 씁니다.
    맨 왼쪽 값은 클라이언트가 보낸 헤더가 그대로 남은 것일 수 있어 위조가 가능합니다.
    """
    for name in _TRUSTED_CLIENT_IP_HEADERS:
        value = headers.get(name)
        if value and value.strip():
            return value.strip(), name

    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if entries:
            chosen = entries[0] if xff_position == "first" else entries[-1]
            return chosen, f"x-forwarded-for[{xff_position}]"

    return (fallback or "unknown"), "socket"


def hash_ip(ip: str) -> str:
    """로그용 식별자. 원본 IP 는 남기지 않고 같은 IP 인지만 알아볼 수 있게 합니다."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:12]


class SlidingWindowRateLimiter:
    """limits=[(최대 건수, 윈도우 초), ...]. 건수가 0 인 윈도우는 끕니다."""

    CLEANUP_EVERY = 1000

    def __init__(self, limits, clock=time.monotonic):
        self._limits = [(int(count), float(window)) for count, window in limits if int(count) > 0]
        self._clock = clock
        self._hits: dict[str, deque] = {}
        self._checks = 0
        self._longest = max((window for _, window in self._limits), default=0.0)

    @property
    def enabled(self) -> bool:
        return bool(self._limits)

    def check(self, key: str) -> int | None:
        """허용되면 None 을 돌려주고 기록합니다. 제한이면 Retry-After 초를 돌려주고 기록하지 않습니다."""
        if not self._limits:
            return None
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._longest:
            hits.popleft()

        retry_after = 0.0
        for count, window in self._limits:
            in_window = [stamp for stamp in hits if stamp > now - window]
            if len(in_window) >= count:
                retry_after = max(retry_after, in_window[0] + window - now)
        if retry_after > 0:
            return max(1, math.ceil(retry_after))

        hits.append(now)
        self._checks += 1
        if self._checks % self.CLEANUP_EVERY == 0:
            self._cleanup(now)
        return None

    def _cleanup(self, now: float) -> None:
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= now - self._longest]
        for key in stale:
            del self._hits[key]

    def tracked_keys(self) -> int:
        return len(self._hits)
