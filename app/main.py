from fastapi import FastAPI, HTTPException
import sys
import os

# 현재 파일의 부모 폴더(app)를 경로에 추가하여 scraper를 찾을 수 있게 함
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from scraper import NexonAPIHandler
except ImportError:
    from app.scraper import NexonAPIHandler

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