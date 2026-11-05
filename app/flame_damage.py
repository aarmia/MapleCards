"""Exact conditional stat-product moments, preserving the existing flame grade bins.

The index polynomial is cubic at most: (A + X + Y*p + Z*p)*(T+a).
Keep its ten downward-closed moments per grade instead of enumerating a million
full flame results per item. Probability and grade rounding match flame_distribution.
"""
import math

from app.calculator import calculate_item_score
from app.damage_index import ReplacementModel
from app.scoring_config import SPEC_UP

MASKS = (0, 1, 2, 4, 8, 6, 9, 10, 12, 14)  # 1, X, p, Z, a, Zp, Xa, pa, Za, Zpa
POSITIONS = {mask: i for i, mask in enumerate(MASKS)}


def vector(model, option):
    x = z = 0.0
    for key, _, mult, weight in model.stats:
        flat = float(option.get(key, 0))
        x += weight * mult * flat
        z += weight * flat
    return (x, float(option.get("all_stat", 0)) / 100, z,
            float(option.get(model.attack_key, 0)) * model.context.attack_multiplier_effective)


def distribution(level, class_name, main_stat, context):
    from app.spec_up import _flame_option
    model = ReplacementModel(context)
    config = SPEC_UP.flame
    dp = [{0.0: [1.0] + [0.0] * 9}] + [{} for _ in range(config.lines)]
    for key in config.options:
        choices = []
        for tier, prob in zip(config.tiers, config.tier_odds):
            option = _flame_option(key, tier + config.boss_bonus, level)
            grade = round(calculate_item_score(option, class_name, main_stat, context), 1)
            vec = vector(model, option)
            shifts = []
            for mask in MASKS:
                terms = []
                for sub in MASKS:
                    if sub & mask != sub:
                        continue
                    coefficient = prob
                    for bit in range(4):
                        if (mask ^ sub) & (1 << bit):
                            coefficient *= vec[bit]
                    if coefficient:
                        terms.append((POSITIONS[sub], coefficient))
                shifts.append(terms)
            choices.append((grade, shifts))
        for k in range(config.lines, 0, -1):
            target = dp[k]
            for grade, moments in dp[k - 1].items():
                for add, shifts in choices:
                    total = round(grade + add, 1)
                    dest = target.setdefault(total, [0.0] * 10)
                    for i, terms in enumerate(shifts):
                        dest[i] += sum(moments[j] * coefficient for j, coefficient in terms)
    combinations = math.comb(len(config.options), config.lines)
    return {grade: [v / combinations for v in moments] for grade, moments in dp[config.lines].items()}


def rewards(moments, before, context):
    """Return {grade: probability * expected final damage gain at that grade}."""
    model = ReplacementModel(context)
    x0, p0, z0, a0 = vector(model, before)
    c = context
    y = sum(base * weight for _, base, _, weight in model.stats)
    empty_a = c.stat_attack - x0 - y * p0 + z0 * p0
    empty_t = c.attack_effective - a0
    y -= z0
    coefficients = (empty_a * empty_t, empty_t, y * empty_t, -p0 * empty_t,
                    empty_a, empty_t, 1, y, -p0, 1)
    baseline = c.stat_attack * c.attack_effective
    return {grade: 100 * (sum(a * b for a, b in zip(coefficients, row)) / baseline - row[0])
            for grade, row in moments.items()}
