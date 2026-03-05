import httpx
import os
import re
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

    async def get_character_item(self, ocid: str):
        """캐릭터의 장비 아이템 정보를 가져옵니다."""
        url = f"{self.base_url}/character/item-equipment"
        params = {"ocid": ocid}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params)
            return response.json() if response.status_code == 200 else None

    def get_main_stat(self, class_name: str) -> str:
        """직업명을 받아 주스탯 키워드를 반환합니다."""
        # 1. 매핑 데이터 정의
        stat_map = {
            "str": ["히어로", "팔라딘", "다크나이트", "소울마스터", "미하일", "데몬슬레이어", "아란", "카이저", "제로", "블래스터", "렌", "아델", "바이퍼",
                    "스트라이커", "은월", "캐논마스터", "아크"],
            "dex": ["보우마스터", "신궁", "패스파인더", "윈드브레이커", "와일드헌터", "메르세데스", "카인", "캡틴", "메카닉", "엔젤릭버스터"],
            "int": ["아크메이지(불,독)", "아크메이지(썬,콜)", "비숍", "플레임위자드", "에반", "루미너스", "배틀메이지", "일리움", "라라", "키네시스"],
            "luk": ["나이트로드", "섀도어", "듀얼블레이드", "나이트워커", "팬텀", "카데나", "칼리", "호영"]
        }

        # 공백 제거 (API 데이터와 매칭 확률 높임)
        target_class = class_name.replace(" ", "")

        # 2. 특수 직업 판정
        if target_class == "데몬어벤져":
            return "hp"
        if target_class == "제논":
            return "all_stat"

        # 3. 일반 직업 판정 (반복문으로 검색)
        for stat, classes in stat_map.items():
            if target_class in classes:
                return stat

        return "str"  # 기본값 (혹은 에러 처리)

    def calculate_item_score(self, add_option: dict, class_name: str) -> int:
        """직업별 특성을 고려하여 최종 추옵 점수를 계산합니다."""
        main_stat = self.get_main_stat(class_name)

        # 기본 수치들
        all_stat_pct = int(add_option.get("all_stat", 0))
        attack_pwr = int(add_option.get("attack_power", 0))
        magic_pwr = int(add_option.get("magic_power", 0))

        # [케이스 1] 데몬어벤져 (HP)
        if main_stat == "hp":
            hp_val = int(add_option.get("max_hp", 0))
            # 보통 HP 100 = 주스탯 1로 계산
            return (hp_val // 100) + (all_stat_pct * 10) + (attack_pwr * 4)

        # [케이스 2] 제논 (All Stat)
        if main_stat == "all_stat":
            # 제논은 모든 스탯 합산 + 올스탯% 비중이 높음
            s_val = int(add_option.get("str", 0))
            d_val = int(add_option.get("dex", 0))
            l_val = int(add_option.get("luk", 0))
            return (s_val + d_val + l_val) + (all_stat_pct * 20) + (attack_pwr * 4)

        # [케이스 3] 마법사 (Magic Power 기준)
        if main_stat == "int":
            int_val = int(add_option.get("int", 0))
            return int_val + (all_stat_pct * 10) + (magic_pwr * 4)

        # [케이스 4] 일반 직업 (STR, DEX, LUK)
        stat_val = int(add_option.get(main_stat, 0))
        return stat_val + (all_stat_pct * 10) + (attack_pwr * 4)

    def calculate_potential_score(self, item_data: dict, potential_type: str, class_name: str,
                                  char_level: int) -> float:
        slot = item_data.get("item_equipment_slot", "")
        part = item_data.get("item_equipment_part", "")

        # 무보엠 판정 (슬롯 명칭 및 부위 명칭 기준)
        exclude_keywords = ["무기", "보조무기", "엠블렘"]

        is_excluded = any(k in slot for k in exclude_keywords) or any(k in part for k in exclude_keywords)

        if is_excluded:
            return -1

        if class_name in ["데몬어벤져", "제논"]:
            return -1

        # 주스탯 결정 (예: LUK)
        main_stat = self.get_main_stat(class_name).upper()

        options = [
            item_data.get(f"{potential_type}_option_1"),
            item_data.get(f"{potential_type}_option_2"),
            item_data.get(f"{potential_type}_option_3")
        ]

        total_converted_pct = 0.0

        for opt in options:
            if not opt: continue

            # 1. 크리티컬 데미지
            if "크리티컬 데미지" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    val = int(val_match.group(1))
                    weight = 3.5 if class_name in ["보우마스터", "신궁", "패스파인더", "윈드브레이커", "와일드헌터", "메르세데스", "카인"] else 4.0
                    total_converted_pct += val * weight

            # 2. 올스탯%
            elif "올스탯" in opt and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    val = int(val_match.group(1))
                    weight = 1.2 if class_name in ["섀도어", "카데나", "듀얼블레이드"] else 1.1
                    total_converted_pct += val * weight

            # 3. 캐릭터 레벨당 주스탯 (예: 캐릭터 기준 9레벨 당 LUK +2)
            elif "레벨" in opt and main_stat in opt:
                # 숫자만 추출 (예: +2 에서 2 추출)
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    val = int(val_match.group(1))
                    # 요청하신 대로 수치 1당 3.5% 적용
                    total_converted_pct += val * 3.5

            # 4. 주스탯% (LUK : +12% 등)
            elif main_stat in opt and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    total_converted_pct += int(val_match.group(1))

            # 5. 공격력/마력 (정수치)
            elif ("공격력" in opt or "마력" in opt) and "%" not in opt:
                atk_key = "마력" if main_stat == "INT" else "공격력"
                if atk_key in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        total_converted_pct += int(val_match.group(1)) * 0.3

            # 6. 주스탯 정수치 (LUK : +10 등)
            elif main_stat in opt and "%" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    total_converted_pct += int(val_match.group(1)) * 0.09

        return round(total_converted_pct, 2)