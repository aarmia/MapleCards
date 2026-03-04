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
    ocid = await nexon_api.get_ocid(character_name)
    basic_info = await nexon_api.get_character_basic(ocid)
    item_data = await nexon_api.get_character_item(ocid)

    if not item_data or "item_equipment" not in item_data:
        return {"error": "장비 데이터를 가져오지 못했습니다."}

    char_class = basic_info.get("character_class")
    items = item_data.get("item_equipment", [])

    report = []

    for item in items:
        part = item.get("item_equipment_part")
        name = item.get("item_name")
        add_opt_raw = item.get("item_add_option", {})

        # === [핵심 수정] 값이 0이 아닌 항목만 골라내어 새로운 딕셔너리 생성 ===
        # 수치가 숫자 0이거나 문자열 '0'이 아닌 것만 필터링합니다.
        filtered_add_opt = {
            k: v for k, v in add_opt_raw.items()
            if v != 0 and v != '0'
        }

        # 추옵급 점수 계산 (원본 데이터 사용)
        score = nexon_api.calculate_item_score(add_opt_raw, char_class)

        report.append({
            "part": part,
            "name": name,
            "score": score,
            "add_option": filtered_add_opt  # 필터링된 데이터 저장
        })

    # 터미널 출력
    print(f"\n=== {character_name} 님의 장비 상세 현황 (총 {len(report)}개) ===")
    for r in report:
        if r['add_option']:  # 필터링 후 데이터가 남아있는 경우만 출력
            print(f"[{r['part']}] {r['name']} : {r['score']}급")
            print(f"   ㄴ 상세추옵: {r['add_option']}")
        else:
            print(f"[{r['part']}] {r['name']} : 추가옵션 없음")

    print("==========================================\n")

    return {
        "character": character_name,
        "class": char_class,
        "report": report
    }