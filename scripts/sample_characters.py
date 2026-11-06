"""감사·점검 스크립트가 조회하는 샘플 캐릭터. 저장소에는 익명 라벨만 두고 실제 캐릭터 이름은 두지 않습니다.

실제 이름은 커밋되지 않는 .env 의 MECULATOR_SAMPLE_CHARACTERS 에 '라벨=이름' 을 ; 로 이어 적습니다.
예) MECULATOR_SAMPLE_CHARACTERS=2고스펙=<이름>;1랭커=<이름>;5세나=<이름>
출력에는 라벨만 씁니다.
"""
import os

from dotenv import load_dotenv

LABELS = ("2고스펙", "1랭커", "5세나")


def character_name(label: str) -> str:
    load_dotenv()
    pairs = dict(item.split("=", 1) for item in os.getenv("MECULATOR_SAMPLE_CHARACTERS", "").split(";") if "=" in item)
    name = pairs.get(label, "").strip()
    if not name:
        raise SystemExit(f".env 의 MECULATOR_SAMPLE_CHARACTERS 에 '{label}=<캐릭터 이름>' 이 필요합니다")
    return name
