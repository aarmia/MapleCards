"""스펙업 추천: 고정 프리셋에서 기대 비용 100억 메소당 최종뎀 증가율로 정렬합니다.

행동은 아이템마다 축별로 하나씩 만듭니다 (방어구·장신구, 무기는 소울 증폭만).
- 스타포스: 다음 1성. 증가량 = 성별 증가표(scoring.toml starforce.gain)의 주스탯% 환산 차이. 비용은 노작 시세로 화면이 계산합니다.
- 윗잠재·에디셔널: 메소 재설정으로 "환산 X% 이상"이 나올 때까지. 공식 줄별 확률(potential_odds.json)로 세 줄 합계 분포를 만들고,
  목표 X 마다 성공 확률 P 와 성공 시 기대값 E[V | V ≥ X] 를 구해 효율이 가장 높은 X 를 고릅니다.
  레전드리 미만이면 등급업 비용(천장 포함)을 더합니다.
- 소울 증폭: 다음 1단계. 지금 3줄을 공식 표(soul_potential.json)의 다음 단계 값으로 바꿔 증가를 구합니다.
  0단계는 미라클로 레전드리까지 올려 공/마% 두 줄을 뽑는 재설정 비용을 더합니다. 비용 = 기대 시도 수 × (메소 + 에테르 시세) + 재설정 메소,
  에테르 시세는 화면이 계산합니다.
- 추가옵션: 메소 재설정으로 "N급 이상"이 나올 때까지. 19종 중 4종(중복 없음) × 단계 확률로 급수 분포를 정확히 계산하고
  같은 방식으로 목표를 고릅니다.
주스탯%는 표시·목표 판정 단위로 유지합니다. 상시 옵션은 기존 옵션 제거 후 새 옵션을 적용한
지수의 비율, 확률 강화는 성공 결과별 비율의 조건부 평균을 사용합니다.
"""

import itertools
import json
import math
import re

from app.calculator import calculate_item_score, starforce_option
from app.damage_index import ReplacementModel, flame_options, line_options
from app.flame_damage import distribution as flame_moments
from app.flame_damage import rewards as flame_rewards
from app.potential import StatContext, analyze_lines, convert_line
from app.scoring_config import DATA_DIR, POTENTIAL, SCORING, SPEC_UP, STARFORCE_COST
from app.set_projection import DAWN, MEMBERS, PITCHED, RADIANT
from app.weapon import _BOSS, _DAMAGE, _IED, SOUL_KEYS, WeaponContext, analyze_weapon_lines

ODDS = json.loads((DATA_DIR / "potential_odds.json").read_text(encoding="utf-8"))
EOK = 100_000_000  # 1억 메소
KIND_LABEL = {"potential": "윗잠재", "additional": "에디셔널"}
KIND_PREFIX = {"potential": "potential", "additional": "additional_potential"}
MIN_STEP_CHANCE = 1e-6
COOLDOWN = "재사용 대기시간"  # 쿨감 줄


def expected_resets(chance: float, pity: int) -> float:
    """등급업까지 평균 재설정 횟수. pity 번 연속 실패하면 그다음 재설정은 확정입니다."""
    return (1 - (1 - chance) ** (pity + 1)) / chance


def _cost_row(table, level: int) -> list[float]:
    return next(costs for floor, costs in table if level >= floor)


def grade_up_cost(kind: str, grade: str, level: int) -> float:
    """현재 등급에서 레전드리까지 평균 메소 (천장 포함)."""
    cube = SPEC_UP.cube[kind]
    grades = SPEC_UP.cube.grades
    costs = _cost_row(cube["cost"], level)
    start = grades.index(grade)
    return sum(expected_resets(cube["grade_up"][i], cube["pity"][i]) * costs[i] * EOK for i in range(start, len(grades) - 1))


def _odds_table(kind: str, part: str, level: int):
    """공식 표. 그 레벨 구간에 표가 없는 부위(250제 벨트 등)는 아래 구간 표를 씁니다."""
    floors = sorted((int(floor) for floor in ODDS[kind]), reverse=True)
    for floor in (f for f in floors if f <= level):
        table = ODDS[kind][str(floor)].get(part)
        if table is not None:
            return table
    return None


def _convolve(a: dict, b: dict, digits: int = 2) -> dict:
    out: dict = {}
    for va, pa in a.items():
        for vb, pb in b.items():
            key = round(va + vb, digits)
            out[key] = out.get(key, 0.0) + pa * pb
    return out


def potential_distribution(kind: str, part: str, level: int, context: StatContext, char_level: int, value_of=None) -> dict | None:
    """레전드리 재설정 1회의 세 줄 환산 합계 분포 {주스탯%: 확률}. value_of(줄) 를 주면 그 값의 분포입니다(검증용)."""
    table = _odds_table(kind, part, level)
    if table is None:
        return None
    total = {0.0: 1.0}
    for line in table:
        dist: dict = {}
        for text, chance in line:
            if value_of is not None:
                value = value_of(text)
            else:
                converted = convert_line(text, context, char_level)
                value = round(converted[0], 2) if converted else 0.0
            dist[value] = dist.get(value, 0.0) + chance
        total = _convolve(total, dist)
    return total


