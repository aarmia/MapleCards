"""전투력 공식과 최적 세팅 보정.

전투력 = floor((주스탯 × 4 + 부스탯) ÷ 100) × 공격력 × (1 + 보공%) × (1 + 크뎀%)
(엔젤릭버스터·캐논마스터 캐릭터 실측으로 1% 안에서 확인. 데미지%·크뎀 기본 1.35·최종뎀은 들어가지 않습니다)

API 전투력은 조회 시점의 세팅(프리셋·하이퍼·유니온 등)으로 계산된 값이라 최적 세팅과 다를 수 있습니다.
보정 전투력은 두 추정 중 큰 쪽입니다.
- 절대값: 최적 세팅 투영값을 공식에 넣은 값. 사냥 세팅 스냅샷처럼 API 전투력이 자기 스탯보다 낮게 잡힌 경우에 맞습니다.
- 비율: API 전투력 × 공식(최적) ÷ 공식(현재). API 크뎀이 인게임보다 낮게 오는 직업처럼 API 전투력이 공식보다 큰 경우에 맞습니다.
최적 세팅은 현재 세팅보다 나쁠 수 없으므로 API 값 아래로는 내리지 않습니다. 직업별 공식 오차(5% 안팎)는 남습니다.
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
    scaled = api_power * formula_optimal / formula_current
    adjusted = max(formula_optimal, scaled)
    if adjusted <= api_power * 1.005:  # 0.5% 이상 올라갈 때만 보정
        return basis
    return {**basis, "ratio": round(adjusted / api_power, 4), "status": "adjusted", "adjusted": round(adjusted)}
