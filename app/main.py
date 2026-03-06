from fastapi import FastAPI, HTTPException, Response
import sys
import os
import math

# 현재 파일의 부모 폴더(app)를 경로에 추가하여 scraper를 찾을 수 있게 함
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from scraper import NexonAPIHandler
except ImportError:
    from app.scraper import NexonAPIHandler

from app.image_gen import CardGenerator

app = FastAPI()
# 기존에 정의하신 nexon_api 변수명을 일관되게 사용합니다.
nexon_api = NexonAPIHandler()
card_gen = CardGenerator()

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
    # 1. 기초 데이터 로드
    ocid = await nexon_api.get_ocid(character_name)
    if not ocid:
        return {"error": "캐릭터를 찾을 수 없습니다."}

    basic_info = await nexon_api.get_character_basic(ocid)
    item_data = await nexon_api.get_character_item(ocid)

    if not basic_info or not item_data:
        return {"error": "데이터를 불러오는 데 실패했습니다."}

    char_class = basic_info.get("character_class")
    char_level = int(basic_info.get("character_level", 0))

    # 최적의 프리셋 찾기 (장착 중인 장비 우선)
    best_preset_idx = nexon_api.get_best_preset(item_data, char_class, char_level)
    items = item_data.get(f"item_equipment_preset_{best_preset_idx}")
    if not items:
        items = item_data.get("item_equipment", [])

    evaluate_list = []

    # --- [헬퍼 함수 1: 추옵 점수 산출 (계단식 감점 로직)] ---
    def get_advanced_add_score(actual_급수, level, part_name):
        no_add_slots = ["반지", "어깨장식", "기계 심장", "훈장", "뱃지", "포켓 아이템", "엠블렘", "보조무기", "무기"]
        if any(k in part_name for k in no_add_slots):
            return 100.0  # 추옵이 없는 부위는 기본점수 부여

        # 레벨별 타겟 급수 설정
        target = {250: 186, 200: 162, 160: 144, 150: 132, 140: 126}.get(level, 0)
        if target == 0: return 100.0

        diff_percent = ((actual_급수 - target) / target) * 100.0
        score = 100.0
        rem = abs(diff_percent)
        mult = 0.5 if diff_percent > 0 else 2.0  # 상향은 어렵게, 하향은 매섭게

        while rem > 0:
            chunk = min(10.0, rem)
            score += (chunk * mult) if diff_percent > 0 else -(chunk * mult)
            rem -= chunk
            mult *= (0.9 if diff_percent > 0 else 1.1)
        return round(score, 2)

    # --- [헬퍼 함수 2: 동적 가이드 생성 (상대적 약점 분석)] ---
    def get_dynamic_guide(scores, star_val, part_name, total_score):
        # scores = [추옵, 잠재, 에디, 스타포스]
        labels = ["추가옵션", "윗잠재", "에디셔널", "스타포스"]

        # 1. 부위별 평가 대상 확정
        eval_indices = [1, 2]  # 잠재, 에디는 공통
        if not any(k in part_name for k in ["반지", "어깨장식", "기계 심장"]):
            eval_indices.append(0)  # 추옵 포함
        if not any(k in part_name for k in ["보조무기", "엠블렘"]):
            eval_indices.append(3)  # 스타포스 포함

        # 2. 달성률 계산 (준종결급 기준점)
        # 추옵 180급 / 주스탯 30% / 에디 2줄(20%) / 22성 점수를 100%로 상정
        max_bench = [135, 99, 55, 110]
        ratios = [scores[i] / max_bench[i] for i in range(4)]

        valid_ratios = [ratios[i] for i in eval_indices]
        avg_ratio = sum(valid_ratios) / len(valid_ratios)

        # 상대적으로 가장 처지는 항목 찾기
        min_ratio = min(valid_ratios)
        worst_idx = eval_indices[valid_ratios.index(min_ratio)]
        worst_label = labels[worst_idx]

        # 3. 체급별 솔루션 도출
        # A. 엔드급 (Total 360점 이상)
        if total_score >= 360:
            if min_ratio > 0.95:
                return "✨ [종결] 완벽한 장비입니다. 추가 투자가 불필요합니다."
            return f"🔍 [미세조정] 엔드급이나, 상대적으로 '{worst_label}'이(가) 살짝 아쉽습니다."

        # B. 미드급 (Total 260 ~ 360점)
        elif total_score >= 260:
            # 에디(2)나 고강화(3)가 평균 이상이면 '강화' 추천 (매몰비용 보호)
            is_high_value = (ratios[2] > avg_ratio or (star_val >= 22 and ratios[3] > avg_ratio))
            if is_high_value:
                return f"🛠️ [강화 권장] 에디/스타포스 베이스가 훌륭합니다. '{worst_label}'만 보완해 보세요."
            return f"♻️ [교체 고려] 준수하지만, 직접 '{worst_label}'을(를) 띄우기보다 완성품 교체가 효율적입니다."

        # C. 입문급 (Total 260점 미만)
        else:
            if star_val >= 22:
                return "🛠️ [강화 권장] 22성 베이스가 아깝습니다. 잠재/추옵만 적절히 돌려 살려 쓰세요."
            return "🚨 [교체 권장] 전체적인 체급이 낮습니다. 상위 아이템이나 완성품으로 교체를 추천합니다."

    # 2. 장비 순회 분석
    for item in items:
        part = item.get("item_equipment_part")
        name = item.get("item_name")
        star = int(item.get("starforce", 0))
        item_req_level = int(item.get("item_base_option", {}).get("base_equipment_level", 0))

        # [점수 계산 1] 스타포스: 롤백 버전 (100점 기준 / 분모 600)
        adv_star_score = 0.0
        if item_req_level > 0:
            level_weight = 1.0 + (item_req_level - 200) / 600.0
            base22 = 100.0 * ((min(star, 22) / 22.0) ** 2) * level_weight
            adv_star_score = base22 if star <= 22 else base22 + (3.0 * math.log(star - 21))

        # [점수 계산 2] 잠재/에디: 250제 보너스 삭제 버전
        pot_val = nexon_api.calculate_potential_score(item, "potential", char_class, char_level)
        pot_score = (pot_val * 3.3) if pot_val > 0 else 0

        eddy_val = nexon_api.calculate_potential_score(item, "additional_potential", char_class, char_level)
        eddy_score = (eddy_val * 2.5) if eddy_val > 0 else 0

        # [점수 계산 3] 추가옵션
        actual_add_급수 = nexon_api.calculate_item_score(item.get("item_add_option", {}), char_class)
        add_score = get_advanced_add_score(actual_add_급수, item_req_level, part)

        total_item_score = add_score + pot_score + eddy_score + adv_star_score

        # [필터링] 분석 제외 대상 (시드링 및 특수 부위)
        special_rings = ["리스트레인트", "컨티뉴어스", "웨폰퍼프"]
        exclude_parts = ["훈장", "뱃지", "포켓 아이템", "칭호"]

        if pot_val != -1 and not any(k in part for k in exclude_parts) and not any(k in name for k in special_rings):
            guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part,
                                           total_item_score)
            evaluate_list.append({
                "part": part,
                "name": name,
                "star": star,
                "total_score": round(total_item_score, 2),
                "guide": guide_text,
                "detail": f"추옵:{add_score:.1f} / 별:{adv_star_score:.1f} / 잠재:{pot_score:.1f} / 에디:{eddy_score:.1f}"
            })

    # 3. 결과 정렬 및 반환 (하위 5개)
    bottom_5 = sorted(evaluate_list, key=lambda x: x["total_score"])[:5]

    return {
        "character": character_name,
        "class": char_class,
        "level": char_level,
        "best_preset": best_preset_idx,
        "results": bottom_5
    }