def best_target(dist: dict, current: float, cost_per_try: float, setup_cost: float = 0.0, first_try_free: bool = False,
                min_target: float = float("-inf"), max_target: float = float("inf"), rewards: dict | None = None):
    """목표 X(min_target ≤ X ≤ max_target) 마다 (기대 증가량 ÷ 기대 비용)을 계산해 가장 좋은 X 를 고릅니다.
    기대 증가량은 X 이상 결과 전체(max_target 을 넘는 결과 포함)의 평균 − 현재입니다.
    first_try_free: 등급업 직후 첫 레전드리 결과도 성공일 수 있어 추가 재설정이 (1 − P) / P 회입니다."""
    best, tail_p, tail_v, tail_reward = None, 0.0, 0.0, 0.0
    for value in sorted(dist, reverse=True):
        tail_p += dist[value]
        tail_v += dist[value] * value
        if rewards is not None:
            tail_reward += rewards.get(value, 0)
        if tail_p <= 0 or not (min_target - 1e-9 <= value <= max_target + 1e-9):
            continue
        gain = tail_v / tail_p - current
        if gain <= 1e-9:
            continue
        tries = (1 - tail_p) / tail_p if first_try_free else 1 / tail_p
        cost = setup_cost + tries * cost_per_try
        if cost <= 0:
            continue
        reward = tail_reward / tail_p if rewards is not None else gain
        if reward <= 0:
            continue
        efficiency = reward / cost
        if best is None or efficiency > best["efficiency"]:
            best = {"target": value, "chance": tail_p, "gain": gain, "cost": cost, "tries": tries, "efficiency": efficiency,
                    "reward": reward}
    return best


def flame_steps(dist: dict, current: float, cost_per_try: float) -> list[dict]:
    """추가옵션 목표 단계: 현재 + first_step 급 이상, 그 뒤 step 급씩. 나올 수 있는 최대 급수까지 단계마다
    성공 확률·평균 재설정·기대 비용·성공 시 기대 급수 증가를 계산합니다."""
    flame = SPEC_UP.flame
    top = max(dist)
    steps, target = [], current + flame.first_step
    while target <= top + 1e-9:
        hits = [(value, chance) for value, chance in dist.items() if value >= target - 1e-9]
        chance = sum(p for _, p in hits)
        if chance < MIN_STEP_CHANCE:  # 100만 번에 한 번보다 드문 목표는 보여주지 않습니다
            break
        if chance > 0:
            gain = sum(v * p for v, p in hits) / chance - current
            steps.append({"plus": round(target - current, 1), "target": round(target, 1), "threshold": target, "chance": chance,
                          "tries": 1 / chance, "cost": cost_per_try / chance, "gain": gain})
        target += flame.step
    return steps


def potential_outcomes(kind: str, part: str, level: int, context: StatContext, char_level: int) -> list | None:
    """레전드리 재설정 1회에 나올 수 있는 세 줄 구성: [(유효 줄 텍스트들, 환산 합계, 쿨감 줄 환산값, 확률)].
    환산값이 0 인 줄은 '기타'로 묶어 조합 수를 줄입니다 (같은 구성은 확률을 더합니다)."""
    table = _odds_table(kind, part, level)
    if table is None:
        return None
    reduced = []
    for line in table:
        merged: dict = {}
        for text, chance in line:
            converted = convert_line(text, context, char_level)
            value = round(converted[0], 2) if converted else 0.0
            key = text if converted is not None else None  # retain capped crit rate: reroll can lose existing rate
            old = merged.get(key, (value if key else 0.0, 0.0))
            merged[key] = (old[0], old[1] + chance)
        reduced.append([(key, value, chance) for key, (value, chance) in merged.items()])
    outcomes: dict = {}
    for (k1, v1, p1), (k2, v2, p2), (k3, v3, p3) in itertools.product(*reduced):
        texts = tuple(sorted(k for k in (k1, k2, k3) if k))
        cooldown = sum(v for k, v in ((k1, v1), (k2, v2), (k3, v3)) if k and COOLDOWN in k)
        value, _, _, prob = outcomes.get(texts, (round(v1 + v2 + v3, 2), 0, 0, 0.0))
        outcomes[texts] = (value, cooldown, 0, prob + p1 * p2 * p3)
    return [(texts, value, cooldown, prob) for texts, (value, cooldown, _, prob) in outcomes.items()]


def _combo_text(texts: tuple) -> str:
    return " + ".join(texts + ("기타",) * (3 - len(texts))) if texts else "기타 3줄"


# 경우의 수: 이름 → (구성 → 그 경우의 환산값, 아니면 None). 모자 에디셔널만 쿨감 유무로 나눕니다.
VARIANTS = {
    "all": ("", lambda value, cooldown: value),
    "main": ("주스탯 ", lambda value, cooldown: round(value - cooldown, 2)),
    "cooldown": ("쿨감 1초 + 주스탯 ", lambda value, cooldown: value if cooldown > 0 else None),
}


