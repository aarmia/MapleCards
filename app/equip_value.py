"""Marginal item value at a fixed boss preset; independent of grade/benchmark scores."""
import math
from dataclasses import replace

from app.character_profile import _gear_pct
from app.preset_optimizer import boss_index, equipment_effects
from app.preset_projection import flat_potential
from app.set_projection import analyze_sets, apply_sets, membership


def _flat(item, key):
    # Exceptional upgrades can be separate from the API total. Check the component
    # sum before adding them; never add starforce/scroll/flame twice.
    total = float(item["item_total_option"][key])
    ex = float((item.get("item_exceptional_option") or {}).get(key) or 0)
    if ex:
        parts = sum(float(item[k][key]) for k in ("item_base_option", "item_add_option", "item_etc_option", "item_starforce_option"))
        if total == parts:
            return total + ex
        if total != parts + ex:
            raise ValueError("익셉셔널 포함 여부 확인 불가")
    return total


def equivalent(empty, target_index):
    ctx, dmg, boss, rem = empty
    start = boss_index(*empty)
    plus_one = replace(ctx, stat_pct=ctx.stat_pct + .01, final_main=ctx.final_main + ctx.base_stat / 100)
    slope = boss_index(plus_one, dmg, boss, rem) - start
    if slope <= 0 or not math.isfinite(slope):
        raise ValueError("주스탯 1%의 보스 지수 증가량을 계산할 수 없음")
    return (target_index - start) / slope


def shifted(state, delta):
    ctx, dmg, boss, rem = state
    ctx = apply_sets(ctx, {"status": "applied", "delta": delta})
    if ctx.sources["set_projection_status"] != "applied":
        raise ValueError("제거 후 스탯을 복원할 수 없음")
    return ctx, dmg + delta["damage"], boss + delta["boss"], min(1, rem * delta["ied_ratio"])


def item_value(item, selected, actual, context, opt, level, set_data, full_sets):
    if (opt.get("method") != "saved_presets_boss_stat_index" or not context.sub_names
            or context.main_stat not in ("str", "dex", "int", "luk")):
        return {"status": "unavailable", "reason": "프리셋 또는 주·부스탯 복원 미지원"}
    # Single-sub classes use sub_* sources; multi-sub classes (e.g. STR+DEX) use per-stat sources["subs"].
    single = len(context.sub_names) == 1 and "sub_base_estimate" in context.sources
    if not single and set(context.sources.get("subs") or ()) != set(context.sub_names):
        return {"status": "unavailable", "reason": "부스탯 역산 정보 없음"}
    full = context, opt["damage"], opt["boss_damage"], 1 - opt["ignore_defense"] / 100
    # Both reports must be anchored to the actual equipment, not a fictitious API
    # snapshot for the optimized preset.
    bare_sets = analyze_sets(actual, [x for x in selected if x is not item], set_data)
    a, b = full_sets["delta"], bare_sets["delta"]
    loss = {k: (b[k] / a[k] if k == "ied_ratio" else b[k] - a[k]) for k in a}
    item_delta = dict.fromkeys(a, 0.0)
    effects = equipment_effects([item], context)
    if not effects["ied"]:
        raise ValueError("장비 방무를 역산할 수 없음")
    item_delta.update(ied_ratio=1 / effects["ied"], damage=-effects["damage"], boss=-effects["boss"],
                      crit=-effects["crit"], rate=-effects["rate"])
    for label in (context.main_label, *context.sub_names):
        key = label.lower()
        item_delta[key] = -(_flat(item, key) + flat_potential([item], label, level))
        item_delta[key + "_pct"] = -_gear_pct([item], key, (label,))
    magic = context.main_stat == "int"
    attack = "magic" if magic else "attack"
    item_delta[attack] = -(_flat(item, attack + "_power") + flat_potential([item], "마력" if magic else "공격력", level))
    item_delta[attack + "_pct"] = -effects["attack_pct"]
    # A set-less item is known to contribute 0 only when every API set is explained by
    # the registered table; otherwise it could be an unregistered set member.
    unresolved = any(r["reasons"] for r in (full_sets, bare_sets))
    if membership(item):
        partial = unresolved
    else:
        partial = unresolved or bool(full_sets["preserved_sets"])
        loss = None
    without_sets = full if partial or loss is None else shifted(full, loss)
    empty = shifted(without_sets, item_delta)
    own = equivalent(empty, boss_index(*without_sets))
    total = equivalent(empty, boss_index(*full))
    return {"status": "partial" if partial else "estimated", "stat": context.main_label,
            "item": round(own, 2), "set": None if partial else round(total - own, 2),
            "total": None if partial else round(total, 2),
            "reason": "세트 소속·단계를 확정할 수 없어 자체 가치만 표시" if partial else ""}


def attach_values(rows, selected, actual, profile, level, set_data):
    opt = profile.get("optimization", {})
    full_sets = analyze_sets(actual, selected, set_data)
    by_slot = {x.get("item_equipment_slot"): x for x in selected}
    for row in rows:
        item = by_slot.get(row["slot"])
        if not item or row["is_wse"] or row["is_special"]:
            row["equip_value"] = {"status": "unavailable", "reason": "무기류·특수 장비는 빈 슬롯 비교 대상이 아닙니다"}
            continue
        try:
            row["equip_value"] = item_value(item, selected, actual, profile["context"], opt, level, set_data, full_sets)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            row["equip_value"] = {"status": "unavailable", "reason": "장비 제거 전후 스탯을 확정할 수 없습니다"}
