from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import sys
import os
import asyncio
import math

# 현재 파일의 부모 폴더(app)를 경로에 추가
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

# 서버 세마포어 설정 (동시 API 처리 인원을 10명으로 제한)
api_semaphore = asyncio.Semaphore(10)

# 경로 설정
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print(f"⚠️ Warning: Static directory not found at {static_dir}")


# 파비콘 404 에러 방지 핸들러
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/check-items/{character_name}")
async def check_items(character_name: str):
    # 세마포어를 사용하여 넥슨 API 호출 구간 보호
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

        best_preset_idx = nexon_api.get_best_preset(item_data, char_class, char_level)
        items = item_data.get(f"item_equipment_preset_{best_preset_idx}")
        if not items:
            items = item_data.get("item_equipment", [])

        evaluate_list = []

        # --- [헬퍼 함수 1: 추옵 점수 산출 로직] ---
        def get_advanced_add_score(actual_급수, level, part_name):
            no_add_slots = ["반지", "어깨장식", "기계 심장", "훈장", "뱃지", "포켓 아이템", "엠블렘", "보조무기", "무기"]
            if any(k in part_name for k in no_add_slots):
                return 100.0

            target_map = {250: 186, 200: 162, 160: 144, 150: 132, 140: 126, 135: 123, 130: 120}
            target = target_map.get(level)

            if target is None:
                if level >= 100:
                    target = (level * 0.6) + 42
                else:
                    return 100.0

            diff_percent = ((actual_급수 - target) / target) * 100.0
            score = 100.0
            rem = abs(diff_percent)

            if diff_percent > 0:
                mult = 0.5
                decay = 0.9
            else:
                mult = 1.2
                decay = 1.05

            while rem > 0:
                chunk = min(5.0, rem)
                if diff_percent > 0:
                    score += (chunk * mult)
                    mult *= decay
                else:
                    score -= (chunk * mult)
                    mult = min(mult * decay, 3.0)
                rem -= chunk

            return round(max(0, score), 2)

        # --- [헬퍼 함수 2: 공통 스타포스 점수 계산] ---
        def get_starforce_score(star, level):
            if level <= 0: return 0.0
            weight = 1.0 + (level - 200) / 600.0
            base22 = 100.0 * ((min(star, 22) / 22.0) ** 2) * weight
            return base22 if star <= 22 else base22 + (3.0 * math.log(star - 21))

        # --- [헬퍼 함수 3: 세분화된 가이드 생성] ---
        def get_dynamic_guide(scores, star_val, part_name, total_score, item_name, item_req_level):
            target_hearts = ["리튬 하트", "페어리 하트", "플라즈마 하트"]
            black_heart = ["블랙 하트"]
            if any(heart in item_name for heart in target_hearts):
                return "🚨 [교체 권장] 현재 하트는 성능 한계가 명확합니다. 상위 등급의 아이템으로 교체를 고려하세요."
            if any(heart in item_name for heart in black_heart):
                return "🚨 블랙 하트는 점수 환산을 지원하지 않습니다."

            max_star_possible = 30
            if item_req_level < 128:
                max_star_possible = 15
            elif item_req_level < 138:
                max_star_possible = 20

            labels = ["추가옵션", "윗잠재", "에디셔널", "스타포스"]
            max_bench = [110, 108, 40, 105]

            eval_indices = [1, 2]
            # [수정] 보조무기, 엠블렘은 추옵/스타포스가 없으므로 평가 대상에서 완벽히 제외
            if not any(k in part_name for k in ["반지", "어깨장식", "기계 심장", "보조무기", "엠블렘"]):
                eval_indices.append(0)
            if not any(k in part_name for k in ["보조무기", "엠블렘"]):
                eval_indices.append(3)

            ratios = [scores[i] / max_bench[i] for i in range(4)]
            valid_ratios = [ratios[i] for i in eval_indices]
            min_ratio = min(valid_ratios)
            worst_idx = eval_indices[valid_ratios.index(min_ratio)]
            worst_label = labels[worst_idx]

            if total_score >= 350:
                if star_val >= 22 or star_val >= max_star_possible:
                    if scores[2] <= 43:
                        return "✨ [에디/교체 권장] 베이스는 완벽하지만, 에디셔널을 강화하거나 상위 매물로 교체하세요"
                    return f"👑 [종결] 완벽한 장비입니다. {star_val + 1}성 도전 외엔 투자가 무의미합니다."
                return f"🔍 [미세조정] 종결급이나 '{worst_label}'이(가) 평균보다 낮습니다."

            elif total_score >= 300:
                if total_score >= 330:
                    if star_val >= 23:
                        return "✨ [종결 / 상위매물 교체] 23성 이상 도달한 장비입니다. 추가 강화보다는 이대로 사용하시거나, 상위 등급의 베이스 아이템 매물로 교체하는 것을 추천합니다."

                    target_star = 23 if item_req_level <= 160 else 22

                    if star_val < target_star and 3 in eval_indices:
                        return f"⭐ [스타포스 권장] 체급에 걸맞은 '{target_star}성' 달성이 시급합니다. 우선적으로 {star_val + 1}성 강화를 최우선하세요."

                    if worst_label == "추가옵션":
                        return "💎 [준종결] 베이스가 훌륭합니다. 추옵이 1% 아쉬운 상황이니 조금 더 높은 추옵을 노려보세요."
                    elif worst_label == "윗잠재":
                        return "💎 [준종결] 체급은 완성되었습니다. 잠재능력을 조금 더 높은 단계로 끌어올릴 차례입니다."
                    elif worst_label == "에디셔널":
                        return "💎 [준종결] 장비의 뼈대는 완벽합니다. 에디셔널을 더 완벽하게 만드는 것이 종결의 열쇠입니다."
                    else:
                        return f"💎 [준종결] 종결급 체급입니다. 마지막 퍼즐인 '{worst_label}' 수치 보완을 추천합니다."

                if 3 in eval_indices:
                    if star_val >= max_star_possible:
                        return f"✅ [강화 한계] 최대치까지 강화되었습니다. 이제 부족한 '{worst_label}'에 집중하세요."

                    if star_val <= 18:
                        target_star = 21 if max_star_possible >= 21 else max_star_possible
                        return f"⚔️ [단계적 강화] 베이스가 훌륭합니다. 우선 '{target_star}성 안착'을 목표로 강화를 추천합니다."

                    if star_val < 22:
                        if min_ratio < 0.25:
                            return f"🚨 [밸런스 보완] 스타포스보다 급한 것은 '{worst_label}'입니다. 밸런스를 먼저 맞춰주세요."
                        return "📈 [상위 강화] 22성 도전의 가치가 있는 베이스입니다. 22성 강화를 고려하세요."

                return f"🛠️ [강화 권장] 엘리트 장비입니다. 전체적인 밸런스를 위해 '{worst_label}'을 보완하세요."

            elif total_score >= 250:
                if star_val < 17 and 3 in eval_indices:
                    return "📦 [가성비 강화] 최소 17 ~ 18성 달성 후 잠재능력을 손보는 것이 효율적입니다."
                return f"📈 [효율 투자 / 교체] '{worst_label}'부터 차근차근 올리거나, 상위 아이템으로 교체를 추천합니다."
            else:
                return "🚨 [교체 시급] 현재 세팅에서 가장 효율이 떨어지는 부위입니다. 상위 아이템으로 교체를 추천합니다."

        # --- [아이템 개별 평가 루프] ---
        for item in items:
            slot = item.get("item_equipment_slot", "")
            part = item.get("item_equipment_part", "")
            name = item.get("item_name", "")
            icon = item.get("item_icon", "")
            star = int(item.get("starforce", 0))
            item_req_level = int(item.get("item_base_option", {}).get("base_equipment_level", 0))

            # [핵심] 무기류와 방어구를 분리하여 로직 적용
            is_weapon = any(k in slot for k in ["무기", "보조무기", "엠블렘"])

            if is_weapon:
                if any(k in slot for k in ["보조무기", "엠블렘"]):
                    add_score = 100.0  # 보조/엠블렘은 추옵 100점 고정 보정
                    adv_star_score = 100.0  # 스타포스 100점 고정 보정
                else:
                    # 무기 전용 추가옵션 계산 및 350점 척도 스케일링 (* 2.0)
                    weapon_add_val = nexon_api.calculate_weapon_add_option_score(item, char_class)
                    add_score = weapon_add_val * 2.0
                    adv_star_score = get_starforce_score(star, item_req_level)

                # 무기 전용 잠재능력 계산 및 스케일링 (* 3.3, * 2.5)
                weapon_pot_val = nexon_api.calculate_weapon_potential_score(item, "potential", char_class)
                pot_score = (weapon_pot_val * 3.3) if weapon_pot_val > 0 else 0

                weapon_eddy_val = nexon_api.calculate_weapon_potential_score(item, "additional_potential", char_class)
                eddy_score = (weapon_eddy_val * 2.5) if weapon_eddy_val > 0 else 0

                pot_val = weapon_pot_val  # -1 필터링 통과용

            else:
                # 기존 방어구 계산 로직
                actual_add_급수 = nexon_api.calculate_item_score(item.get("item_add_option", {}), char_class)
                add_score = get_advanced_add_score(actual_add_급수, item_req_level, part)
                adv_star_score = get_starforce_score(star, item_req_level)

                pot_val = nexon_api.calculate_potential_score(item, "potential", char_class, char_level)
                pot_score = (pot_val * 3.3) if pot_val > 0 else 0

                eddy_val = nexon_api.calculate_potential_score(item, "additional_potential", char_class, char_level)
                eddy_score = (eddy_val * 2.5) if eddy_val > 0 else 0

            # 총점 합산
            total_item_score = add_score + pot_score + eddy_score + adv_star_score

            special_rings = ["리스트레인트", "컨티뉴어스", "웨폰퍼프"]
            exclude_parts = ["훈장", "뱃지", "포켓 아이템", "칭호"]

            if pot_val != -1 and not any(k in part for k in exclude_parts) and not any(
                    k in name for k in special_rings):
                guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part,
                                               total_item_score, name, item_req_level)

                evaluate_list.append({
                    "is_wse": is_weapon, "part": part, "name": name, "icon": icon, "star": star,
                    "total_score": round(total_item_score, 2),
                    "guide": guide_text,
                    "detail": {
                        "add": round(add_score, 1), "star": round(adv_star_score, 1),
                        "pot": round(pot_score, 1), "pot_additional": round(eddy_score, 1)
                    },
                    "raw_options": {
                        "base": item.get("item_base_option"),
                        "add": item.get("item_add_option"),
                        "etc": item.get("item_etc_option"),
                        "starforce": item.get("item_starforce_option"),
                        "potential_grade": item.get("potential_option_grade"),
                        "potential_options": [
                            item.get("potential_option_1"),
                            item.get("potential_option_2"),
                            item.get("potential_option_3")
                        ],
                        "additional_grade": item.get("additional_potential_option_grade"),
                        "additional_options": [
                            item.get("additional_potential_option_1"),
                            item.get("additional_potential_option_2"),
                            item.get("additional_potential_option_3")
                        ],
                        "exceptional": item.get("item_exceptional_option")
                    }
                })

        all_sorted_results = sorted(evaluate_list, key=lambda x: x["total_score"])
        return {
            "character": character_name, "class": char_class, "level": char_level,
            "character_image": char_image, "combat_power": combat_power,
            "best_preset": best_preset_idx, "results": all_sorted_results
        }