def potential_actions(item: dict, kind: str, part: str, level: int, context: StatContext, char_level: int,
                      cap: float | None = None, memo: dict | None = None) -> list[dict]:
    """윗잠재·에디셔널 재설정 추천. cap(그 아이템의 축 만점)이 있으면 목표를 만점 이하로 두고, 현재가 만점 이상이면 추천하지 않습니다.
    모자 에디셔널은 '주스탯만'(쿨감 줄은 0 으로 셈)과 '쿨감 1초 + 주스탯'(쿨감 줄이 있는 결과만 성공) 두 가지로 나눕니다."""
    prefix = KIND_PREFIX[kind]
    grade = item.get(f"{prefix}_option_grade")
    grades = SPEC_UP.cube.grades
    if grade not in grades:
        return []
    key = (kind, part, level)
    memo = memo if memo is not None else {}
    if key not in memo:
        memo[key] = potential_outcomes(kind, part, level, context, char_level)
    outcomes = memo[key]
    if not outcomes:
        return []
    legendary = grade == grades[-1]
    lines = [item.get(f"{prefix}_option_{i}") for i in (1, 2, 3)]
    current = max(analyze_lines([line for line in lines if line], context, char_level)["score"], 0.0)
    cube = SPEC_UP.cube[kind]
    if cap is not None and current >= cap - 1e-9:
        return []  # 이미 만점 (종결)
    legendary_cost = _cost_row(cube["cost"], level)[-1] * EOK
    setup = grade_up_cost(kind, grade, level)
    via = "" if legendary else f"{grade} → 레전드리 등급업 후 "
    names = ("main", "cooldown") if kind == "additional" and part == "모자" else ("all",)
    actions = []
    # 같은 진단 안에서는 계산 모델과 결과 구성별 옵션 해석을 재사용합니다 (부위·레벨이 같으면 결과 구성도 같음).
    # 요청 사이 캐시는 두지 않습니다: 워커당 수십 MB 를 쓰는 데 비해 이득이 작았습니다.
    model_key = ("model", id(context))
    if model_key not in memo:
        try:
            memo[model_key] = ReplacementModel(context)
        except ValueError:
            memo[model_key] = None
    model = memo[model_key]
    parsed_key = ("parsed", *key, char_level)
    if model and parsed_key not in memo:
        memo[parsed_key] = [line_options(texts, char_level) for texts, _, _, _ in outcomes]
    before = line_options(lines, char_level)
    outcome_gains = {texts: model.gain(before, after)
                     for (texts, _, _, _), after in zip(outcomes, memo[parsed_key])} if model else {}
    for name in names:
        prefix_label, value_of = VARIANTS[name]
        dist: dict = {}
        rewards: dict = {}
        for texts, value, cooldown, prob in outcomes:
            variant_value = value_of(value, cooldown)
            slot_value = variant_value if variant_value is not None else float("-inf")
            dist[slot_value] = dist.get(slot_value, 0.0) + prob
            if model:
                rewards[slot_value] = rewards.get(slot_value, 0.0) + prob * outcome_gains[texts]
        best = best_target(dist, current, legendary_cost, setup, first_try_free=not legendary,
                           min_target=current + cube["min_step"], max_target=cap if cap is not None else float("inf"),
                           rewards=rewards if model else None)
        if not best:
            continue
        hits = [(texts, prob) for texts, value, cooldown, prob in outcomes
                if (value_of(value, cooldown) if value_of(value, cooldown) is not None else float("-inf")) >= best["target"] - 1e-9]
        top_texts, top_prob = max(hits, key=lambda hit: hit[1])
        shown = best["target"]
        if name == "cooldown":  # 표시는 쿨감 1초 몫을 뺀 주스탯% (판정·효율은 쿨감 포함 환산값 그대로)
            converted = convert_line("스킬 재사용 대기시간 -1초", context, char_level)
            shown = round(shown - (converted[0] if converted else 0.0), 2)
        final_damage = best["reward"] if model else best["gain"] * final_damage_per_pct(context)
        hybrid = before.get("cooldown", 0) or any(line_options(texts, char_level).get("cooldown", 0) for texts, _ in hits) or context.is_archer
        actions.append({"axis": KIND_LABEL[kind], "variant": name,
                        "final_damage": final_damage,
                        "model": ("direct_with_skill_estimate" if hybrid else "direct") if model else "legacy_special_stat",
                        "label": f"{via}{prefix_label}환산 {shown:g}% 이상까지 재설정",
                        "combo": _combo_text(top_texts), "combo_share": round(top_prob / best["chance"], 4),
                        "gain": final_damage / final_damage_per_pct(context), "cost": best["cost"], "chance": best["chance"], "tries": best["tries"]})
    return actions


def _flame_option(key: str, tier: int, level: int) -> dict:
    band = next(entry for entry in SCORING.add_option.flame.bands if level >= entry[0])
    single, dual, hp = band[1] * tier, band[2] * tier, band[3] * tier
    if "+" in key:
        return dict.fromkeys(key.split("+"), dual)
    if key in ("str", "dex", "int", "luk"):
        return {key: single}
    if key in ("max_hp", "max_mp"):
        return {key: hp}
    if key in ("attack_power", "magic_power", "all_stat"):
        return {key: tier * SCORING.add_option.flame.other_per_tier}
    return {}  # 방어력·이동속도·점프력·착용 레벨 감소: 점수 없음


