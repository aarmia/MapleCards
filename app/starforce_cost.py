"""스타포스 기대 비용 (app/data/starforce_cost.toml). starforce.gg/ev 와 같은 계산입니다.

- 이벤트는 샤타(15~16 미포함) 고정: 비용 × 0.7, 21성 이하 파괴 확률 × 0.7, 복구 메소 × 0.8.
- 실패하면 성이 유지됩니다. 파괴되면 노작을 새로 사서 12성부터 다시 올리거나(기본), 확정 복구(같은 아이템 + 메소)를 합니다.
- 성마다 파괴 방지(15~17)·확정 복구·그냥 강화 중 기대 비용이 가장 싼 쪽을 고릅니다 (optimal_plan).
- 12성 미만은 계산하지 않습니다(비용 0).
"""

import math
from functools import lru_cache

from app.scoring_config import STARFORCE_COST

FIRST_STAR, LAST_STAR = 12, 30
EOK = 100_000_000  # 1억 메소


def _round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def step(star: int, level: int, prevent: bool = False) -> tuple[float, float, int]:
    """star 성에서 한 번 누를 때의 (성공 확률, 파괴 확률, 메소)."""
    config = STARFORCE_COST
    success, _fail, destroy = config.odds[str(star)]
    if star <= config.event.destroy_max_star:
        destroy *= config.event.destroy_multiplier
    if prevent:
        destroy = 0.0
    base = _round_half_up((1000 + level ** 3 * (star + 1) ** 2.7 / config.cost_denominator[str(star)]) / 100) * 100
    cost = base * config.event.cost_multiplier + (2 * base if prevent else 0)
    return success, destroy, _round_half_up(cost)


def _restore_cost(star: int, level: int, base_price: float) -> float | None:
    meso = (STARFORCE_COST.restore.meso.get(str(level)) or {}).get(str(star))
    if meso is None:
        return None
    return base_price * STARFORCE_COST.restore.items[str(star)] + meso * STARFORCE_COST.event.restore_multiplier


@lru_cache(maxsize=512)
def optimal_plan(level: int, base_price: float) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(파괴 방지할 성, 확정 복구할 성). 15~17성은 셋 중 최소, 18~22성은 복구가 다시 올리기보다 싸면 복구."""
    prevent_stars = tuple(STARFORCE_COST.prevention.stars)
    cumulative, prevent, restore = 0.0, [], []
    for star in range(FIRST_STAR, 23):
        success, destroy, cost = step(star, level)
        redo = (destroy * (base_price + cumulative) + cost) / success
        options = [(redo, None)]
        restore_cost = _restore_cost(star, level, base_price) if star >= 15 else None
        if restore_cost is not None:
            options.append(((destroy * restore_cost + cost) / success, "restore"))
        if star in prevent_stars:
            p_success, p_destroy, p_cost = step(star, level, prevent=True)
            options.append(((p_destroy * (base_price + cumulative) + p_cost) / p_success, "prevent"))
        # 같으면 파괴 방지 → 복구 → 그냥 강화 순 (사이트와 같은 순서)
        best, choice = min(options, key=lambda option: (option[0], {"prevent": 0, "restore": 1, None: 2}[option[1]]))
        if choice == "prevent":
            prevent.append(star)
        elif choice == "restore":
            restore.append(star)
        cumulative += best
    return tuple(prevent), tuple(restore)


@lru_cache(maxsize=512)
def step_costs(level: int, base_price: float) -> dict[int, float]:
    """성마다 star → star + 1 의 기대 메소 (12~29성). 파괴 후 다시 올리는 비용까지 포함합니다."""
    prevent, restore = optimal_plan(level, base_price)
    costs: dict[int, float] = {}
    cumulative = 0.0
    restored_22 = None  # 22성에서 복구하면 23성 이상에서 파괴돼도 22성으로 복구 후 다시 올립니다
    since_22 = 0.0
    for star in range(FIRST_STAR, LAST_STAR):
        success, destroy, cost = step(star, level, prevent=star in prevent)
        restore_cost = _restore_cost(star, level, base_price) if star in restore else None
        if restore_cost is not None:
            expected = (destroy * restore_cost + cost) / success
            if star == 22:
                restored_22, since_22 = restore_cost, 0.0
        elif restored_22 is not None:
            expected = (destroy * (restored_22 + since_22) + cost) / success
        else:
            expected = (destroy * (base_price + cumulative) + cost) / success
        costs[star] = expected
        cumulative += expected
        since_22 += expected
    return costs


def expected_cost(level: int, base_price: float, start: int, end: int) -> float:
    """start 성에서 end 성까지의 기대 메소. 12성 미만 구간은 0 으로 봅니다."""
    costs = step_costs(level, float(base_price))
    return sum(costs[star] for star in range(max(start, FIRST_STAR), min(end, LAST_STAR)))


def base_price(name: str, level: int, part: str = "", slot: str = "") -> tuple[float, bool]:
    """노작 시세 기본값(메소)과 등록 여부. 등록되지 않은 아이템은 레벨별 기본값입니다.
    parts 는 부위(모자 등) 또는 장착 칸(무기·보조무기)과 비교합니다. 무기류는 부위가 무기 종류(단검 등)라서입니다."""
    for item in STARFORCE_COST.base_price.item:
        if item["name"] in (name or "") and (not item.get("parts") or part in item["parts"] or slot in item["parts"]):
            return item["price"] * EOK, True
    price = next(price for floor, price in STARFORCE_COST.base_price.default if level >= floor)
    return price * EOK, False
