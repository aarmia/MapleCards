"""Project observable equipment differences without inventing skill or set effects."""
import re
from dataclasses import replace

from app.calculator import get_potential_options
from app.set_projection import analyze_sets, apply_sets


def active_items(items):
    return [item for item in items if item.get("item_equipment_slot") != "예비 특수 반지"]


def flat_potential(items, label, level):
    total = 0.0
    for item in active_items(items):
        lines = [item.get("soul_option") or ""]
        for kind in ("potential", "additional_potential", "soul_potential"):
            lines.extend(get_potential_options(item, kind))
        for line in lines:
            match = re.fullmatch(r"(STR|DEX|INT|LUK|올스탯|공격력|마력) \+(\d+)", line)
            if match and (match[1] == label or match[1] == "올스탯" and label in ("STR", "DEX", "INT", "LUK")):
                total += int(match[2])
            match = re.search(r"(\d+)레벨 당 (STR|DEX|INT|LUK) \+(\d+)$", line)
            if match and match[2] == label and int(match[1]) > 0 and level > 0:
                total += (level // int(match[1])) * int(match[3])
    return total


def flat_delta(equipped, selected, field, label, level):
    equipped, selected = active_items(equipped), active_items(selected)
    complete = bool(equipped and selected) and all(field in (item.get("item_total_option") or {}) for item in equipped + selected)
    item_delta = (sum(float(item["item_total_option"][field] or 0) for item in selected)
                  - sum(float(item["item_total_option"][field] or 0) for item in equipped)) if complete else 0.0
    potential_delta = flat_potential(selected, label, level) - flat_potential(equipped, label, level)
    return {"item": item_delta, "potential": potential_delta, "total": item_delta + potential_delta,
            "item_totals_complete": complete}


def project_context(current, selected, equipped_items, selected_items, level, set_data=None, same_preset=False):
    equipped_items, selected_items = active_items(equipped_items), active_items(selected_items)
    # Identical equipment must retain actual API values, rather than manufacture a projection.
    same = same_preset or equipped_items == selected_items
    set_report = analyze_sets(equipped_items, equipped_items if same else selected_items, set_data)

    def names(items):
        return sorted((item.get("item_equipment_slot", ""), item.get("item_name", "")) for item in items)

    set_status = "unchanged" if same or names(equipped_items) == names(selected_items) else "unresolved"
    sources = {**selected.sources, "set_projection_status": set_status,
               "current_set_effects": (set_data or {}).get("set_effect", []),
               "preset_projection": "equipped" if same else "equipment_and_potential",
               "equipped_gear_pct": current.sources.get("gear_pct")}
    if same:
        return apply_sets(replace(current, sources={**sources, **current.sources}), set_report)
    if current.main_stat not in ("str", "dex", "int", "luk"):
        return replace(current, basis="estimated", sources={**sources, "preset_projection": "unsupported_special_class"})
    main = flat_delta(equipped_items, selected_items, current.main_stat, current.main_label, level)
    base = current.base_stat + main["total"]
    if base <= 0:
        return replace(current, basis="estimated", sources={**sources, "preset_projection": "invalid_base_fallback"})
    attack_label = "마력" if current.main_stat == "int" else "공격력"
    attack_field = "magic_power" if current.main_stat == "int" else "attack_power"
    attack_delta = flat_delta(equipped_items, selected_items, attack_field, attack_label, level)
    attack = (current.attack / current.attack_multiplier + attack_delta["total"]) * selected.attack_multiplier
    if attack <= 0:
        attack = current.attack
        sources["attack_projection_fallback"] = True
    final_sub = current.final_sub
    sub_ratio = current.sub_pct_ratio * current.base_stat / base if current.sub_pct_ratio is not None else None
    if len(current.sub_names) == 1 and "sub_base_estimate" in current.sources:
        label = current.sub_names[0]
        sub = flat_delta(equipped_items, selected_items, label.lower(), label, level)
        sub_base = max(0, current.sources["sub_base_estimate"] + sub["total"])
        sub_mult = selected.sources["sub_multiplier"]
        final_sub = sub_base * sub_mult + current.sources["sub_exempt"]
        sub_ratio = sub_base / (current.stat_weight * base)
        sources.update(preset_sub_flat_delta=sub, sub_base_estimate=sub_base,
                       sub_exempt=current.sources["sub_exempt"])
    else:
        sources["sub_projection_unmodeled"] = True
    sources.update(preset_main_flat_delta=main["total"], preset_main_flat_breakdown=main,
                   preset_attack_flat_delta=attack_delta)
    projected = replace(selected, base_stat=base, final_main=base * selected.stat_pct + current.exempt_stat,
                   exempt_stat=current.exempt_stat, final_sub=final_sub, attack=attack,
                   sub_pct_ratio=sub_ratio, basis="estimated", sources=sources)
    return apply_sets(projected, set_report)