def flame_distribution(level: int, class_name: str, main_stat: str, context: StatContext) -> dict:
    """추가옵션 재설정 1회의 급수 분포 {급수: 확률}: 옵션 종류 lines 개를 중복 없이 균등하게, 줄마다 단계 확률로."""
    flame = SPEC_UP.flame
    tiers = [tier + flame.boss_bonus for tier in flame.tiers]
    # dp[k] = k 종류를 고른 합계 분포(조합마다 가중치 1). 끝에서 조합 수로 나눕니다.
    dp = [{0.0: 1.0}] + [{} for _ in range(flame.lines)]
    null_types = 0  # 급수 0 인 종류: 분포를 바꾸지 않으므로 DP 에서 빼고 끝에서 조합 수로 채웁니다 (같은 결과)
    for key in flame.options:
        dist: dict = {}
        for tier, chance in zip(tiers, flame.tier_odds):
            grade = round(calculate_item_score(_flame_option(key, tier, level), class_name, main_stat, context), 1)
            dist[grade] = dist.get(grade, 0.0) + chance
        if set(dist) == {0.0}:
            null_types += 1
            continue
        for k in range(flame.lines, 0, -1):
            if dp[k - 1]:
                for value, weight in _convolve(dp[k - 1], dist, 1).items():
                    dp[k][value] = dp[k].get(value, 0.0) + weight
    combos = math.comb(len(flame.options), flame.lines)
    result: dict = {}
    for j in range(flame.lines + 1):  # 가치 있는 종류 j 개 + 급수 0 종류 (lines − j) 개
        weight = math.comb(null_types, flame.lines - j) / combos
        for value, chance in dp[j].items():
            result[value] = result.get(value, 0.0) + chance * weight
    return {value: chance for value, chance in result.items() if chance}


def starforce_recommendation_cap(name: str) -> int:
    """User's recommendation ceiling, not the game's enhancement limit."""
    name = ' '.join(name.split())
    if name.startswith('에테르넬') or name in MEMBERS[RADIANT]:
        return 26
    if name in MEMBERS[PITCHED]:
        return 25
    if name.startswith('아케인셰이드') or name in MEMBERS[DAWN] or name == '마이스터링':
        return 23
    return 22


def starforce_action(name: str, part: str, slot: str, level: int, star: int, max_star: int,
                     class_name: str, main_stat: str, context: StatContext, target_star: int | None = None) -> dict | None:
    """현재 → 목표 성의 누적 최종뎀. 비용은 노작 시세로 화면(app.js starforceCost)이 계산합니다. 12성 미만은 비용 자료가 없어 만들지 않습니다."""
    target_star = star + 1 if target_star is None else target_star
    if star < 12 or not star < target_star <= min(max_star, starforce_recommendation_cap(name)):
        return None
    now = starforce_option(level, star, main_stat, context.sub_names, part)
    nxt = starforce_option(level, target_star, main_stat, context.sub_names, part)
    if not now or not nxt:
        return None
    gain = (calculate_item_score(nxt, class_name, main_stat, context)
            - calculate_item_score(now, class_name, main_stat, context)) * context.pct_per_flat
    try:
        model = ReplacementModel(context)
        final_damage = model.gain(flame_options(now), flame_options(nxt))
        gain = final_damage / model.per_pct
        method = "direct"
    except ValueError:
        final_damage, method = gain * final_damage_per_pct(context), "legacy_special_stat"
    if gain <= 0:
        return None
    return {"axis": "스타포스", "label": f"{star}→{target_star}성", "gain": gain,
            "final_damage": final_damage, "model": method, "star": star, "target_star": target_star, "level": level}


STAT_LABEL = {"hp": "HP", "all_stat": "올스탯"}


def stat_label(context: StatContext) -> str:
    """효율 앞에 붙이는 주스탯 이름: STR·DEX·INT·LUK·HP·올스탯"""
    return STAT_LABEL.get(context.main_stat, context.main_label)


def final_damage_per_pct(context: StatContext) -> float:
    """유효 스탯 1% 가 올리는 최종 데미지(%). 1% = 최종 주스탯 + a'/100 이므로 ΔA/A × 100 = w × a' / A.
    공격력·데미지·크뎀 등 나머지 배율은 그대로라 스탯 공격력(A)의 증가 비율이 곧 최종 데미지 증가입니다."""
    if context.stat_attack <= 0:
        return 0.0
    return context.stat_weight * context.base_stat_effective / context.stat_attack


RESTRAINT, CONTINUOUS = "리스트레인트 링", "컨티뉴어스 링"


def burst_share(class_name: str) -> tuple[float, float]:
    """리레 4(15초)·리레 5 이상(20초) 구간이 120초 주기 딜에서 차지하는 비중(0~1). 표에 없는 직업은 평균."""
    s15, s20 = SPEC_UP.seed_ring.burst_share.get(class_name) or SPEC_UP.seed_ring.burst_share["default"]
    return s15 / 100, s20 / 100


