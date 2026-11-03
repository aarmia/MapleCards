"""Enumerate saved presets using an explicit, approximate boss stat index.

No in-game state is changed. Only directly parseable unconditional effects are
projected; unknown effects stay in the observed baseline, not invented deltas.
"""
import itertools
import math
import re
from dataclasses import replace

from app.calculator import final_stat_float, get_potential_options
from app.character_profile import _effect_stats, build_stat_context
from app.preset_projection import active_items, project_context
from app.scoring_config import POTENTIAL

BOSS_DEFENSE = 380.0
FIELDS = ("main_flat", "main_exempt", "main_pct", "sub_flat", "sub_exempt", "sub_pct",
          "attack", "attack_pct", "damage", "boss", "crit", "rate")


def effects(texts, context, exempt=False):
    result = dict.fromkeys(FIELDS, 0.0)
    result.update(ied=1.0, ignored=[])
    attack = "마력" if context.main_stat == "int" else "공격력"
    patterns = {
        "boss": r"보스 몬스터(?: 공격 시)? 데미지",
        "damage": r"데미지", "crit": r"크리티컬 데미지",
        "rate": r"크리티컬 확률", "ied": r"(?:몬스터 )?방어율 무시|몬스터 방어율 무시",
        "attack": rf"{attack}|공격력(?:과 |/|, )마력",
    }
    for text in texts:
        # Splitting at a comma only before a named combat field preserves STR, DEX lists.
        clauses = re.split(r"\n|,\s*(?=(?:크리티컬|방어율|보스|공격력|마력|데미지|(?:STR|DEX|INT|LUK)\s*\+?\d))", text or "")
        for clause in clauses:
            clause = clause.strip().removeprefix("영구적으로 ")
            if not clause:
                continue
            found = False
            for key, pattern in patterns.items():
                match = re.fullmatch(rf"(?:{pattern})\s*\+?\s*(\d+(?:\.\d+)?)\s*(%?)\s*(?:증가)?", clause)
                if not match:
                    continue
                value = float(match[1])
                if key == "ied" and match[2]:
                    result["ied"] *= 1 - min(value, 100) / 100
                elif key == "attack":
                    result["attack_pct" if match[2] else "attack"] += value
                elif match[2]:
                    result[key] += value
                found = True
                break
            if found:
                continue
            # Only standalone stat clauses: never parse a conditional sentence as a buff.
            if re.fullmatch(r"(?:(?:STR|DEX|INT|LUK|힘|민첩성|민첩|지력|운|올스탯|모든 능력치|모든 스탯|ALLSTAT)[,\s]*)+\+?\s*\d+(?:%)?\s*(?:증가)?", clause):
                for prefix, names in (("main", context.main_names), ("sub", context.sub_names)):
                    if not names:
                        continue
                    flat, pct = _effect_stats([clause], names)
                    result[prefix + ("_exempt" if exempt else "_flat")] += flat
                    result[prefix + "_pct"] += pct
            else:
                result["ignored"].append(clause)
    return result


def group_candidates(extras):
    """Current always remains a candidate; empty/null saved slots are skipped."""
    groups = []
    hyper = extras.get("hyper_stat") or {}

    def hyper_text(rows):
        return [r.get("stat_increase") for r in rows or [] if float(r.get("stat_level") or 0) > 0]

    current = str(hyper.get("use_preset_no") or "current")
    groups.append(("hyper_stat", True, hyper_text(hyper.get(f"hyper_stat_preset_{current}")),
                   [(str(n), hyper_text(hyper.get(f"hyper_stat_preset_{n}"))) for n in range(1, 4) if hyper.get(f"hyper_stat_preset_{n}")]))
    union = extras.get("union_raider") or {}
    if "union_state_stat" in union:
        rows = [(str(r.get("preset_no")), r["union_state_stat"]) for r in union.get("union_state_stat_preset") or [] if r.get("union_state_stat")]
        groups.append(("union", True, union.get("union_state_stat") or [], rows))
    else:
        def union_text(d):
            return list(d.get("union_raider_stat") or []) + list(d.get("union_occupied_stat") or [])
        rows = [(str(n), union_text(union[f"union_raider_preset_{n}"])) for n in range(1, 6) if union.get(f"union_raider_preset_{n}")]
        groups.append(("union", True, union_text(union), rows))
    link = extras.get("link_skill") or {}
    def link_text(suffix=""):
        owned = link.get("character_owned_link_skill" + suffix) or []
        if isinstance(owned, dict):
            owned = [owned]
        return [r.get("skill_effect") for r in list(link.get("character_link_skill" + suffix) or []) + owned]
    groups.append(("link", False, link_text(), [(str(n), link_text(f"_preset_{n}")) for n in range(1, 4) if link.get(f"character_link_skill_preset_{n}")]))
    ability = extras.get("ability") or {}

    def ability_text(d):
        return [r.get("ability_value") for r in d.get("ability_info") or []]

    groups.append(("ability", False, ability_text(ability), [(str(n), ability_text(ability[f"ability_preset_{n}"])) for n in range(1, 4) if (ability.get(f"ability_preset_{n}") or {}).get("ability_info")]))
    return groups


