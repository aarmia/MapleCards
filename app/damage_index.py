"""Shared buffed boss index and fixed-preset option replacement.

This is a stat comparison, not a skill rotation simulator. Option dictionaries
contain raw flat stats and percentage points, never equivalent-main-stat scores.
"""
import math
import re

from app.scoring_config import POTENTIAL


def boss_index(context, damage=0, boss=0, remaining_defense=0, defense=None):
    defense = POTENTIAL.weapon.boss_defense if defense is None else defense  # 방무 환산 기준 보스 방어율 (설정 하나)
    rate = min(100, max(0, context.crit_rate or 0)) / 100
    critical = 1 + rate * (POTENTIAL.crit_base - 1 + context.crit_damage_effective / 100)
    dmg = 1 + (damage + boss + POTENTIAL.buff.damage_pct + POTENTIAL.buff.boss_damage_pct) / 100
    return (max(0, context.stat_attack) * max(0, context.attack_effective) * max(0, dmg)
            * critical * max(0, 1 - defense / 100 * remaining_defense))


def line_options(lines, level):
    result = {}
    for line in lines:
        if not line:
            continue
        match = re.search(r"(\d+)레벨 당 (STR|DEX|INT|LUK) \+(\d+)$", line)
        if match:
            interval, stat, value = match.groups()
            if int(interval) <= 0:
                continue
            key, value = stat.lower(), int(value) * (level // int(interval))
        else:
            match = re.fullmatch(r"(STR|DEX|INT|LUK|올스탯|공격력|마력|크리티컬 데미지|크리티컬 확률) \+(\d+)(%?)", line)
            if not match:
                cooldown = re.search(r"재사용 대기시간 -(\d+)초", line)
                if cooldown:
                    result["cooldown"] = result.get("cooldown", 0) + int(cooldown[1])
                continue
            stat, value, pct = match.groups()
            key = {"올스탯": "all_stat", "공격력": "attack_power", "마력": "magic_power",
                   "크리티컬 데미지": "crit", "크리티컬 확률": "rate"}.get(stat, stat.lower())
            if pct and key not in ("crit", "rate"):
                key += "_pct"
            value = float(value)
        result[key] = result.get(key, 0) + value
    return result


def flame_options(options):
    return {("all_stat_pct" if k == "all_stat" else k): float(v or 0) for k, v in options.items()}


class ReplacementModel:
    """Ratios against one frozen, buffed baseline. Unchanged damage/IED cancel.

    Per-sub-stat bases are preferred. Older/reference contexts use the same
    inferred sub ratios as the existing conversion engine.
    """
    def __init__(self, context):
        if context.main_stat not in ("str", "dex", "int", "luk"):
            raise ValueError("특수 주스탯 직접 비교 미지원")
        self.context = context
        self.stats = [(context.main_stat, context.base_stat_effective, context.stat_pct, context.stat_weight)]
        for label in context.sub_names:
            part = context.sources.get("subs", {}).get(label)
            if part:
                base, mult = part["base"], part["mult"]
            elif len(context.sub_names) == 1 and "sub_base_estimate" in context.sources:
                base, mult = context.sources["sub_base_estimate"], context.sources["sub_multiplier"]
            else:
                base = (context.sub_pct_ratio or POTENTIAL.sub_stat_ratio) * context.stat_weight * context.base_stat
                mult = (context.sub_flat_ratio or POTENTIAL.sub_stat_ratio) * context.stat_weight * context.stat_pct
            self.stats.append((label.lower(), base, mult, 1))
        self.attack_key = "magic_power" if context.main_stat == "int" else "attack_power"
        self.per_pct = context.stat_weight * context.base_stat_effective / context.stat_attack
        if min(context.stat_attack, context.attack_effective, self.per_pct) <= 0:
            raise ValueError("전투 기준 수치 부족")
        # gain() 은 잠재 결과마다 수천 번 불리므로 기준 상태의 값을 한 번만 계산해 둡니다 (수식은 같음).
        self.stat_attack = context.stat_attack
        self.attack_base = context.attack / context.attack_multiplier + POTENTIAL.buff.attack_flat
        self.attack_multiplier = context.attack_multiplier_effective
        self.attack_effective = context.attack_effective
        self.crit_rate = context.crit_rate or 0
        self.crit_extra = POTENTIAL.crit_base - 1
        self.crit_damage = context.crit_damage_effective
        self.critical0 = 1 + min(100, max(0, self.crit_rate)) / 100 * (self.crit_extra + self.crit_damage / 100)
        self.cooldown_value = POTENTIAL.cooldown_per_second * self.per_pct
        self.is_archer = context.is_archer
        self.excess_value = context.pct_per_excess_crit_rate * self.per_pct if context.is_archer else 0.0

    def gain(self, before, after, cooldown=True):
        delta = {k: after.get(k, 0) - before.get(k, 0) for k in before.keys() | after.keys()}
        stat = self.stat_attack
        all_flat, all_pct = delta.get("all_stat", 0), delta.get("all_stat_pct", 0)
        for key, base, mult, weight in self.stats:
            flat = delta.get(key, 0) + all_flat
            pct = (delta.get(key + "_pct", 0) + all_pct) / 100
            stat += weight * ((base + flat) * (mult + pct) - base * mult)
        attack = (self.attack_base + delta.get(self.attack_key, 0)) * (self.attack_multiplier + delta.get(self.attack_key + "_pct", 0) / 100)
        rate_delta = delta.get("rate", 0)
        rate = min(100, max(0, self.crit_rate + rate_delta)) / 100
        critical = 1 + rate * (self.crit_extra + (self.crit_damage + delta.get("crit", 0)) / 100)
        result = 100 * (max(0, stat) / self.stat_attack * max(0, attack) / self.attack_effective * critical / self.critical0 - 1)
        # Explicit legacy models, separate from the direct permanent-stat ratio.
        if cooldown:
            result += delta.get("cooldown", 0) * self.cooldown_value
        if self.is_archer:
            excess = max(0, self.crit_rate + rate_delta - 100) - max(0, self.crit_rate - 100)
            result += excess * self.excess_value
        if not math.isfinite(result):
            raise ValueError("유효하지 않은 데미지 증가율")
        return result
