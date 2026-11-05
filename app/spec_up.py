"""스펙업 추천: 강화 행동마다 기대 비용 100억 메소당 주스탯% 증가량(효율)을 계산합니다 (app/data/spec_up.toml).

행동은 아이템마다 축별로 하나씩 만듭니다 (방어구·장신구만, 무기류는 이후 단계).
- 스타포스: 다음 1성. 증가량 = 성별 증가표(scoring.toml starforce.gain)의 주스탯% 환산 차이, 비용 = starforce_cost 기대값.
- 윗잠재·에디셔널: 메소 재설정으로 "환산 X% 이상"이 나올 때까지. 공식 줄별 확률(potential_odds.json)로 세 줄 합계 분포를 만들고,
  목표 X 마다 성공 확률 P 와 성공 시 기대값 E[V | V ≥ X] 를 구해 효율이 가장 높은 X 를 고릅니다.
  레전드리 미만이면 등급업 비용(천장 포함)을 더합니다.
- 추가옵션: 메소 재설정으로 "N급 이상"이 나올 때까지. 19종 중 4종(중복 없음) × 단계 확률로 급수 분포를 정확히 계산하고
  같은 방식으로 목표를 고릅니다.
증가량은 모두 캐릭터별 주스탯% 환산(StatContext)이라 축끼리 비교할 수 있습니다.
"""

import itertools
import json
import math

from app.calculator import calculate_item_score, starforce_option
from app.potential import StatContext, analyze_lines, convert_line
from app.scoring_config import DATA_DIR, POTENTIAL, SCORING, SPEC_UP
from app.starforce_cost import EOK, base_price, expected_cost

