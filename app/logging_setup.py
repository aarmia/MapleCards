"""JSON 한 줄 로그 설정. Render 로그 탐색기에서 필드 단위로 검색할 수 있게 합니다."""

import json
import logging
import sys
from datetime import datetime, timezone

# LogRecord 기본 속성. 이 밖의 속성은 logger.info(..., extra={...}) 로 넘어온 구조화 필드입니다.
_STANDARD_ATTRS = set(vars(logging.LogRecord("x", logging.INFO, "x", 0, "", (), None))) | {
    "message",
    "asctime",
    "taskName",
}


def _extra_fields(record: logging.LogRecord) -> dict:
    return {key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS and not key.startswith("_")}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "pid": record.process,
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """로컬 개발용. 기본 포맷 뒤에 구조화 필드를 key=value 로 붙입니다."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = _extra_fields(record)
        if extras:
            line += " " + " ".join(f"{key}={json.dumps(value, ensure_ascii=False, default=str)}" for key, value in extras.items())
        return line


def configure_logging(log_format: str = "json", level: str = "INFO", stream=None) -> logging.Handler:
    """루트 로거를 핸들러 하나로 통일합니다. uvicorn 로거는 루트로 전파시키고 액세스 로그는 끕니다.

    액세스 로그는 진행 상태 폴링(탭당 초당 약 3회)까지 전부 찍어 소음만 커서 끄고,
    대신 /check-items 가 진단 1건당 'diagnosis' 이벤트 한 줄을 남깁니다.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream or sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)

    for name in ("uvicorn", "uvicorn.error", "gunicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    for name in ("uvicorn.access", "gunicorn.access"):
        access_logger = logging.getLogger(name)
        access_logger.handlers.clear()
        access_logger.propagate = False
        access_logger.disabled = True
    # httpx 는 요청마다 INFO 로 URL 전체(캐릭터 이름 포함)를 찍습니다. 진단 1건당 4줄의 소음이자 이름 노출이라 WARNING 부터만 남깁니다.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