def seed_ring_action(name: str, level: int, class_name: str, context: StatContext, damage_total: float,
                     continuous_level: int) -> dict | None:
    """시드링 다음 레벨: (리레) 구간 딜 비중 · (컨티) 가동률 × 상대 증가 = 최종 데미지(%) → 주스탯% (final_damage_per_pct 의 역).
    리레 구간의 공/마 배율에는 컨티 공/마를, 컨티의 보공 배율에는 컨티 자신의 현재 보공을 포함합니다."""
    ring = SPEC_UP.seed_ring
    step = ring.upgrade.get(str(level + 1))
    per_pct = final_damage_per_pct(context)
    if step is None or per_pct <= 0:
        return None
    s15, s20 = burst_share(class_name)
    conti = ring.continuous.get(str(continuous_level), [0, 0])
    attack = context.attack_multiplier_effective * 100  # 공/마 % 배율 (100 = 기본)
    if RESTRAINT in name:
        now, nxt = ring.restraint.get(str(level)), ring.restraint.get(str(level + 1))
        if not now or not nxt:
            return None
        base = attack + conti[1]

        def total(effect):  # 리레 없는 딜 1 대비 전체 딜: 1 + 구간 비중 × 공/마 ÷ 공/마 배율
            seconds, attack_pct = effect
            return 1 + (s15 if seconds <= 15 else s20) * attack_pct / base

        # 컨티와 같은 기준: 지금 레벨의 전체 딜 대비 다음 레벨의 전체 딜 증가
        final_damage = 100 * (total(nxt) / total(now) - 1)
        detail = f"{now[0]}초 공/마 {now[1]}% → {nxt[0]}초 공/마 {nxt[1]}%"
    elif CONTINUOUS in name:
        now, nxt = ring.continuous.get(str(level)), ring.continuous.get(str(level + 1))
        if not now or not nxt:
            return None
        boss = 100 + damage_total + POTENTIAL.buff.damage_pct + POTENTIAL.buff.boss_damage_pct + now[0]  # 스탯창 + 도핑 + 컨티
        uptime = ring.continuous["uptime"]
        final_damage = 100 * uptime * ((1 + (nxt[0] - now[0]) / boss) * (1 + (nxt[1] - now[1]) / (attack + now[1])) - 1)
        detail = f"상시 보공 {now[0]}%·공/마 {now[1]}% → 보공 {nxt[0]}%·공/마 {nxt[1]}%"
    else:
        return None
    if final_damage <= 0:
        return None
    return {"axis": "시드링", "label": f"{level}→{level + 1}레벨 ({step['material']} {step['count']}개 + {step['meso'] / EOK:g}억)",
            "final_damage": final_damage, "model": "seed_ring_uptime", "detail": detail, "gain": final_damage / per_pct,
            "material": {"name": step["material"], "count": step["count"], "meso": step["meso"]}}


SOUL_AXIS = "소울 증폭"
SOUL_TABLES = json.loads((DATA_DIR / "soul_potential.json").read_text(encoding="utf-8"))["tables"]  # 등급 → 단계 → 줄 → [옵션, %]


def next_soul_line(grade: str, line: int, stage: int, text: str) -> str:
    """공식 표에서 같은 등급·줄·행의 다음 단계 옵션. 표에 없는 줄은 그대로 둡니다."""
    stages = SOUL_TABLES.get(grade) or {}
    if str(stage + 1) not in stages:
        return text
    return next((upgraded for (option, _), (upgraded, _) in zip(stages[str(stage)][line], stages[str(stage + 1)][line])
                 if option == text), text)


def soul_first_potential(attack_label: str) -> tuple[list[str], float, float]:
    """0→1단계 뒤 소울 잠재: 레어에서 미라클로 레전드리까지 + 첫 줄과 둘째·셋째 중 한 줄 이상이 공/마% 일 때까지 재설정.
    둘째·셋째 줄만 공/마% 인 결과는 성공으로 보지 않습니다. (가정한 1단계 두 줄, 평균 메소, 1회 성공 확률)"""
    soul = SPEC_UP.soul
    meso = sum(expected_resets(chance * soul.miracle, pity) * cost
               for chance, pity, cost in zip(soul.grade_up, soul.pity, soul.reset_cost))
    pattern = re.compile(rf"^{attack_label} \+[\d.]+%$")
    lines = SOUL_TABLES[SPEC_UP.cube.grades[-1]]["1"]
    p1, p2, p3 = (sum(chance for text, chance in rows if pattern.match(text)) / 100 for rows in lines)
    hit = p1 * (1 - (1 - p2) * (1 - p3))
    assumed = [max((row for row in rows if pattern.match(row[0])), key=lambda row: row[1])[0] for rows in lines[:2]]
    return assumed, (meso + soul.reset_cost[-1] / hit) * EOK, hit


def amplify_tries(chance: float, step: float, pity: int) -> float:
    """증폭 기대 시도 수: k번째 시도 확률 = chance + (k−1)·step, pity 번 연속 실패한 다음 시도는 확정."""
    alive, expected = 1.0, 0.0
    for k in range(1, pity + 2):
        q = 1.0 if k == pity + 1 else min(1.0, chance + (k - 1) * step)
        expected += alive * q * k
        alive *= 1 - q
    return expected


