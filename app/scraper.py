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
        self.client = None

    async def _get_client(self):
        # 요청 시점에 클라이언트가 없으면 생성 (싱글톤 패턴)
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(headers=self.headers, timeout=10.0)
        return self.client

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
            "luk": ["나이트로드", "섀도어", "듀얼블레이더", "나이트워커", "팬텀", "카데나", "칼리", "호영"]
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
            return (hp_val // 100) + (all_stat_pct * 10) + (attack_pwr * 4)

        # [케이스 2] 제논 (All Stat)
        if main_stat == "all_stat":
            s_val = int(add_option.get("str", 0))
            d_val = int(add_option.get("dex", 0))
            l_val = int(add_option.get("luk", 0))
            # 수정된 제논 추옵 로직: STR + DEX + LUK + 올스탯% 환산 + (공격력 * 5)
            return s_val + d_val + l_val + (all_stat_pct * 20) + (attack_pwr * 5)

        # [케이스 3] 마법사 (Magic Power 기준)
        if main_stat == "int":
            int_val = int(add_option.get("int", 0))
            return int_val + (all_stat_pct * 10) + (magic_pwr * 3)

        # [케이스 4] 일반 직업 (STR, DEX, LUK)
        stat_val = int(add_option.get(main_stat, 0))
        return stat_val + (all_stat_pct * 10) + (attack_pwr * 4)

    def calculate_potential_score(self, item_data: dict, potential_type: str, class_name: str,
                                  char_level: int) -> float:
        slot = item_data.get("item_equipment_slot", "")
        part = item_data.get("item_equipment_part", "")

        exclude_keywords = ["무기", "보조무기", "엠블렘"]
        is_excluded = any(k in slot for k in exclude_keywords) or any(k in part for k in exclude_keywords)

        if is_excluded:
            return -1

        if class_name in ["데몬어벤져"]:
            return -1

        main_stat = self.get_main_stat(class_name).upper()

        options = [
            item_data.get(f"{potential_type}_option_1"),
            item_data.get(f"{potential_type}_option_2"),
            item_data.get(f"{potential_type}_option_3")
        ]

        total_stat_score = 0.0
        total_special_score = 0.0

        for opt in options:
            if not opt: continue

            # --- [분리] 공통 특수 옵션 (가중치 제외 대상) ---
            if "크리티컬 데미지" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    total_special_score += int(val_match.group(1)) * 1.75
                continue

            if "스킬 재사용 대기시간" in opt:
                val_match = re.search(r'(\d+)초', opt)
                if val_match:
                    total_special_score += int(val_match.group(1)) * 7.25
                continue

            # --- [분리] 스탯 관련 옵션 (제논 가중치 적용 대상) ---
            # 제논(ALL_STAT) 전용 잠재능력 분기
            if main_stat == "ALL_STAT":
                if "올스탯" in opt and "%" in opt:
                    val_match = re.search(r'\+(\d+)%', opt)
                    if val_match:
                        total_stat_score += int(val_match.group(1))
                elif "레벨" in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        val = int(val_match.group(1))
                        if any(stat in opt for stat in ["STR", "DEX", "LUK"]):
                            total_stat_score += val / 3.0
                elif any(stat in opt for stat in ["STR", "DEX", "LUK"]) and "%" in opt:
                    val_match = re.search(r'\+(\d+)%', opt)
                    if val_match:
                        total_stat_score += int(val_match.group(1)) / 3.0
                elif "공격력" in opt and "%" not in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        total_stat_score += int(val_match.group(1)) * 0.3
                elif any(stat in opt for stat in ["STR", "DEX", "LUK"]) and "%" not in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        total_stat_score += (int(val_match.group(1)) / 3.0) * 0.09

            # 일반 직업 잠재능력 분기
            else:
                if "올스탯" in opt and "%" in opt:
                    val_match = re.search(r'\+(\d+)%', opt)
                    if val_match:
                        val = int(val_match.group(1))
                        weight = 1.2 if class_name in ["섀도어", "카데나", "듀얼블레이더"] else 1.1
                        total_stat_score += val * weight
                elif "레벨" in opt and main_stat in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        val = int(val_match.group(1))
                        total_stat_score += val * 3.5
                elif main_stat in opt and "%" in opt:
                    val_match = re.search(r'\+(\d+)%', opt)
                    if val_match:
                        total_stat_score += int(val_match.group(1))
                elif ("공격력" in opt or "마력" in opt) and "%" not in opt:
                    atk_key = "마력" if main_stat == "INT" else "공격력"
                    if atk_key in opt:
                        val_match = re.search(r'\+(\d+)', opt)
                        if val_match:
                            total_stat_score += int(val_match.group(1)) * 0.3
                elif main_stat in opt and "%" not in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        total_stat_score += int(val_match.group(1)) * 0.09

        # 제논일 경우 스탯 관련 점수에만 1.35배 적용
        if main_stat == "ALL_STAT":
            total_stat_score *= 1.35

        return round(total_stat_score + total_special_score, 2)

    def get_best_preset(self, item_data: dict, class_name: str, char_level: int) -> int:
        preset_scores = {1: 0.0, 2: 0.0, 3: 0.0}

        for i in range(1, 4):
            preset_items = item_data.get(f"item_equipment_preset_{i}", [])
            if not preset_items:
                continue

            total_score = 0.0
            for item in preset_items:
                score = self.calculate_potential_score(item, "potential", class_name, char_level)
                add_score = self.calculate_potential_score(item, "additional_potential", class_name, char_level)

                if score > 0: total_score += score
                if add_score > 0: total_score += add_score

            preset_scores[i] = total_score

        return max(preset_scores, key=preset_scores.get)

    def calculate_weapon_add_option_score(self, item_data: dict, class_name: str) -> float:
        if class_name in ["데몬어벤져"]:
            return 0.0

        add_option = item_data.get("item_add_option", {})
        base_option = item_data.get("item_base_option", {})

        if not add_option or not base_option:
            return 0.0

        main_stat = self.get_main_stat(class_name)
        target_atk_key = "magic_power" if main_stat == "int" else "attack_power"

        base_atk = int(base_option.get(target_atk_key, 0))
        add_atk = int(add_option.get(target_atk_key, 0))

        if base_atk == 0:
            return 0.0

        atk_score = (add_atk / base_atk) * 100

        boss_dmg_score = int(add_option.get("boss_damage", 0)) * 0.275
        dmg_score = int(add_option.get("damage", 0)) * 0.275
        all_stat_score = int(add_option.get("all_stat", 0)) * 0.2475
        target_stat_score = int(add_option.get(main_stat, 0)) * 0.05

        total_score = atk_score + boss_dmg_score + dmg_score + all_stat_score + target_stat_score

        return round(total_score, 2)

    def calculate_weapon_potential_score(self, item_data: dict, potential_type: str, class_name: str) -> float:
        slot = item_data.get("item_equipment_slot", "")
        part = item_data.get("item_equipment_part", "")
        is_wse = any(k in slot for k in ["무기", "보조무기"]) or "엠블렘" in part
        if not is_wse:
            return 0.0

        main_stat = self.get_main_stat(class_name)
        target_atk = "마력" if main_stat == "int" else "공격력"
        target_stat = main_stat.upper()

        options = [
            item_data.get(f"{potential_type}_option_1"),
            item_data.get(f"{potential_type}_option_2"),
            item_data.get(f"{potential_type}_option_3")
        ]

        total_stat_score = 0.0
        total_special_score = 0.0

        for opt in options:
            if not opt: continue

            val_match = re.search(r'\+(\d+)%', opt)
            if not val_match: continue
            val = int(val_match.group(1))

            # --- [분리] 특수 옵션 (데미지, 방무 등은 가중치 제외) ---
            if "데미지" in opt:
                total_special_score += val * 0.275
            elif "방어율 무시" in opt:
                total_special_score += val * 0.1875

            # --- [분리] 스탯/공격력 옵션 (가중치 적용 대상) ---
            elif target_atk in opt and "%" in opt:
                total_stat_score += val
            elif "올스탯" in opt and "%" in opt:
                total_stat_score += val * 0.2475
            elif target_stat in opt and "%" in opt:
                total_stat_score += val * 0.225

        return round(total_stat_score + total_special_score, 2)