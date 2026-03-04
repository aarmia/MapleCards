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
    # 1. ocid 가져오기
    ocid = await nexon_api.get_ocid(character_name)
    if not ocid:
        return {"error": "캐릭터를 찾을 수 없습니다."}

    # 2. [추가] 캐릭터 기본 정보 가져오기 (직업 확인용)
    basic_info = await nexon_api.get_character_basic(ocid)
    if not basic_info:
        return {"error": "캐릭터 기본 정보를 가져오지 못했습니다."}

    char_class = basic_info.get("character_class")

    # 3. 장비 데이터 가져오기
    item_data = await nexon_api.get_character_item(ocid)

    if item_data and "item_equipment" in item_data:
        items = item_data["item_equipment"]

        if len(items) > 0:
            first_item = items[0]
            add_opt = first_item.get('item_add_option', {})

            # 4. 이제 basic_info에서 가져온 직업명을 넘겨줍니다.
            item_score = nexon_api.calculate_item_score(add_opt, char_class)

            print(f"\n=== {character_name} 님의 장비 점검 ===")
            print(f"직업: {char_class}")
            print(f"첫 번째 장비 명칭: {first_item.get('item_name')}")
            print(f">>> 판정된 추옵급: {item_score}급")
            print("====================================\n")

            return {
                "character": character_name,
                "class": char_class,
                "item_name": first_item.get('item_name'),
                "score": item_score
            }

    return {"error": "데이터를 가져오지 못했습니다."}