def soul_damage(before: list, after: list, context: StatContext, weapon: WeaponContext, char_level: int,
                damage_total: float) -> tuple[float, str]:
    """소울 잠재 줄 교체의 최종 데미지(%). 스탯·공/마·크리는 ReplacementModel, 보공·데미지는 시드링과 같은 분모,
    방무는 무기 환산과 같은 남은 방어율 비율. 특수 주스탯 직업은 무기 공% 환산값 ÷ 전투 중 공격력 배율."""
    try:
        model = ReplacementModel(context)
    except ValueError:
        units = analyze_weapon_lines(after, weapon, char_level)["score"] - analyze_weapon_lines(before, weapon, char_level)["score"]
        return units / weapon.attack_multiplier, "legacy_special_stat"

    def matched(lines, *patterns):
        return [float(m[1]) for line in lines for m in (p.match(line) for p in patterns) if m]

    stat = model.gain(line_options(before, char_level), line_options(after, char_level))
    damage = 100 + damage_total + POTENTIAL.buff.damage_pct + POTENTIAL.buff.boss_damage_pct  # 스탯창(지금 줄 포함) + 도핑
    damage_ratio = (damage - sum(matched(before, _BOSS, _DAMAGE)) + sum(matched(after, _BOSS, _DAMAGE))) / damage
    defense, remaining = POTENTIAL.weapon.boss_defense / 100, weapon.remaining_defense
    without = remaining / math.prod(1 - pct / 100 for pct in matched(before, _IED))
    now = max(0.0, 1 - defense * remaining)
    new = max(0.0, 1 - defense * without * math.prod(1 - pct / 100 for pct in matched(after, _IED)))
    ied_ratio = new / now if now > 0 else 1.0
    return 100 * ((1 + stat / 100) * damage_ratio * ied_ratio - 1), "direct"


def soul_action(item: dict, level: int, context: StatContext, weapon: WeaponContext, char_level: int,
                damage_total: float) -> dict | None:
    """소울 증폭 다음 단계. 위대한 소울·요구 레벨 200 이상 무기만. 비용(에테르 시세 포함)은 화면이 계산합니다."""
    soul = SPEC_UP.soul
    if level < soul.min_level or soul.keyword not in (item.get("soul_name") or ""):
        return None
    stage = int(item.get("soul_potential_amplified_grade") or 0)
    step = soul.amplify.get(str(stage + 1))
    per_pct = final_damage_per_pct(context)
    lines = [(index, str(item[key]).strip()) for index, key in enumerate(SOUL_KEYS) if str(item.get(key) or "").strip()]
    if step is None or per_pct <= 0 or (stage > 0 and not lines):
        return None
    tries = amplify_tries(step["chance"], step["step"], step["pity"])
    meso = tries * step["meso"]
    label = f"{stage}→{stage + 1}단계 (평균 {tries:.1f}회 · {step['material']} {tries:.1f}개 + {meso / EOK:.1f}억"
    before = [text for _, text in lines]
    if stage == 0:
        after, potential, hit = soul_first_potential("마력" if context.main_stat == "int" else "공격력")
        meso += potential
        label += f" · 잠재 {potential / EOK:.1f}억"
        detail = (f"가정: 레전드리 {' · '.join(after)} (미라클로 레어→레전드리 후 첫 줄 포함 공/마% 두 줄이 나올 때까지 재설정, 1회 {hit * 100:.2f}%)")
    else:
        grade = item.get("soul_potential_grade") or ""
        after = [next_soul_line(grade, index, stage, text) for index, text in lines]
        detail = " · ".join(before) + " → " + " · ".join(after)
    final_damage, method = soul_damage(before, after, context, weapon, char_level, damage_total)
    if final_damage <= 0:
        return None
    return {"axis": SOUL_AXIS, "label": label + ")", "final_damage": final_damage, "model": method,
            "gain": final_damage / per_pct, "detail": detail, "material": {"name": step["material"], "count": tries, "meso": meso}}


def seed_ring_value(name: str, level: int, class_name: str, context: StatContext, damage_total: float) -> tuple[float, float] | None:
    """시드링 효과의 주스탯 %p 환산 (지금 레벨, 최고 레벨). 리스트레인트·컨티뉴어스만, 나머지는 None.
    리레 = 구간 딜 비중 × 공/마 ÷ 공/마 배율, 컨티 = 가동률 × (보공·공/마 상대 증가). 다음 레벨 추천과 같은 식입니다."""
    ring = SPEC_UP.seed_ring
    per_pct = final_damage_per_pct(context)
    attack = context.attack_multiplier_effective * 100
    if per_pct <= 0 or attack <= 0:
        return None
    if RESTRAINT in name:
        table, s15, s20 = ring.restraint, *burst_share(class_name)

        def damage(effect):
            return 100 * (s15 if effect[0] <= 15 else s20) * effect[1] / attack
    elif CONTINUOUS in name:
        table, boss = ring.continuous, 100 + damage_total + POTENTIAL.buff.damage_pct + POTENTIAL.buff.boss_damage_pct

        def damage(effect):
            return 100 * ring.continuous["uptime"] * ((1 + effect[0] / boss) * (1 + effect[1] / attack) - 1)
    else:
        return None
    levels = sorted(int(key) for key in table.raw if key.isdigit())
    now = table.raw.get(str(level))
    return (damage(now) / per_pct if now else 0.0), damage(table.raw[str(levels[-1])]) / per_pct


