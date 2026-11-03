"""환경 변수로 조정하는 운영 설정. 값이 없거나 형식이 잘못되면 기본값을 씁니다."""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _read(name, default, cast, minimum):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r, using default %r", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Ignoring out-of-range %s=%r, using default %r", name, raw, default)
        return default
    return value


def _read_choice(name, default, choices):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    by_lower = {choice.lower(): choice for choice in choices}
    chosen = by_lower.get(raw.strip().lower())
    if chosen is None:
        logger.warning("Ignoring invalid %s=%r, using default %r", name, raw, default)
        return default
    return chosen


@dataclass(frozen=True)
class Settings:
    # 동시에 Nexon API 를 호출하는 진단 수. 서비스 단계 키 기준 8 까지는 여유가 있습니다.
    diagnosis_concurrency: int = 5
    # 대기열 상한. 넘치면 503 QUEUE_FULL 로 즉시 돌려보냅니다. 폴링 부하의 상한이기도 합니다.
    queue_max_waiting: int = 40
    # 대기열에서 기다리는 최대 초. 정상 상태에서는 상한 × 서비스 시간 ÷ 동시성 이 이보다 작아 발동하지 않습니다.
    queue_wait_timeout: float = 8.0
    nexon_connect_timeout: float = 3.0
    nexon_read_timeout: float = 6.0
    # json 이면 한 줄 JSON, text 면 사람이 읽는 형식. Render(RENDER 환경 변수 존재)에서는 기본 json.
    log_format: str = "json"
    log_level: str = "INFO"
    # 미지원 옵션 집계 요약(unsupported_options_summary)을 남기는 주기(초).
    unsupported_summary_interval: float = 3600.0

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        default_log_format = "json" if os.getenv("RENDER") else "text"
        return cls(
            diagnosis_concurrency=_read("DIAGNOSIS_CONCURRENCY", defaults.diagnosis_concurrency, int, 1),
            queue_max_waiting=_read("QUEUE_MAX_WAITING", defaults.queue_max_waiting, int, 0),
            queue_wait_timeout=_read("QUEUE_WAIT_TIMEOUT", defaults.queue_wait_timeout, float, 0.5),
            nexon_connect_timeout=_read("NEXON_CONNECT_TIMEOUT", defaults.nexon_connect_timeout, float, 0.5),
            nexon_read_timeout=_read("NEXON_READ_TIMEOUT", defaults.nexon_read_timeout, float, 0.5),
            log_format=_read_choice("LOG_FORMAT", default_log_format, ("json", "text")),
            log_level=_read_choice("LOG_LEVEL", defaults.log_level, ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
            unsupported_summary_interval=_read(
                "UNSUPPORTED_SUMMARY_INTERVAL", defaults.unsupported_summary_interval, float, 10.0
            ),
        )
