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

    print(f"\n" + "=" * 65)
    print(f" {character_name} 님의 장비 정밀 분석 (최적 프리셋: {best_preset_idx}번)")
    print(f" [LV.{char_level} {char_class}]")
    print("=" * 65)

    report_data = []
    evaluate_list = []

    # --- [추옵 점수 산출 함수] ---
    def get_advanced_add_score(actual_급수, level, part_name):
        no_add_slots = ["반지", "어깨장식", "기계 심장", "훈장", "뱃지", "포켓 아이템", "엠블렘", "보조무기", "무기"]
        if any(k in part_name for k in no_add_slots):
            return 100.0

        target = 0
        if level >= 250:
            target = 186
        elif level >= 200:
            target = 162
        elif level >= 160:
            target = 144
        elif level >= 150:
            target = 132
        elif level >= 140:
            target = 126

        if target == 0: return 100.0

        diff_percent = ((actual_급수 - target) / target) * 100.0
        score = 100.0

        if diff_percent > 0:
            rem = diff_percent
            mult = 0.5
            while rem > 0:
                chunk = min(10.0, rem)
                score += chunk * mult
                rem -= chunk
                mult *= 0.9
        elif diff_percent < 0:
            rem = abs(diff_percent)
            mult = 2.0
            while rem > 0:
                chunk = min(10.0, rem)
                score -= chunk * mult
                rem -= chunk
                mult *= 1.1
        return round(score, 2)

    # 3. 장비 순회 및 분석
    for item in items:
        part = item.get("item_equipment_part")
        name = item.get("item_name")
        star = int(item.get("starforce", 0))
        star_display = f"★{star} " if star > 0 else ""

        base_opt = item.get("item_base_option", {})
        item_req_level = int(base_opt.get("base_equipment_level", 0))

        actual_add_급수 = nexon_api.calculate_item_score(item.get("item_add_option", {}), char_class)
        pot_score = nexon_api.calculate_potential_score(item, "potential", char_class, char_level)
        add_pot_score = nexon_api.calculate_potential_score(item, "additional_potential", char_class, char_level)

        # 각 부문별 점수 계산
        adv_add_score = get_advanced_add_score(actual_add_급수, item_req_level, part)

        adv_pot_score = 0.0
        if pot_score > 0:
            adv_pot_score = pot_score * 3.3

        adv_add_pot_score = 0.0
        if add_pot_score > 0:
            adv_add_pot_score = add_pot_score * 2.5

        adv_star_score = 0.0
        if item_req_level > 0:
            # 레벨 가중치 완화: (Level / 200) 대신 보정식 사용
            # 140제: 0.9 / 200제: 1.0 / 250제: 1.083 정도로 편차 축소
            level_weight = 1.0 + (item_req_level - 200) / 600.0

            # 22성까지의 베이스 점수
            base22 = 100.0 * ((min(star, 22) / 22.0) ** 2) * level_weight

            if star <= 22:
                adv_star_score = base22
            else:
                # 23성 이상 로그 상승분 (레벨 가중치 미포함)
                # 상수 5.0 -> 3.0으로 하향 조정 (로그 상승분 축소)
                adv_star_score = base22 + (5.0 * math.log(star - 21))

        total_item_score = adv_add_score + adv_pot_score + adv_add_pot_score + adv_star_score

        # --- [수정] 하위 3개 선별 리스트 필터링 강화 ---
        exclude_parts = ["훈장", "뱃지", "포켓 아이템", "칭호"]
        # 특수 스킬 반지(시드링) 키워드
        special_skill_rings = ["리스트레인트", "컨티뉴어스", "웨폰퍼프"]

        # 제외 조건 판별
        is_exclude_part = any(k in part for k in exclude_parts)
        is_special_ring = any(k in name for k in special_skill_rings)

        # 잠재 환산이 불가능한 부위(무보엠, 모자, 장갑), 제외 부위, 시드링은 랭킹에서 제외
        if pot_score != -1 and not is_exclude_part and not is_special_ring:
            evaluate_list.append({
                "part": part,
                "name": f"{star_display}{name}",
                "total_score": round(total_item_score, 2),
                "detail": f"[추옵:{adv_add_score:.1f} | 잠재:{adv_pot_score:.1f} | 에디:{adv_add_pot_score:.1f} | 별:{adv_star_score:.1f}]"
            })

        # 터미널 상세 출력
        print(f"[{part}] (Lv.{item_req_level}) {star_display}{name}")
        filtered_add_opt = {k: v for k, v in item.get("item_add_option", {}).items() if v != 0 and v != '0'}
        if filtered_add_opt:
            print(f"   ㄴ 추가옵션: {actual_add_급수}급 ({filtered_add_opt})")

        for p_label, p_score, p_type in [("잠재", pot_score, "potential"), ("에디", add_pot_score, "additional_potential")]:
            grade = item.get(f"{p_type}_option_grade")
            if grade:
                if p_score == -1:
                    opts = [item.get(f"{p_type}_option_{i}") for i in range(1, 4)]
                    print(f"   ㄴ {p_label}({grade}): {[o for o in opts if o]}")
                else:
                    print(f"   ㄴ {p_label}({grade}): 주스탯 환산 {p_score}%")
        print("-" * 65)

    # 하위 3개 출력
    sorted_items = sorted(evaluate_list, key=lambda x: x["total_score"])
    bottom_3 = sorted_items[:5]

    print("\n" + "🚨 [스펙업 권장] 교체 1순위 하위 장비 TOP 5 🚨")
    for i, b_item in enumerate(bottom_3, 1):
        print(f" {i}위: [{b_item['part']}] {b_item['name']}")
        print(f"      총점: {b_item['total_score']}점 {b_item['detail']}")
    print("=" * 65 + "\n")

    return {
        "character": character_name,
        "best_preset": best_preset_idx,
        "bottom_3": bottom_3
    }