def finish(action: dict) -> dict:
    """화면용 반올림. 비용이 시세와 무관한 행동(잠재·추옵)만 서버가 효율을 붙이고, 스타포스·시드링은 화면이 시세로 계산합니다."""
    out = dict(action)
    out['stat_conversion'] = action['final_damage'] / action['gain'] if action['gain'] else 0
    if "cost" in action:
        out["efficiency"] = round(action["gain"] / (action["cost"] / SPEC_UP.ranking.unit), 4)
        out["damage_efficiency"] = action["final_damage"] / (action["cost"] / SPEC_UP.ranking.unit)
        out["cost"] = round(action["cost"])
    out["gain"] = round(action["gain"], 2)
    if "chance" in out:
        out["chance"] = round(out["chance"], 6)
        out["tries"] = round(out["tries"], 1)
    return out


def recommendation_assumptions(context: StatContext, class_name: str) -> list[str]:
    """Expose configured assumptions without independently maintained UI constants."""
    s15, s20 = burst_share(class_name)
    return [
        "선택된 프리셋을 고정하고 기존 옵션을 제거한 뒤 새 옵션을 적용한 데미지 지수의 비율로 최종뎀을 추정합니다. 실제 DPS·전투력 상승이나 강화 후 재최적화 결과가 아닙니다. 쿨감·시드링·특수 주스탯은 별도 추정 모델입니다. 추천끼리 증가율을 단순 합산하지 마세요.",
        f"쿨감 1초는 주스탯 {POTENTIAL.cooldown_per_second:g}% 상당의 고정 가정입니다. 직업별 실제 스킬 주기와 딜 상승을 재현하지 않습니다.",
        "스타포스 비용은 샤타 이벤트(15→16 확정 미포함)와 설정된 파괴 방지·복구 전략 기준입니다. 노작·연마석 시세는 실시간 시세가 아니며 직접 수정할 수 있습니다.",
        "잠재는 설정된 줄별 확률·등급업 천장 기준입니다. 기대 비용은 평균이지 예산 내 성공 보장이 아니며, 증가량은 목표 이상 결과들의 조건부 평균입니다.",
        "소울 증폭은 지금 소울 잠재 줄을 넥슨 공식 확률표의 다음 단계 수치로 바꿔 비교합니다. 비용은 게이지를 반영한 기대 시도 수 × (메소 + 에테르 시세)입니다. 0→1단계는 소울 잠재가 레어로 생긴다고 보고 미라클 기준으로 레전드리까지 올린 뒤 첫 줄 포함 공/마% 두 줄이 나올 때까지의 평균 재설정 비용을 더합니다.",
        f"시드링은 {class_name}의 리레 15초/20초 딜 비중 {s15*100:.2f}%/{s20*100:.2f}%, 컨티 가동률 {SPEC_UP.seed_ring.continuous['uptime']*100:g}%를 가정합니다. 직업표에는 비실측 자료가 포함되며 개인 캐릭터 실측값이 아닙니다.",
    ]


