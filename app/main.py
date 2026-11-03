import asyncio
import hashlib
import json
import logging
import os
import time
from collections import Counter
from contextlib import asynccontextmanager, suppress
from typing import Annotated

import httpx
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import ClientDisconnect

from app.analyzer import build_report, unsupported_registry
from app.errors import AppError
from app.logging_setup import configure_logging
from app.rate_limit import RateLimitedError, SlidingWindowRateLimiter, client_ip, hash_ip
from app.request_tracker import DiagnosticRequestTracker
from app.scraper import NexonAPIError, NexonAPIHandler
from app.settings import Settings

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

logger = logging.getLogger(__name__)

settings = Settings.from_env()

nexon_api = NexonAPIHandler(
    timeout=httpx.Timeout(
        connect=settings.nexon_connect_timeout,
        read=settings.nexon_read_timeout,
        write=settings.nexon_read_timeout,
        pool=5.0,
    )
)


async def unsupported_summary_loop(interval_seconds: float):
    """미지원 옵션 집계를 주기적으로 한 줄로 남깁니다. 처음 본 옵션은 analyzer 가 그 자리에서 WARNING 으로 알립니다."""
    while True:
        await asyncio.sleep(interval_seconds)
        summary = unsupported_registry.summary(top=20)
        if summary["total"]:
            logger.info("unsupported_options_summary", extra=summary)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging(settings.log_format, settings.log_level)
    logger.info(
        "startup",
        extra={
            "concurrency": settings.diagnosis_concurrency,
            "queue_max_waiting": settings.queue_max_waiting,
            "queue_wait_timeout": settings.queue_wait_timeout,
        },
    )
    await nexon_api.start()
    summary_task = asyncio.create_task(unsupported_summary_loop(settings.unsupported_summary_interval))
    try:
        yield
    finally:
        summary_task.cancel()
        with suppress(asyncio.CancelledError):
            await summary_task
        await nexon_api.close()


app = FastAPI(lifespan=lifespan)

# 동시 진단 수 제한, 대기열 상한·타임아웃, 요청별 진행 상태 추적. 워커 프로세스 1개를 전제로 메모리에만 둡니다.
request_tracker = DiagnosticRequestTracker(
    concurrency=settings.diagnosis_concurrency,
    max_waiting=settings.queue_max_waiting,
    wait_timeout=settings.queue_wait_timeout,
)

# IP 당 진단 요청 제한. /check-items 에만 적용하고 진행 상태 폴링과 정적 파일은 세지 않습니다.
rate_limiter = SlidingWindowRateLimiter(
    [(settings.rate_limit_per_10min, 600.0), (settings.rate_limit_per_hour, 3600.0)],
)

# 경로 설정
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")
favicon_path = os.path.join(static_dir, "images", "favicon.ico")

# robots.txt / sitemap.xml에 쓰는 공개 주소. 배포 환경에서는 SITE_URL 환경 변수로 덮어쓸 수 있습니다.
SITE_URL = os.getenv("SITE_URL", "https://meculator.onrender.com").rstrip("/")


def _static_asset_version(path: str) -> str:
    """정적 파일 내용이 바뀔 때만 URL 버전을 변경합니다."""
    try:
        with open(path, "rb") as asset:
            return hashlib.sha256(asset.read()).hexdigest()[:12]
    except OSError:
        return "missing"


APP_JS_VERSION = _static_asset_version(os.path.join(static_dir, "js", "app.js"))

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print(f"⚠️ Warning: Static directory not found at {static_dir}")


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    # Nexon 오류, 대기열 초과 등 사용자에게 보여줄 오류는 모두 같은 모양으로 내려갑니다.
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload(), headers=headers)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"app_js_version": APP_JS_VERSION},
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml"


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{SITE_URL}/</loc></url>\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


@app.get("/request-status/{request_id}")
async def request_status(request_id: str):
    return await request_tracker.status(request_id)