ODDS = json.loads((DATA_DIR / "potential_odds.json").read_text(encoding="utf-8"))
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
                min_target: float = float("-inf"), max_target: float = float("inf")):
    """목표 X(min_target ≤ X ≤ max_target) 마다 (기대 증가량 ÷ 기대 비용)을 계산해 가장 좋은 X 를 고릅니다.
    기대 증가량은 X 이상 결과 전체(max_target 을 넘는 결과 포함)의 평균 − 현재입니다.
    first_try_free: 등급업 직후 첫 레전드리 결과도 성공일 수 있어 추가 재설정이 (1 − P) / P 회입니다."""
    best, tail_p, tail_v = None, 0.0, 0.0
    for value in sorted(dist, reverse=True):
        tail_p += dist[value]
        tail_v += dist[value] * value
        if tail_p <= 0 or not (min_target - 1e-9 <= value <= max_target + 1e-9):
            continue
        gain = tail_v / tail_p - current
        if gain <= 1e-9:
            continue
        tries = (1 - tail_p) / tail_p if first_try_free else 1 / tail_p
        cost = setup_cost + tries * cost_per_try
        if cost <= 0:
            continue
        efficiency = gain / cost
        if best is None or efficiency > best["efficiency"]:
            best = {"target": value, "chance": tail_p, "gain": gain, "cost": cost, "tries": tries, "efficiency": efficiency}
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
            steps.append({"plus": round(target - current, 1), "target": round(target, 1), "chance": chance,
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
            key = text if value > 0 else None
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
    for name in names:
        prefix_label, value_of = VARIANTS[name]
        dist: dict = {}
        for _texts, value, cooldown, prob in outcomes:
            variant_value = value_of(value, cooldown)
            slot_value = variant_value if variant_value is not None else float("-inf")
            dist[slot_value] = dist.get(slot_value, 0.0) + prob
        best = best_target(dist, current, legendary_cost, setup, first_try_free=not legendary,
                           min_target=current + cube["min_step"], max_target=cap if cap is not None else float("inf"))
        if not best:
            continue
        hits = [(texts, prob) for texts, value, cooldown, prob in outcomes
                if (value_of(value, cooldown) if value_of(value, cooldown) is not None else float("-inf")) >= best["target"] - 1e-9]
        top_texts, top_prob = max(hits, key=lambda hit: hit[1])
        shown = best["target"]
        if name == "cooldown":  # 표시는 쿨감 1초 몫을 뺀 주스탯% (판정·효율은 쿨감 포함 환산값 그대로)
            converted = convert_line("스킬 재사용 대기시간 -1초", context, char_level)
            shown = round(shown - (converted[0] if converted else 0.0), 2)
        actions.append({"axis": KIND_LABEL[kind], "variant": name,
                        "label": f"{via}{prefix_label}환산 {shown:g}% 이상까지 재설정",
                        "combo": _combo_text(top_texts), "combo_share": round(top_prob / best["chance"], 4),
                        "gain": best["gain"], "cost": best["cost"], "chance": best["chance"], "tries": best["tries"]})
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
    for key in flame.options:
        dist: dict = {}
        for tier, chance in zip(tiers, flame.tier_odds):
            grade = round(calculate_item_score(_flame_option(key, tier, level), class_name, main_stat, context), 1)
            dist[grade] = dist.get(grade, 0.0) + chance
        for k in range(flame.lines, 0, -1):
            if dp[k - 1]:
                for value, weight in _convolve(dp[k - 1], dist, 1).items():
                    dp[k][value] = dp[k].get(value, 0.0) + weight
    combos = math.comb(len(flame.options), flame.lines)
    return {value: weight / combos for value, weight in dp[flame.lines].items()}


def starforce_action(name: str, part: str, slot: str, level: int, star: int, max_star: int,
                     class_name: str, main_stat: str, context: StatContext) -> dict | None:
    """다음 1성 강화. 12성 미만은 비용 데이터가 없어 만들지 않습니다."""
    if star < 12 or star + 1 > max_star:
        return None
    now = starforce_option(level, star, main_stat, context.sub_names, part)
    nxt = starforce_option(level, star + 1, main_stat, context.sub_names, part)
    if not now or not nxt:
        return None
    gain = (calculate_item_score(nxt, class_name, main_stat, context)
            - calculate_item_score(now, class_name, main_stat, context)) * context.pct_per_flat
    price, registered = base_price(name, level, part, slot)
    cost = expected_cost(level, price, star, star + 1)
    if gain <= 0 or cost <= 0:
        return None
    return {"axis": "스타포스", "label": f"{star}→{star + 1}성", "gain": gain, "cost": cost,
            "base_price": price, "base_price_registered": registered, "star": star, "level": level}


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
    price = ring.materials[step["material"]] * EOK
    return {"axis": "시드링", "label": f"{level}→{level + 1}레벨 ({step['material']} {step['count']}개 + {step['meso'] / EOK:g}억)",
            "detail": detail, "gain": final_damage / per_pct, "cost": step["count"] * price + step["meso"],
            "material": {"name": step["material"], "count": step["count"], "meso": step["meso"], "price": price}}


def finish(action: dict) -> dict:
    """효율(100억당 주스탯%)을 붙이고 화면용으로 반올림합니다."""
    out = dict(action)
    out["efficiency"] = round(action["gain"] / (action["cost"] / SPEC_UP.ranking.unit), 4)
    out["gain"] = round(action["gain"], 2)
    out["cost"] = round(action["cost"])
    if "chance" in out:
        out["chance"] = round(out["chance"], 6)
        out["tries"] = round(out["tries"], 1)
    return out


def attach_spec_up(rows: list, items: list, context: StatContext, class_name: str, main_stat: str, char_level: int,
                   exclude_names=(), damage_total: float = 0.0) -> dict:
    """진단 행마다 축별 강화 행동(row["spec_up"], 효율 내림차순)을 붙이고 전체 상위 행동을 돌려줍니다.
    items 는 진단한 아이템 전체(예비 특수 반지 포함)입니다.
    무기류·특수 장비·놀장·이벤트 반지(exclude_names)는 제외하고, 슈페리얼은 스타포스를 빼고 계산합니다."""
    from app.calculator import max_starforce  # 순환 import 방지

    by_slot = {item.get("item_equipment_slot"): item for item in items}
    damage_per_pct = final_damage_per_pct(context)
    flame_memo: dict = {}
    cube_memo: dict = {}  # (종류, 부위, 레벨) → 세 줄 구성 (같은 부위·레벨이면 재사용)
    superior_keyword = SCORING.special_items.superior_keyword
    ranked = []
    # 쓰는 시드링 레벨. 반지를 바꿔 껴도 쿨타임이 바로 돌아(2026 상반기~) 예비 특수 반지 칸 반지도 함께 씁니다.
    rings = {RESTRAINT: 0, CONTINUOUS: 0}
    for item in items:
        for ring_name in rings:
            if ring_name in (item.get("item_name") or ""):
                rings[ring_name] = max(rings[ring_name], int(float(item.get("special_ring_level") or 0)))
    for row in rows:
        item = by_slot.get(row["slot"])
        row["spec_up"] = []
        ring_name = next((name for name in rings if name in row["name"]), None)
        if item and ring_name:
            action = seed_ring_action(row["name"], int(float(item.get("special_ring_level") or 0)), class_name, context,
                                      damage_total, rings[CONTINUOUS])
            row["spec_up"] = [finish(action)] if action else []
            for action in row["spec_up"]:
                action["final_damage"] = round(action["gain"] * damage_per_pct, 2)
            ranked += [{**action, "slot": row["slot"], "name": row["name"], "icon": row.get("icon")} for action in row["spec_up"]]
            continue
        if (not item or row["is_wse"] or row["is_special"] or row.get("is_noljang")
                or any(keyword in row["name"] for keyword in exclude_names)):
            continue
        level, part, slot, name = row["level"], row["part"], row["slot"], row["name"]
        superior = superior_keyword in name
        actions = []
        if not superior and item.get("starforce_scroll_flag") != "사용":
            actions.append(starforce_action(name, part, slot, level, int(item.get("starforce") or 0),
                                            max_starforce(level, name, superior), class_name, main_stat, context))
        benchmarks = (row.get("scoring_basis") or {}).get("benchmarks") or {}
        for kind, key in (("potential", "pot"), ("additional", "pot_additional")):
            actions += potential_actions(item, kind, part, level, context, char_level, benchmarks.get(key), cube_memo)
        if "add" in (row.get("detail") or {}) and item.get("item_add_option"):
            floor = next(entry[0] for entry in SCORING.add_option.flame.bands if level >= entry[0])
            if floor not in flame_memo:
                flame_memo[floor] = flame_distribution(level, class_name, main_stat, context)
            current = calculate_item_score(item["item_add_option"], class_name, main_stat, context)
            steps = flame_steps(flame_memo[floor], current, SPEC_UP.flame.cost * EOK) if context.pct_per_flat > 0 else []
            unit = SPEC_UP.ranking.unit
            for step in steps:  # 급수 증가를 주스탯% 로, 단계마다 효율
                step["gain"] *= context.pct_per_flat
                step["efficiency"] = round(step["gain"] / (step["cost"] / unit), 4)
            if steps:
                best = max(steps, key=lambda step: step["efficiency"])
                actions.append({"axis": "추가옵션", "label": f"+{best['plus']:g}급({best['target']:g}급) 이상까지 재설정",
                                "gain": best["gain"], "cost": best["cost"], "chance": best["chance"], "tries": best["tries"],
                                "current_grade": round(current, 1),
                                "steps": [{**step, "gain": round(step["gain"], 2), "cost": round(step["cost"]),
                                           "chance": round(step["chance"], 8), "tries": round(step["tries"], 1)} for step in steps]})
        row["spec_up"] = sorted((finish(a) for a in actions if a), key=lambda a: a["efficiency"], reverse=True)
        for action in row["spec_up"]:  # 이 강화로 오르는 최종 데미지(%)
            action["final_damage"] = round(action["gain"] * damage_per_pct, 2)
        ranked += [{**action, "slot": slot, "name": name, "icon": row.get("icon")} for action in row["spec_up"]]
    ranked.sort(key=lambda a: a["efficiency"], reverse=True)
    limit = int(SPEC_UP.ranking.top)
    return {"unit": SPEC_UP.ranking.unit, "limit": limit, "stat_label": stat_label(context),
            "final_damage_per_pct": round(damage_per_pct, 4), "top": ranked[:limit], "count": len(ranked)}
