from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import sys
import os
import math

# 현재 파일의 부모 폴더(app)를 경로에 추가하여 scraper를 찾을 수 있게 함
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    from scraper import NexonAPIHandler
except ImportError:
    from app.scraper import NexonAPIHandler

from app.image_gen import CardGenerator

app = FastAPI()
nexon_api = NexonAPIHandler()
card_gen = CardGenerator()

# [수정] 템플릿 및 정적 파일 경로를 BASE_DIR(app 폴더) 기준으로 설정
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")

# 정적 파일 마운트 (경로 존재 여부 확인 포함)
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print(f"⚠️ Warning: Static directory not found at {static_dir}")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/get-ocid/{nickname}")
async def read_ocid(nickname: str):
    result = await nexon_api.get_ocid(nickname)
    if result is None:
        raise HTTPException(status_code=404, detail="캐릭터를 찾을 수 없습니다.")
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {"nickname": nickname, "ocid": result}


@app.get("/character-card/{nickname}")
async def get_card_data(nickname: str):
    ocid = await nexon_api.get_ocid(nickname)
    if not ocid or isinstance(ocid, dict):
        raise HTTPException(status_code=404, detail="캐릭터 식별자를 찾을 수 없습니다.")

    basic_info = await nexon_api.get_character_basic(ocid)
    stat_info = await nexon_api.get_character_stat(ocid)

    if not basic_info or not stat_info:
        raise HTTPException(status_code=400, detail="데이터를 불러오는 데 실패했습니다.")

    stats = stat_info.get("final_stat", [])
    combat_power = next((s['stat_value'] for s in stats if s['stat_name'] == '전투력'), "0")

    return {
        "name": basic_info.get("character_name"),
        "world": basic_info.get("world_name"),
        "class": basic_info.get("character_class"),
        "level": basic_info.get("character_level"),
        "image": basic_info.get("character_image"),
        "combat_power": combat_power
    }


@app.get("/generate-card/{nickname}")
async def generate_card(nickname: str):
    data = await get_card_data(nickname)
    card_img_stream = await card_gen.create_card(data)
    return Response(content=card_img_stream.getvalue(), media_type="image/png")


