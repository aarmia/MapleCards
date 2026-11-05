"""전투력 보정용 크리티컬 데미지 합산 (성장 출처만).

게임 내 전투력 도움말이 포함한다고 밝힌 성장 요소만 더합니다 (docs/combat-power-policy.md):
장비 잠재·에디셔널·소울, 세트 효과, 하이퍼스탯, 어빌리티, 유니온 공격대·점령, 유니온 아티팩트, 헥사 스탯.
직업·링크·길드 스킬, 소비 아이템, 유니온 챔피언 휘장(도움말 미명시)은 넣지 않습니다. 신규 출처는 자동 포함하지 않습니다.
"""

import re

_CRIT = re.compile(r"크리티컬 데미지\s*\+?\s*(\d+(?:\.\d+)?)\s*%")
_GEAR_KEYS = ("soul_option",) + tuple(f"{kind}_option_{n}" for kind in
                                      ("potential", "additional_potential", "soul_potential") for n in (1, 2, 3))
# 헥사 스탯 크리티컬 데미지 증가: 메인 스탯은 레벨별 누적값, 추가 스탯은 레벨당 0.35%
_HEXA_MAIN = (0.0, 0.35, 0.7, 1.05, 1.4, 2.1, 2.8, 3.5, 4.55, 5.6, 7.0)
_HEXA_SUB = 0.35
_HEXA_NAME = "크리티컬 데미지 증가"


def _crit(text) -> float:
    return sum(float(value) for value in _CRIT.findall(text or ""))


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


def growth_crit_damage(char_class: str, items: list, extras: dict) -> tuple[float, dict]:
    """현재 착용 상태의 성장 출처 크리티컬 데미지(%)와 출처별 내역. char_class 는 호출 형태를 맞추려고 받습니다."""
    extras = extras or {}
    union = extras.get("union_raider") or {}
    breakdown = {
        "gear": sum(_crit(item.get(key)) for item in items or [] for key in _GEAR_KEYS),
        "set": sum(_crit(effect.get("set_option")) for s in (extras.get("set_effect") or {}).get("set_effect") or []
                   for effect in s.get("set_effect_info") or []),
        "hyper_stat": _hyper(extras.get("hyper_stat")),
        "ability": sum(_crit(ability.get("ability_value")) for ability in (extras.get("ability") or {}).get("ability_info") or []),
        "union": sum(_crit(text) for text in (union.get("union_raider_stat") or [])
                     + (union.get("union_state_stat", union.get("union_occupied_stat")) or [])),
        "artifact": sum(_crit(effect.get("name")) for effect in (extras.get("union_artifact") or {}).get("union_artifact_effect") or []),
        "hexa_stat": _hexa(extras.get("hexa_stat")),
    }
    breakdown = {key: round(value, 2) for key, value in breakdown.items()}
    return round(sum(breakdown.values()), 2), breakdown