def equipment_effects(items, context):
    texts = []
    for item in active_items(items):
        total = item.get("item_total_option") or {}
        for field, label in (("damage", "데미지"), ("boss_damage", "보스 몬스터 데미지"), ("ignore_monster_armor", "방어율 무시")):
            texts.append(f"{label} +{total.get(field) or 0}%")
        for kind in ("potential", "additional_potential", "soul_potential"):
            texts.extend(get_potential_options(item, kind))
        texts.append(item.get("soul_option") or "")
    return effects(texts, context)


def boss_index(context, damage, boss, remaining_defense, defense=BOSS_DEFENSE):
    rate = min(100, max(0, context.crit_rate or 0)) / 100
    critical = 1 + rate * (POTENTIAL.crit_base - 1 + context.crit_damage / 100)
    defense_factor = max(0, 1 - defense / 100 * remaining_defense)
    return max(0, context.stat_attack) * max(0, context.attack) * max(0, 1 + (damage + boss) / 100) * critical * defense_factor


def optimize_presets(char_class, main_stat, level, item_data, stat_data, extras, current):
    required = [final_stat_float(stat_data, key) for key in ("데미지", "보스 몬스터 데미지", "방어율 무시", "크리티컬 확률", "크리티컬 데미지")]
    if current.basis == "reference" or main_stat not in ("str", "dex", "int", "luk") or len(current.sub_names) != 1 or any(v is None for v in required):
        return None
    extras = extras or {}
    damage, boss, ied, _, _ = required
    equipped = active_items(item_data.get("item_equipment", []))
    equip_current = equipment_effects(equipped, current)
    dimensions, current_effects, unavailable = [], [], []
    for key, exempt, texts, saved in group_candidates(extras):
        baseline = effects(texts, current, exempt)
        current_effects.append(baseline)
        # Prefer the observed current state on exact ties; annotate its stored id when found.
        current_id = next((n for n, rows in saved if sorted(filter(None, rows)) == sorted(filter(None, texts))), "current")
        options = [(current_id, baseline)]
        for n, rows in saved:
            if n != current_id:
                options.append((n, effects(rows, current, exempt)))
        if not saved:
            unavailable.append(key)
        dimensions.append((key, options))
    current_no = item_data.get("preset_no")
    equipment_options = [(current_no, equipped, project_context(current, current, equipped, equipped, level, extras.get("set_effect"), same_preset=True))]
    for n in range(1, 4):
        items = active_items(item_data.get(f"item_equipment_preset_{n}") or [])
        if not items or str(n) == str(current_no):
            continue
        selected = build_stat_context(char_class, main_stat, level, items, stat_data, extras)
        projected = project_context(current, selected, equipped, items, level, extras.get("set_effect"))
        equipment_options.append((n, items, projected))
    baseline_remaining = min(1, max(0, 1 - ied / 100))
    baseline_score = boss_index(current, damage, boss, baseline_remaining)
    best, count = None, 0
    for equipment_no, items, equipment_context in equipment_options:
        equipment = equipment_effects(items, current)
        set_delta = equipment_context.sources.get("set_projection", {}).get("delta", {})
        for combination in itertools.product(*(options for _, options in dimensions)):
            count += 1
            delta = {key: sum(option[1][key] - old[key] for option, old in zip(combination, current_effects)) for key in FIELDS}
            base = equipment_context.base_stat + delta["main_flat"]
            mult = equipment_context.stat_pct + delta["main_pct"] / 100
            exempt = equipment_context.exempt_stat + delta["main_exempt"]
            attack_mult = equipment_context.attack_multiplier + delta["attack_pct"] / 100
            attack = (equipment_context.attack / equipment_context.attack_multiplier + delta["attack"]) * attack_mult
            sub_base = equipment_context.sources.get("sub_base_estimate", 0) + delta["sub_flat"]
            sub_mult = equipment_context.sources.get("sub_multiplier", 1) + delta["sub_pct"] / 100
            sub_exempt = equipment_context.sources.get("sub_exempt", 0) + delta["sub_exempt"]
            if min(base, mult, attack_mult, attack, sub_mult) <= 0:
                continue
            crit = equipment_context.crit_damage + equipment["crit"] - equip_current["crit"] + delta["crit"]
            rate = equipment_context.crit_rate + equipment["rate"] - equip_current["rate"] + delta["rate"]
            candidate = replace(equipment_context, base_stat=base, stat_pct=mult, exempt_stat=exempt,
                final_main=base * mult + exempt, final_sub=max(0, sub_base * sub_mult + sub_exempt),
                attack=attack, attack_multiplier=attack_mult, crit_damage=crit, crit_rate=rate,
                sub_pct_ratio=sub_base / (current.stat_weight * base), sub_flat_ratio=sub_mult / (current.stat_weight * mult),
                sources={**equipment_context.sources, "sub_base_estimate": sub_base, "sub_multiplier": sub_mult, "sub_exempt": sub_exempt})
            old_ied = math.prod([equip_current["ied"], *(old["ied"] for old in current_effects)])
            new_ied = math.prod([equipment["ied"], *(option[1]["ied"] for option in combination)])
            remaining = min(1, max(0, baseline_remaining * new_ied / old_ied * set_delta.get("ied_ratio", 1))) if old_ied > 0 else baseline_remaining
            new_damage = damage + equipment["damage"] - equip_current["damage"] + delta["damage"] + set_delta.get("damage", 0)
            new_boss = boss + equipment["boss"] - equip_current["boss"] + delta["boss"] + set_delta.get("boss", 0)
            score = boss_index(candidate, new_damage, new_boss, remaining)
            if not math.isfinite(score) or best and score <= best[0] * (1 + 1e-12):
                continue
            selected_ids = {"equipment": equipment_no, **{key: option[0] for (key, _), option in zip(dimensions, combination)}}
            is_current = str(equipment_no) == str(current_no) and all(
                option[0] == options[0][0] for option, (_, options) in zip(combination, dimensions)
            )
            ignored = sorted(set(text for source in [*(option[1] for option in combination), *current_effects]
                                 for text in source["ignored"]))
            best = (score, equipment_no, items, candidate, selected_ids, ignored, new_damage, new_boss, remaining, is_current)
    if best is None:
        return None
    score, equipment_no, items, context, ids, ignored, new_damage, new_boss, remaining, is_current = best
    metadata = {"method": "saved_presets_boss_stat_index", "boss_defense_percent": BOSS_DEFENSE,
        "selected": ids, "combinations_evaluated": count, "unavailable": unavailable,
        "relative_index": round(score / baseline_score, 6) if baseline_score > 0 else None,
        "damage": new_damage, "boss_damage": new_boss, "ignore_defense": (1 - remaining) * 100,
        "unmodeled_effects": ignored, "set_projection": context.sources.get("set_projection"),
        "limitations": ["지원 스탯 기준 최적 조합이며 실제 DPS 최적을 보장하지 않습니다", "조건부 효과 가동률·쿨감·버프 지속시간 미보정", "검증된 세트만 보정하며 미등록·불일치 규칙은 미보정", "아티팩트·헥사·직업 스킬은 현재 상태 유지"]}
    # 현재 상태 그대로가 최적이면 실제 API 수치를 쓰므로 추정 표시를 하지 않습니다.
    context = replace(context, basis=context.basis if is_current else "estimated", sources={**context.sources, "preset_optimization": metadata})
    return equipment_no, items, context, metadata
