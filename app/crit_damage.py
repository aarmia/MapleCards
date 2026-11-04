"""크리티컬 데미지 직접 합산 (보스 세팅 기준).

보스전 크뎀과 전투력 산출용 크뎀은 다릅니다. 전투력에는 아래 출처 중 확인된 성장 요소만 사용합니다.

- 상시: 장비 잠재·에디셔널·소울, 하이퍼스탯, 세트 효과, 유니온 공격대·점령, 유니온 아티팩트, 링크, 어빌리티, 헥사 스탯,
  유니온 챔피언 휘장, 직업 패시브(app/data/class_skills.toml)
- 보스 세팅 버프: 직업 지속 버프(같은 표), 길드 노블레스, 공용 쓸만한 샤프 아이즈(직업 샤프 아이즈가 있으면 중복 없음)
- 제외: 고급 무기 제련 같은 소모품 버프, 60초 이하 극딜 버프, 조건부 효과
"""

import re

from app.scoring_config import CLASS_SKILLS

_CRIT = re.compile(r"크리티컬 데미지\s*\+?\s*(\d+(?:\.\d+)?)\s*%")
_GEAR_KEYS = ("soul_option",) + tuple(f"{kind}_option_{n}" for kind in
                                      ("potential", "additional_potential", "soul_potential") for n in (1, 2, 3))
# 헥사 스탯 크리티컬 데미지 증가: 메인 스탯은 레벨별 누적값, 추가 스탯은 레벨당 0.35%
_HEXA_MAIN = (0.0, 0.35, 0.7, 1.05, 1.4, 2.1, 2.8, 3.5, 4.55, 5.6, 7.0)
_HEXA_SUB = 0.35
_HEXA_NAME = "크리티컬 데미지 증가"


def _crit(text) -> float:
    return sum(float(value) for value in _CRIT.findall(text or ""))


def _class_row(char_class: str) -> dict:
    name = (char_class or "").replace(" ", "")
    return CLASS_SKILLS.crit_damage.raw.get(name) or {"passive": 0.0, "boss_buff": 0.0, "sharp_eyes": False}


def _hexa(hexa: dict) -> float:
    total = 0.0
    cores = (core for key in ("character_hexa_stat_core", "character_hexa_stat_core_2", "character_hexa_stat_core_3")
             for core in (hexa or {}).get(key) or [])
    for core in cores:
        if core.get("main_stat_name") == _HEXA_NAME:
            total += _HEXA_MAIN[min(int(core.get("main_stat_level") or 0), len(_HEXA_MAIN) - 1)]
        for n in (1, 2):
            if core.get(f"sub_stat_name_{n}") == _HEXA_NAME:
                total += _HEXA_SUB * int(core.get(f"sub_stat_level_{n}") or 0)
    return total


def _hyper(hyper: dict) -> float:
    preset = (hyper or {}).get("use_preset_no")
    rows = (hyper or {}).get(f"hyper_stat_preset_{preset}") or []
    return sum(_crit(row.get("stat_increase")) for row in rows if row.get("stat_type") == "크리티컬 데미지" and row.get("stat_level"))


def boss_crit_damage(char_class: str, items: list, extras: dict) -> tuple[float, dict]:
    """현재 착용 상태에서 보스 세팅 버프를 켠 크리티컬 데미지(%)와 출처별 내역."""
    extras = extras or {}
    union = extras.get("union_raider") or {}
    row = _class_row(char_class)
    guild = extras.get("guild") or {}
    breakdown = {
        "gear": sum(_crit(item.get(key)) for item in items or [] for key in _GEAR_KEYS),
        "hyper_stat": _hyper(extras.get("hyper_stat")),
        "set": sum(_crit(effect.get("set_option")) for s in (extras.get("set_effect") or {}).get("set_effect") or []
                   for effect in s.get("set_effect_info") or []),
        "union": sum(_crit(text) for text in (union.get("union_raider_stat") or [])
                     + (union.get("union_state_stat", union.get("union_occupied_stat")) or [])),
        "artifact": sum(_crit(effect.get("name")) for effect in (extras.get("union_artifact") or {}).get("union_artifact_effect") or []),
        "link": sum(_crit(skill.get("skill_effect")) for skill in (extras.get("link_skill") or {}).get("character_link_skill") or []),
        "ability": sum(_crit(ability.get("ability_value")) for ability in (extras.get("ability") or {}).get("ability_info") or []),
        "hexa_stat": _hexa(extras.get("hexa_stat")),
        "union_champion": sum(_crit(badge.get("stat")) for badge in (extras.get("union_champion") or {}).get("champion_badge_total_info") or []),
        "class_passive": float(row.get("passive", 0.0)),
        "class_buff": float(row.get("boss_buff", 0.0)),
        "guild_noblesse": sum(_crit(skill.get("skill_effect")) for skill in guild.get("guild_noblesse_skill") or []),
        "useful_sharp_eyes": 0.0 if row.get("sharp_eyes") else float(CLASS_SKILLS.common.useful_sharp_eyes),
    }
    breakdown = {key: round(value, 2) for key, value in breakdown.items()}
    return round(sum(breakdown.values()), 2), breakdown


def growth_crit_damage(char_class: str, items: list, extras: dict) -> tuple[float, dict]:
    """게임 내 전투력 도움말의 성장 출처만 사용. 신규 출처는 자동 포함하지 않습니다."""
    _, boss = boss_crit_damage(char_class, items, extras)
    included = {key: boss[key] for key in ("gear", "set", "hyper_stat", "ability", "union", "artifact", "hexa_stat")}
    # 도움말에 유니온 챔피언 휘장은 별도 명시되어 있지 않아 검증 전까지 제외합니다.
    return round(sum(included.values()), 2), included