@app.get("/check-items/{character_name}")
async def check_items(character_name: str):
    ocid = await nexon_api.get_ocid(character_name)
    if not ocid:
        return {"error": "캐릭터를 찾을 수 없습니다."}

    basic_info = await nexon_api.get_character_basic(ocid)
    item_data = await nexon_api.get_character_item(ocid)

    if not basic_info or not item_data:
        return {"error": "데이터를 불러오는 데 실패했습니다."}

    char_class = basic_info.get("character_class")
    char_level = int(basic_info.get("character_level", 0))

    best_preset_idx = nexon_api.get_best_preset(item_data, char_class, char_level)
    items = item_data.get(f"item_equipment_preset_{best_preset_idx}")
    if not items:
        items = item_data.get("item_equipment", [])

    evaluate_list = []

    # --- [헬퍼 함수 1: 추옵 점수 산출] ---
    def get_advanced_add_score(actual_급수, level, part_name):
        no_add_slots = ["반지", "어깨장식", "기계 심장", "훈장", "뱃지", "포켓 아이템", "엠블렘", "보조무기", "무기"]
        if any(k in part_name for k in no_add_slots):
            return 100.0

        target = {250: 186, 200: 162, 160: 144, 150: 132, 140: 126}.get(level, 0)
        if target == 0: return 100.0

        diff_percent = ((actual_급수 - target) / target) * 100.0
        score = 100.0
        rem = abs(diff_percent)
        mult = 0.5 if diff_percent > 0 else 2.0

        while rem > 0:
            chunk = min(10.0, rem)
            score += (chunk * mult) if diff_percent > 0 else -(chunk * mult)
            rem -= chunk
            mult *= (0.9 if diff_percent > 0 else 1.1)
        return round(score, 2)

    # --- [헬퍼 함수 2: 세분화된 가이드 생성] ---
    def get_dynamic_guide(scores, star_val, part_name, total_score):
        labels = ["추가옵션", "윗잠재", "에디셔널", "스타포스"]
        max_bench = [135, 99, 55, 110]

        eval_indices = [1, 2]
        if not any(k in part_name for k in ["반지", "어깨장식", "기계 심장"]):
            eval_indices.append(0)
        if not any(k in part_name for k in ["보조무기", "엠블렘"]):
            eval_indices.append(3)

        ratios = [scores[i] / max_bench[i] for i in range(4)]
        valid_ratios = [ratios[i] for i in eval_indices]
        avg_ratio = sum(valid_ratios) / len(valid_ratios)

        min_ratio = min(valid_ratios)
        worst_idx = eval_indices[valid_ratios.index(min_ratio)]
        worst_label = labels[worst_idx]

        if total_score >= 350:
            if star_val >= 22 and scores[0] > 99 and scores[1] > 99:
                if scores[2] <= 38:
                    return "♻️ [에디/교체 권장] 추옵과 윗잠 베이스는 완벽하지만, 밸런스에 비해 에디셔널이 아쉽습니다. 에디 강화 혹은 교체를 고려하세요."
                else:
                    return f"🌌 [종결] 완벽한 장비입니다. {star_val + 1}성 도전 외엔 투자가 무의미합니다."
            if min_ratio > 0.95:
                return f"🌌 [종결] 완벽한 장비입니다. {star_val + 1}성 도전 외엔 투자가 무의미합니다."
            return f"🔍 [미세조정] 종결급이나 '{worst_label}'이(가) 평균보다 낮습니다."
        elif total_score >= 300:
            if star_val >= 22 and scores[0] > 93 and scores[1] > 95:
                if scores[2] <= 25:
                    return "♻️ [에디/교체 권장] 22성에 윗잠/추옵 베이스까지 훌륭하나 에디셔널이 아쉽습니다. 에디 강화나 완성된 매물로 교체를 고려하세요."
                else:
                    return f"⚔️ [한계 돌파/상위 매물 교체] 완벽합니다! 여기서 더 스펙업을 원하신다면 {star_val + 1}성을 도전하거나, 상위 매물로 교체를 추천합니다."
            if total_score >= 325:
                if star_val < 22 and 3 in eval_indices:
                    return f"⚔️ [스타포스 권장] {star_val + 1}성 강화가 가장 시급합니다."
                if min_ratio < avg_ratio:
                    return f"♻️ [교체 권장] 하이급이나 '{worst_label}'이(가) 전체 체급을 깎습니다. 매물 교체를 고려하세요."
                return f"🛠️ [정밀 강화] 밸런스가 좋습니다. 가장 낮은 '{worst_label}'을 보완하여 하이엔드를 노리세요."
            else:
                if star_val < 22 and 3 in eval_indices:
                    return "⚔️ [스타포스 권장] 잠재/추옵은 훌륭합니다. 22성 강화가 가장 시급합니다."
                return f"🛠️ [강화 권장] 체급이 높은 엘리트 장비입니다. 부족한 '{worst_label}'을 보완하세요."
        elif total_score >= 250:
            if ratios[2] >= avg_ratio or ratios[3] >= avg_ratio:
                return f"📈 [효율 강화] 에디/별 베이스가 좋아 살려 쓸 가치가 있습니다. '{worst_label}'을 보완하세요."
            return f"♻️ [교체 권장] 특출난 장점이 없습니다. 직접 강화보다 완제품 구매가 경제적입니다."
        else:
            if star_val >= 22:
                return f"🛠️ [강화/ 교체 권장] {star_val}성 수치가 아깝습니다! 나머지 옵션을 돌려 가성비 있게 쓰거나, 교체를 추천합니다."
            return "🚨 [교체 시급] 현재 세팅에서 가장 취약한 부위입니다. 상위 아이템으로 교체를 추천합니다."

    for item in items:
        part = item.get("item_equipment_part")
        name = item.get("item_name")
        icon = item.get("item_icon")
        star = int(item.get("starforce", 0))
        item_req_level = int(item.get("item_base_option", {}).get("base_equipment_level", 0))

        adv_star_score = 0.0
        if item_req_level > 0:
            level_weight = 1.0 + (item_req_level - 200) / 600.0
            base22 = 100.0 * ((min(star, 22) / 22.0) ** 2) * level_weight
            adv_star_score = base22 if star <= 22 else base22 + (3.0 * math.log(star - 21))

        pot_val = nexon_api.calculate_potential_score(item, "potential", char_class, char_level)
        pot_score = (pot_val * 3.3) if pot_val > 0 else 0
        eddy_val = nexon_api.calculate_potential_score(item, "additional_potential", char_class, char_level)
        eddy_score = (eddy_val * 2.5) if eddy_val > 0 else 0

        actual_add_급수 = nexon_api.calculate_item_score(item.get("item_add_option", {}), char_class)
        add_score = get_advanced_add_score(actual_add_급수, item_req_level, part)

        total_item_score = add_score + pot_score + eddy_score + adv_star_score

        special_rings = ["리스트레인트", "컨티뉴어스", "웨폰퍼프"]
        exclude_parts = ["훈장", "뱃지", "포켓 아이템", "칭호"]

        if pot_val != -1 and not any(k in part for k in exclude_parts) and not any(k in name for k in special_rings):
            guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part,
                                           total_item_score)
            evaluate_list.append({
                "part": part,
                "name": name,
                "icon": icon,
                "star": star,
                "total_score": round(total_item_score, 2),
                "guide": guide_text,
                "detail": {
                    "add": round(add_score, 1),
                    "star": round(adv_star_score, 1),
                    "pot": round(pot_score, 1),
                    "pot_additional": round(eddy_score, 1)
                }
            })

    all_sorted_results = sorted(evaluate_list, key=lambda x: x["total_score"])

    return {
        "character": character_name,
        "class": char_class,
        "level": char_level,
        "best_preset": best_preset_idx,
        "results": all_sorted_results
    }