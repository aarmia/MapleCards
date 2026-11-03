import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analyzer import build_report
from app.errors import AppError
from app.request_tracker import DiagnosticRequestTracker
from app.scraper import NexonAPIError, NexonAPIHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

nexon_api = NexonAPIHandler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await nexon_api.start()
    try:
        yield
    finally:
        await nexon_api.close()


app = FastAPI(lifespan=lifespan)

# 동시 진단 5건 제한 및 요청별 진행 상태 추적
request_tracker = DiagnosticRequestTracker(concurrency=5)

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


@app.get("/check-items/{character_name}")
async def check_items(
    character_name: str,
    request_id: Annotated[str | None, Query()] = None,
):
    async with request_tracker.track(request_id) as tracked_request_id:
        ocid = await nexon_api.get_ocid(character_name)
        await request_tracker.set_stage(tracked_request_id, "equipment")

        basic_info, item_data, stat_data = await asyncio.gather(
            nexon_api.get_character_basic(ocid),
            nexon_api.get_character_item(ocid),
            nexon_api.get_character_stat(ocid),
        )

        if not basic_info or not item_data:
            raise NexonAPIError("캐릭터 장비 데이터가 비어 있습니다.", status_code=502, code="NEXON_EMPTY_DATA")

        await request_tracker.set_stage(tracked_request_id, "analysis")

        # 분석 로직은 analyzer.build_report 에 모여 있어 골든 테스트와 같은 경로를 탑니다.
        return build_report(character_name, basic_info, item_data, stat_data)
