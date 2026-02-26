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
nexon_api = NexonAPIHandler()


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
    # 1. ocid 가져오기
    ocid = await nexon_api.get_ocid(nickname)
    if not ocid or isinstance(ocid, dict):
        raise HTTPException(status_code=404, detail="캐릭터 식별자를 찾을 수 없습니다.")

    # 2. 기본 정보와 스탯 가져오기
    basic_info = await nexon_api.get_character_basic(ocid)
    stat_info = await nexon_api.get_character_stat(ocid)

    if not basic_info or not stat_info:
        raise HTTPException(status_code=400, detail="데이터를 불러오는 데 실패했습니다.")

    # 3. 필요한 데이터만 쏙쏙 뽑기 (가공)
    # 전투력은 final_stat 리스트 안에 있으므로 찾아야 합니다.
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


card_gen = CardGenerator()


@app.get("/generate-card/{nickname}")
async def generate_card(nickname: str):
    # 1. 데이터 가져오기 (기존 로직 재사용)
    # 실제로는 위에서 만든 get_card_data 내부 로직을 함수화하여 호출하는 것이 좋습니다.
    data = await get_card_data(nickname)

    # 2. 이미지 생성
    card_img_stream = await card_gen.create_card(data)

    # 3. 이미지 반환
    return Response(content=card_img_stream.getvalue(), media_type="image/png")