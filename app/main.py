import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Response, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, PlainTextResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from app.analyzer import evaluate_equipment, generate_overall_review, get_best_preset
from app.image_gen import CardGenerator
from app.request_tracker import DiagnosticRequestTracker
from app.scraper import NexonAPIError, NexonAPIHandler

nexon_api = NexonAPIHandler()
card_gen = CardGenerator()


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


@app.exception_handler(NexonAPIError)
async def nexon_api_exception_handler(_: Request, exc: NexonAPIError):
    headers = {"Retry-After": "5"} if exc.status_code == 429 else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message},
        headers=headers,
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
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
    return "User-agent: *\nAllow: /\nSitemap: https://meculator.onrender.com/sitemap.xml"


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
            raise NexonAPIError("캐릭터 장비 데이터가 비어 있습니다.", status_code=502)

        await request_tracker.set_stage(tracked_request_id, "analysis")

        char_class = basic_info.get("character_class")
        char_level = int(basic_info.get("character_level", 0))
        char_image = basic_info.get("character_image", "")

        combat_power = 0
        if stat_data and "final_stat" in stat_data:
            for stat in stat_data["final_stat"]:
                if stat.get("stat_name") == "전투력":
                    combat_power = stat.get("stat_value")
                    break

        # 분리된 분석 로직(analyzer) 호출
        best_preset_idx = get_best_preset(item_data, char_class, char_level)
        items = item_data.get(f"item_equipment_preset_{best_preset_idx}")
        if not items:
            items = item_data.get("item_equipment", [])

        evaluate_list = evaluate_equipment(items, char_class, char_level)
        overall_review, all_sorted_results = generate_overall_review(evaluate_list)

        return {
            "character": character_name,
            "class": char_class,
            "level": char_level,
            "character_image": char_image,
            "combat_power": combat_power,
            "best_preset": best_preset_idx,
            "overall": overall_review,
            "results": all_sorted_results
        }
