from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse
import sys
import os
import asyncio

# 현재 파일의 부모 폴더(app)를 경로에 추가
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    from scraper import NexonAPIHandler
    from analyzer import evaluate_equipment, generate_overall_review, get_best_preset
except ImportError:
    from app.scraper import NexonAPIHandler
    from app.analyzer import evaluate_equipment, generate_overall_review, get_best_preset

from app.image_gen import CardGenerator

app = FastAPI()
nexon_api = NexonAPIHandler()
card_gen = CardGenerator()

# 서버 세마포어 설정 (동시 API 처리 인원을 5명으로 제한)
api_semaphore = asyncio.Semaphore(5)

# 경로 설정
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print(f"⚠️ Warning: Static directory not found at {static_dir}")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nAllow: /\nSitemap: https://meculator.onrender.com/sitemap.xml"


@app.get("/check-items/{character_name}")
async def check_items(character_name: str):
    async with api_semaphore:
        ocid = await nexon_api.get_ocid(character_name)
        if not ocid:
            return {"error": "캐릭터를 찾을 수 없습니다."}

        basic_info = await nexon_api.get_character_basic(ocid)
        item_data = await nexon_api.get_character_item(ocid)
        stat_data = await nexon_api.get_character_stat(ocid)

        if not basic_info or not item_data:
            return {"error": "데이터를 불러오는 데 실패했습니다."}

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