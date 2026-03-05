from fastapi import FastAPI, HTTPException, Response
import sys
import os

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
    # 1. 캐릭터 식별 정보 및 기본 데이터 가져오기
    ocid = await nexon_api.get_ocid(character_name)
    if not ocid:
        return {"error": "캐릭터를 찾을 수 없습니다."}

    basic_info = await nexon_api.get_character_basic(ocid)
    item_data = await nexon_api.get_character_item(ocid)

    if not basic_info or not item_data:
        return {"error": "데이터를 불러오는 데 실패했습니다."}

    char_class = basic_info.get("character_class")
    char_level = int(basic_info.get("character_level", 0))

    # 2. [신규 로직] 1, 2, 3번 프리셋 중 주스탯% 합산이 가장 높은 프리셋 찾기
    # nexon_api.get_best_preset은 scraper.py에 정의된 함수입니다.
    best_preset_idx = nexon_api.get_best_preset(item_data, char_class, char_level)

    # 해당 프리셋 아이템 목록 선택 (비어있을 경우 현재 착용 장비로 폴백)
    items = item_data.get(f"item_equipment_preset_{best_preset_idx}")
    if not items:
        items = item_data.get("item_equipment", [])

    print(f"\n" + "=" * 60)
    print(f" {character_name} 님의 장비 정밀 분석 (최적 프리셋: {best_preset_idx}번)")
    print(f" [LV.{char_level} {char_class}]")
    print("=" * 60)

    report_data = []

    for item in items:
        part = item.get("item_equipment_part")
        name = item.get("item_name")

        # 스타포스 수치 가공
        star = item.get("starforce")
        star_display = f"★ {star} " if star and int(star) > 0 else ""

        # 추가 옵션 계산
        add_opt_raw = item.get("item_add_option", {})
        filtered_add_opt = {k: v for k, v in add_opt_raw.items() if v != 0 and v != '0'}
        add_score = nexon_api.calculate_item_score(add_opt_raw, char_class)

        # 잠재능력 환산 점수 계산
        # (이미 scraper.py에서 무보엠, 모자, 장갑은 -1을 반환하도록 수정되었습니다)
        pot_score = nexon_api.calculate_potential_score(item, "potential", char_class, char_level)
        add_pot_score = nexon_api.calculate_potential_score(item, "additional_potential", char_class, char_level)

        # --- 터미널 출력 로직 ---
        print(f"[{part}] {star_display}{name}")

        # 추가옵션 상세 출력
        if filtered_add_opt:
            print(f"   ㄴ 추가옵션: {add_score}급 ({filtered_add_opt})")
        else:
            print(f"   ㄴ 추가옵션: 없음")

        # 잠재/에디 출력 (무보엠, 모자, 장갑은 p_score가 -1이므로 원본 리스트 출력)
        for p_label, p_score, p_type in [
            ("잠재", pot_score, "potential"),
            ("에디", add_pot_score, "additional_potential")
        ]:
            grade = item.get(f"{p_type}_option_grade")
            if grade:
                if p_score == -1:  # 특수 부위 (무보엠, 모자, 장갑) 원본 출력
                    opts = [item.get(f"{p_type}_option_{i}") for i in range(1, 4)]
                    clean_opts = [o for o in opts if o]
                    print(f"   ㄴ {p_label}({grade}): {clean_opts}")
                else:
                    print(f"   ㄴ {p_label}({grade}): 주스탯 환산 {p_score}%")

        print("-" * 50)

        report_data.append({
            "part": part,
            "name": name,
            "starforce": star,
            "add_score": add_score,
            "pot_score": pot_score,
            "add_pot_score": add_pot_score
        })

    return {
        "character": character_name,
        "best_preset_used": best_preset_idx,
        "report": report_data
    }