def attach_spec_up(rows: list, items: list, context: StatContext, class_name: str, main_stat: str, char_level: int,
                   exclude_names=(), damage_total: float = 0.0, weapon: WeaponContext | None = None) -> dict:
    """진단 행마다 축별 강화 행동(row["spec_up"], 효율 내림차순)을 붙이고 전체 상위 행동을 돌려줍니다.
    items 는 진단한 아이템 전체(예비 특수 반지 포함)입니다.
    도전자의 장비·무기류·특수 장비·놀장·이벤트 반지(exclude_names)는 제외하고, 슈페리얼은 스타포스를 빼고 계산합니다.
    무기는 소울 증폭만 봅니다(weapon 이 있을 때)."""
    from app.calculator import max_starforce  # 순환 import 방지

    by_slot = {item.get("item_equipment_slot"): item for item in items}
    damage_per_pct = final_damage_per_pct(context)
    flame_memo: dict = {}
    moment_memo: dict = {}
    cube_memo: dict = {}  # (종류, 부위, 레벨) → 세 줄 구성 (같은 부위·레벨이면 재사용)
    superior_keyword = SCORING.special_items.superior_keyword
    count = 0
    # 쓰는 시드링 레벨. 반지를 바꿔 껴도 쿨타임이 바로 돌아(2026 상반기~) 예비 특수 반지 칸 반지도 함께 씁니다.
    rings = {RESTRAINT: 0, CONTINUOUS: 0}
    for item in items:
        for ring_name in rings:
            if ring_name in (item.get("item_name") or ""):
                rings[ring_name] = max(rings[ring_name], int(float(item.get("special_ring_level") or 0)))
    for row in rows:
        item = by_slot.get(row["slot"])
        row["spec_up"] = []
        # Exclude every upgrade axis, including seed rings, without removing
        # equipped stats from the character's baseline or changing item scores.
        if "도전자의" in (row.get("name") or "") or "도전자의" in ((item or {}).get("item_name") or ""):
            continue
        ring_name = next((name for name in rings if name in row["name"]), None)
        if item and ring_name:
            action = seed_ring_action(row["name"], int(float(item.get("special_ring_level") or 0)), class_name, context,
                                      damage_total, rings[CONTINUOUS])
            row["spec_up"] = [finish(action)] if action else []
            count += len(row["spec_up"])
            continue
        if (not item or row["is_wse"] or row["is_special"] or row.get("is_noljang")
                or any(keyword in row["name"] for keyword in exclude_names)):
            if item and weapon is not None and row["slot"] == "무기" and not any(k in row["name"] for k in exclude_names):
                action = soul_action(item, row["level"], context, weapon, char_level, damage_total)
                row["spec_up"] = [finish(action)] if action else []
                count += len(row["spec_up"])
            continue
        level, part, slot, name = row["level"], row["part"], row["slot"], row["name"]
        superior = superior_keyword in name
        actions = []
        if not superior and item.get("starforce_scroll_flag") != "사용":
            star = int(item.get("starforce") or 0)
            cap = min(max_starforce(level, name, superior), starforce_recommendation_cap(name))
            previous = None
            for target in range(max(12, star) + 1, cap + 1):
                cumulative = starforce_action(name, part, slot, level, star, cap, class_name, main_stat, context, target)
                if not cumulative:
                    break
                action = dict(cumulative)
                if previous:
                    action['final_damage'] = 100 * ((1 + cumulative['final_damage'] / 100) / (1 + previous['final_damage'] / 100) - 1)
                    action['gain'] = action['final_damage'] / damage_per_pct
                    action['star'] = target - 1
                    action['label'] = f"{target - 1}→{target}성"
                actions.append(action)
                previous = cumulative
        benchmarks = (row.get("scoring_basis") or {}).get("benchmarks") or {}
        for kind, key in (("potential", "pot"), ("additional", "pot_additional")):
            actions += potential_actions(item, kind, part, level, context, char_level, benchmarks.get(key), cube_memo)
        if "add" in (row.get("detail") or {}) and item.get("item_add_option"):
            floor = next(entry[0] for entry in SCORING.add_option.flame.bands if level >= entry[0])
            if floor not in flame_memo:
                flame_memo[floor] = flame_distribution(level, class_name, main_stat, context)
            current = calculate_item_score(item["item_add_option"], class_name, main_stat, context)
            steps = flame_steps(flame_memo[floor], current, SPEC_UP.flame.cost * EOK) if context.pct_per_flat > 0 else []
            direct_rewards = None
            try:
                ReplacementModel(context)
                if floor not in moment_memo:
                    moment_memo[floor] = flame_moments(level, class_name, main_stat, context)
                direct_rewards = flame_rewards(moment_memo[floor], item["item_add_option"], context)
            except ValueError:
                pass  # Special-stat classes retain an explicitly labelled legacy estimate.
            unit = SPEC_UP.ranking.unit
            for step in steps:  # 급수 증가를 주스탯% 로, 단계마다 효율
                step["gain"] *= context.pct_per_flat
                step["final_damage"] = (sum(value for grade, value in direct_rewards.items() if grade >= step["threshold"] - 1e-9)
                                        / step["chance"] if direct_rewards is not None else step["gain"] * damage_per_pct)
                step["gain"] = step["final_damage"] / damage_per_pct
                step["efficiency"] = round(step["gain"] / (step["cost"] / unit), 4)
                step["damage_efficiency"] = step["final_damage"] / (step["cost"] / unit)
            if steps:
                best = max(steps, key=lambda step: step["damage_efficiency"])
                if best["final_damage"] <= 0:
                    steps = []
            if steps:
                actions.append({"axis": "추가옵션", "label": f"+{best['plus']:g}급({best['target']:g}급) 이상까지 재설정",
                                "final_damage": best["final_damage"], "model": "direct" if direct_rewards is not None else "legacy_special_stat",
                                "gain": best["gain"], "cost": best["cost"], "chance": best["chance"], "tries": best["tries"],
                                "current_grade": round(current, 1),
                                "steps": [{**step, "gain": round(step["gain"], 2), "cost": round(step["cost"]),
                                           "chance": round(step["chance"], 8), "tries": round(step["tries"], 1)} for step in steps]})
        row["spec_up"] = [finish(a) for a in actions if a]
        count += len(row["spec_up"])
    # 순서(100억당 최종뎀)는 노작·연마석 시세에 따라 바뀌어 화면(app.js specUpActions)이 정합니다.
    return {"unit": SPEC_UP.ranking.unit, "limit": int(SPEC_UP.ranking.top), "stat_label": stat_label(context),
            "estimate": True, "assumptions": recommendation_assumptions(context, class_name),
            "final_damage_per_pct": round(damage_per_pct, 4), "count": count}


def pricing_config() -> dict:
    """화면이 시세로 비용을 계산하는 데 쓰는 설정 (페이지의 window.MECULATOR_PRICING).
    starforce = 확률·비용 분모·이벤트·파괴 방지·복구, base_price = 노작 시세 기본값 표, materials = 연마석·소울 에테르 기본 시세(억)."""
    raw = STARFORCE_COST.raw
    return {"starforce": {key: raw[key] for key in ("event", "odds", "cost_denominator", "prevention", "restore")},
            "base_price": raw["base_price"], "materials": {**SPEC_UP.seed_ring.materials.raw, **SPEC_UP.soul.materials.raw}}