async def client_disconnected(request: Request) -> bool:
    """슬롯을 받은 시점에 브라우저가 이미 떠났는지 확인합니다.

    Starlette 는 연결이 끊겨도 핸들러를 취소하지 않으므로, 대기 중 탭을 닫은 요청이
    Nexon API 호출 4건을 그대로 쓰는 것을 여기서 막습니다.
    """
    try:
        # GET 의 빈 본문 메시지를 먼저 소비해야 그 뒤의 disconnect 메시지를 읽을 수 있습니다.
        await request.body()
    except ClientDisconnect:
        return True
    return await request.is_disconnected()


def _elapsed_ms(since: float) -> int:
    return round((time.perf_counter() - since) * 1000)


# 사용자 과실이 아니라 서버 혼잡을 뜻하는 코드. 운영 로그에서 바로 보이도록 WARNING 으로 남깁니다.
_CONGESTION_CODES = {"QUEUE_FULL", "QUEUE_TIMEOUT", "RATE_LIMITED"}


@app.get("/check-items/{character_name}")
async def check_items(
    request: Request,
    character_name: str,
    request_id: Annotated[str | None, Query()] = None,
):
    started = time.perf_counter()
    ip, ip_source = client_ip(request.headers, request.client.host if request.client else None, settings.client_ip_xff_position)
    # 진단 1건당 'diagnosis' 이벤트 한 줄. 캐릭터 이름과 원본 IP 는 남기지 않습니다.
    event: dict = {
        "request_id": request_id,
        "outcome": "crash",
        "http_status": 500,
        "code": "INTERNAL_ERROR",
        "client_ip_hash": hash_ip(ip),
        "ip_source": ip_source,
    }
    try:
        retry_after = rate_limiter.check(ip)
        if retry_after is not None:
            raise RateLimitedError(retry_after)

        async with request_tracker.track(request_id) as tracked_request_id:
            event["queue_wait_ms"] = _elapsed_ms(started)
            if await client_disconnected(request):
                # 떠난 클라이언트에게 보낼 응답은 없습니다. 499 는 로그 구분용 상태입니다.
                event.update(outcome="client_disconnected", http_status=499, code=None)
                return Response(status_code=499)

            nexon_started = time.perf_counter()
            ocid = await nexon_api.get_ocid(character_name)
            await request_tracker.set_stage(tracked_request_id, "equipment")

            basic_info, item_data, stat_data = await asyncio.gather(
                nexon_api.get_character_basic(ocid),
                nexon_api.get_character_item(ocid),
                nexon_api.get_character_stat(ocid),
            )
            event["nexon_ms"] = _elapsed_ms(nexon_started)

            if not basic_info or not item_data:
                raise NexonAPIError("캐릭터 장비 데이터가 비어 있습니다.", status_code=502, code="NEXON_EMPTY_DATA")

            await request_tracker.set_stage(tracked_request_id, "analysis")
            analysis_started = time.perf_counter()
            unsupported: Counter = Counter()
            # 분석 로직은 analyzer.build_report 에 모여 있어 골든 테스트와 같은 경로를 탑니다.
            report = build_report(character_name, basic_info, item_data, stat_data, unsupported=unsupported)
            # dict 를 그대로 돌려주면 FastAPI 가 jsonable_encoder 로 한 번 더 훑습니다(1건당 약 3ms). 직접 직렬화합니다.
            # FastAPI JSONResponse 와 같은 옵션(공백 없는 구분자, NaN 금지)으로 바이트 단위까지 동일하게 유지합니다.
            body = json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            event.update(
                outcome="ok",
                http_status=200,
                code=None,
                analysis_ms=_elapsed_ms(analysis_started),
                character_class=report["class"],
                character_level=report["level"],
                item_count=len(report["results"]),
                response_bytes=len(body),
                unsupported_count=sum(unsupported.values()),
                unsupported_top=[
                    {"type": potential_type, "option": option, "count": count}
                    for (potential_type, option), count in unsupported.most_common(5)
                ],
            )
            return Response(content=body, media_type="application/json")
    except AppError as exc:
        event.update(outcome="error", http_status=exc.status_code, code=exc.code)
        raise
    finally:
        event["total_ms"] = _elapsed_ms(started)
        event.update(request_tracker.snapshot())
        congested = event["code"] in _CONGESTION_CODES
        level = logging.WARNING if congested or event["http_status"] >= 500 else logging.INFO
        logger.log(level, "diagnosis", extra=event)
