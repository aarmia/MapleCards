"""공식 전투력 자체가 아닌 프리셋 변화율의 간이 지수.

전투력 = floor((주스탯 × 4 + 부스탯) ÷ 100) × 공격력 × (1 + 보공%) × (1 + 크뎀%)
이 근사식은 공식 설명의 데미지·최종 데미지, 무기 표준화, 직업별 보정을 재현하지 않습니다.

API 전투력은 조회 시점의 세팅(프리셋·하이퍼·유니온 등)으로 계산된 값이라 최적 세팅과 다를 수 있습니다.
보정 전투력 = API 전투력 × 공식(최적) ÷ 공식(현재).
공식의 절대값은 진단용으로만 남겨, 모델과 API의 기준 차이를 세팅 개선으로 표시하지 않습니다.
현재 세팅이거나 추정 상승률이 0.5% 이하이면 기존 정책대로 API 값을 유지합니다.
"""

import math


def combat_power(main_stat: float, sub_stat: float, attack: float, boss_damage_pct: float, crit_damage_pct: float) -> float:
    return math.floor((4 * main_stat + sub_stat) / 100) * attack * (1 + boss_damage_pct / 100) * (1 + crit_damage_pct / 100)


def adjust_combat_power(api_power: float, current: dict, optimal: dict, is_current: bool) -> dict:
    """current / optimal 은 combat_power 의 인자 dict. 현재 세팅이 최적이면 API 값을 그대로 둡니다."""
    formula_current = combat_power(**current)
    formula_optimal = combat_power(**optimal)
    basis = {
        "api": api_power,
        "formula_current": round(formula_current),
        "formula_optimal": round(formula_optimal),
        "ratio": 1.0,
        "status": "current",
        "adjusted": api_power,
    }
    if is_current or api_power <= 0 or formula_current <= 0:
        return basis
    ratio = formula_optimal / formula_current
    if ratio <= 1.005:
        return basis
    return {**basis, "ratio": round(ratio, 4), "status": "adjusted", "adjusted": round(api_power * ratio)}
