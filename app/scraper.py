import httpx
import os
from dotenv import load_dotenv

# 현재 파일 위치 기준으로 .env 로드 시도
load_dotenv()


class NexonAPIHandler:
    def __init__(self):
        self.api_key = os.getenv("NEXON_API_KEY")
        # 디버깅을 위해 서버 시작 시 키 로드 여부 출력 (앞 5자리만)
        if self.api_key:
            print(f"✅ API Key Loaded: {self.api_key[:5]}***")
        else:
            print("❌ API Key Missing! Check your .env file.")

        self.base_url = "https://open.api.nexon.com/maplestory/v1"
        self.headers = {
            "x-nxopen-api-key": self.api_key if self.api_key else ""
        }

    async def get_ocid(self, character_name: str):
        if not self.api_key:
            return {"error": "서버의 API Key 설정이 되어있지 않습니다."}

        url = f"{self.base_url}/id"
        params = {"character_name": character_name}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers, params=params)

                # 500 에러 방지를 위한 예외 처리
                if response.status_code != 200:
                    return {"error": f"Nexon API Error ({response.status_code})", "detail": response.text}

                return response.json().get("ocid")

            except Exception as e:
                print(f"Network Error: {e}")
                return {"error": "네트워크 연결 실패"}

    async def get_character_basic(self, ocid: str):
        """ocid로 캐릭터 기본 정보(이름, 월드, 직업, 레벨, 이미지)를 가져옵니다."""
        url = f"{self.base_url}/character/basic"
        params = {"ocid": ocid}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params)
            return response.json() if response.status_code == 200 else None

    async def get_character_stat(self, ocid: str):
        """ocid로 캐릭터의 상세 스탯(전투력 등)을 가져옵니다."""
        url = f"{self.base_url}/character/stat"
        params = {"ocid": ocid}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params)
            return response.json() if response.status_code == 200 else None