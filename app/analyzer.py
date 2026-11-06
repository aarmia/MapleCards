import logging
import operator
import re
from collections import Counter

from app.calculator import (
    add_option_entries,
    analyze_potential_score,
    calculate_item_score,
    calculate_potential_score,
    final_stat_float,
    flame_best_add_option,
    get_main_stat,
    get_potential_options,
    max_starforce,
    noljang_option,
    starforce_benchmark_option,
    starforce_option,
    weapon_starforce_option,
)
from app.character_profile import build_character_profile
from app.combat_power import adjust_combat_power
from app.crit_damage import growth_crit_damage
from app.equip_value import attach_values
from app.potential import StatContext, best_armor_total, reference_context
from app.preset_optimizer import optimize_presets
from app.preset_projection import active_items, project_context
from app.scoring_config import GRADES, POTENTIAL, SCORING, load_guides
from app.spec_up import attach_spec_up, seed_ring_value
from app.special import (
    analyze_special_lines,
    best_total,
    is_seed_ring,
    special_lines,
    title_item,
)
from app.weapon import (
    WeaponContext,
    analyze_weapon_item,
    best_lines_total,
    part_group,
    tier_of,
    weapon_add_best,
    weapon_add_value,
    weapon_attack_tier,
)

logger = logging.getLogger(__name__)

# 가이드 문구의 "[태그]" 부분. 프론트가 정규식으로 다시 파싱하지 않도록 서버에서 분리합니다.
_GUIDE_TAG_PATTERN = re.compile(r"\[(.*?)\]")
DEFAULT_GUIDE_TAG = "INFO"


def split_guide(guide_text: str) -> tuple[str, str]:
    """가이드 문구를 (태그, 본문)으로 나눕니다. 태그가 없으면 기본 태그를 돌려줍니다."""
    text = (guide_text or "").strip()
    match = _GUIDE_TAG_PATTERN.search(text)
    if not match:
        return DEFAULT_GUIDE_TAG, text

    tag = match.group(1).strip() or DEFAULT_GUIDE_TAG
    message = (text[: match.start()] + text[match.end():]).strip()
    message = re.sub(r"\s{2,}", " ", message)
    return tag, message


class UnsupportedOptionRegistry:
    """점수에 반영하지 못한 옵션 문자열을 프로세스 단위로 모읍니다.

    처음 보는 옵션만 WARNING 한 줄로 알리고, 나머지는 집계해 두었다가 주기 요약(main.unsupported_summary_loop)으로 남깁니다.
    요청마다 수십 줄씩 찍히던 경고를 없애면서도 새 옵션은 놓치지 않기 위한 구조입니다.
    """

    def __init__(self):
        self.counter: Counter = Counter()
        self._seen: set[tuple[str, str]] = set()

    def record(self, item_name, potential_type, option, collector: Counter | None = None):
        key = (potential_type, option)
        self.counter[key] += 1
        if collector is not None:
            collector[key] += 1
        if key not in self._seen:
            self._seen.add(key)
            logger.warning(
                "unsupported_option_first_seen",
                extra={"item": item_name, "potential_type": potential_type, "option": option},
            )

    def summary(self, top: int = 20) -> dict:
        return {
            "total": sum(self.counter.values()),
            "distinct": len(self.counter),
            "top": [
                {"type": potential_type, "option": option, "count": count}
                for (potential_type, option), count in self.counter.most_common(top)
            ],
        }

    def reset(self):
        self.counter.clear()
        self._seen.clear()


unsupported_registry = UnsupportedOptionRegistry()


def _log_unsupported_options(item_name, potential_type, analysis, collector=None):
    for option in analysis.get("unsupported", []):
        unsupported_registry.record(item_name, potential_type, option, collector)


def get_best_preset(
    item_data: dict, class_name: str, char_level: int, main_stat: str | None = None, context: StatContext | None = None
) -> int:
    preset_scores = {1: 0.0, 2: 0.0, 3: 0.0}

    for i in range(1, 4):
        preset_items = item_data.get(f"item_equipment_preset_{i}", [])
        if not preset_items:
            continue

        total_score = 0.0
        for item in preset_items:
            score = calculate_potential_score(item, "potential", class_name, char_level, main_stat=main_stat, context=context)
            add_score = calculate_potential_score(item, "additional_potential", class_name, char_level, main_stat=main_stat, context=context)

            if score > 0: total_score += score
            if add_score > 0: total_score += add_score

        preset_scores[i] = total_score

    return max(preset_scores, key=preset_scores.get)


# ── 가이드 규칙 (app/data/guides.toml) ──
# 규칙 표가 참조할 수 있는 필드. build_guide_context 가 만드는 키와 같아야 하며, 로더가 기동 시 대조합니다.
GUIDE_FIELDS = {
    "star", "star_next", "max_star", "at_cap", "level", "noljang", "limited",
    "add_evaluated", "star_evaluated", "worst", "next", "min_ratio", "target_star_330", "target_star_17",
    "target_star_21", "ratio", "add_ratio", "pot_ratio", "eddy_ratio",
}
SPECIAL_GUIDE_FIELDS = {"total", "expired"}
GUIDES = load_guides(rule_fields=GUIDE_FIELDS, special_fields=SPECIAL_GUIDE_FIELDS)

_OPERATORS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def _rule_matches(rule: dict, context: dict, names: dict, item_name: str, part_name: str) -> bool:
    name_any = rule.get("name_any")
    if name_any is not None:
        keywords = names[name_any] if isinstance(name_any, str) else name_any
        if not any(keyword in item_name for keyword in keywords):
            return False
    part_any = rule.get("part_any")
    if part_any is not None and not any(keyword in part_name for keyword in part_any):
        return False
    for field, op, value in rule.get("when", []):
        other = context[value] if isinstance(value, str) else value
        if not _OPERATORS[op](context[field], other):
            return False
    return True


def select_guide(rules: list, context: dict, names: dict, item_name: str, part_name: str = "") -> str:
    """위에서부터 조건이 모두 맞는 첫 규칙의 문구를 돌려줍니다. 마지막 규칙은 조건이 없어 항상 맞습니다."""
    for rule in rules:
        if _rule_matches(rule, context, names, item_name, part_name):
            return rule["text"].format(**context)
    raise ValueError("가이드 규칙에 기본 규칙이 없습니다")


def build_guide_context(axes, star_val, part_name, item_name, ratio, item_req_level, is_noljang) -> dict:
    """규칙 표가 비교할 값들. axes = 축 → (현재 %p, 만점 %p), ratio = 장비 만점 대비 %.
    파생값(최대 스타포스, 가장 약한 축, 목표치)은 코드가 맡고 규칙 표는 조건과 문구만 가집니다."""
    max_star = max_starforce(item_req_level, item_name, any(k in item_name for k in GUIDES.names.superior))
    config = GUIDES.axes
    add_evaluated = not any(k in part_name for k in config.no_add_parts)
    star_evaluated = not any(k in part_name for k in config.no_star_parts)
    keys = ("add", "pot", "pot_additional", "star")   # guides.toml axes.labels 순서
    ratios = {key: axes[key][0] / axes[key][1] if axes.get(key) and axes[key][1] > 0 else 0.0 for key in keys}
    # 가장 약한 축: 윗잠·에디·추옵·스타포스 순으로 비교 (같으면 앞의 축)
    evaluated = [key for key in ("pot", "pot_additional", "add", "star")
                 if key in axes and (key != "add" or add_evaluated) and (key != "star" or star_evaluated)]
    ranked = sorted(((ratios[key], config.labels[keys.index(key)]) for key in evaluated), key=lambda entry: entry[0]) \
        or [(0.0, config.labels[1])]
    targets = GUIDES.targets
    return {
        "star": star_val, "star_next": star_val + 1, "max_star": max_star,
        "at_cap": star_val >= SCORING.starforce.base_star or star_val >= max_star,
        "level": item_req_level, "noljang": is_noljang,
        "limited": any(keyword in item_name for keyword in GUIDES.names.limited),
        "add_evaluated": add_evaluated, "star_evaluated": star_evaluated,
        "worst": ranked[0][1], "next": ranked[1][1] if len(ranked) > 1 else ranked[0][1], "min_ratio": ranked[0][0],
        "ratio": ratio, "add_ratio": ratios["add"], "pot_ratio": ratios["pot"], "eddy_ratio": ratios["pot_additional"],
        "target_star_330": min(
            targets.star_330_low if item_req_level <= targets.star_330_level_split else targets.star_330_high, max_star
        ),
        "target_star_17": min(targets.star_mid, max_star),
        "target_star_21": targets.star_step if max_star >= targets.star_step else max_star,
    }


def exceptional_axis(item, part_name):
    """익셉셔널 강화 횟수: 대상 부위(potential.toml [exceptional].parts)면 횟수, 아니면 None"""
    if not any(keyword in (part_name or "") for keyword in POTENTIAL.exceptional.parts):
        return None
    try:
        return int((item.get("item_exceptional_option") or {}).get("exceptional_upgrade") or 0)
    except (TypeError, ValueError):
        return 0


def get_special_part_guide(total_score, part_name, item_name, expired=False):
    return select_guide(GUIDES.special_rule, {"total": total_score, "expired": expired}, GUIDES.names.raw, item_name, part_name)


def get_dynamic_guide(axes, star_val, part_name, item_name, ratio, item_req_level, is_noljang=False):
    context = build_guide_context(axes, star_val, part_name, item_name, ratio, item_req_level, is_noljang)
    return select_guide(GUIDES.rule, context, GUIDES.names.raw, item_name)


AXIS_LABEL = {"base": "기본 옵션", "scroll": "주문서", "add": "추가옵션", "star": "스타포스", "pot": "윗잠재", "pot_additional": "에디셔널",
              "soul": "소울", "special": "고유 옵션", "exceptional": "익셉셔널"}   # 점수 축과 표시 순서


def evaluate_equipment(items, char_class, char_level, unsupported=None, main_stat=None, profile=None):
    evaluate_list = []
    special_items = SCORING.special_items
    noljang = SCORING.noljang
    # 주스탯은 호출자(build_report)가 스탯 응답까지 보고 정해 넘깁니다. 없으면 직업명으로 정합니다.
    main_stat = main_stat or get_main_stat(char_class)
    # 잠재 환산 기준. build_report 가 캐릭터 스탯으로 만든 것이 없으면 기준 캐릭터(potential.toml reference)를 씁니다.
    context = profile["context"] if profile else reference_context(char_class, main_stat)
    weapon_context = (profile or {}).get("weapon") or WeaponContext(context)

    # 점수 = 축마다 주스탯 %p 로 환산한 값의 합. 만점(같은 단위)은 테두리 색(만점 대비 %)·가이드 구간·총평에 씁니다.
    per_flat = context.pct_per_flat            # 고정 주스탯 1 = 유효 스탯 %  → 급수 × per_flat = %p
    per_unit = weapon_context.stat_pct_unit    # 유효 스탯 1% = 공 % 단위     → 무기류 공% ÷ per_unit = %p
    damage_total = weapon_context.damage + weapon_context.boss_damage

    def weight_notes():
        notes = [
            "점수 = 축마다 주스탯 %p 로 환산한 값의 합 · 테두리 색 = 만점 대비 %",
            f"추가옵션·스타포스: 급수(고정 주스탯 1 = 1급) × {per_flat:.3g}%p",
            "윗잠재·에디셔널: 줄마다 유효 스탯% (주스탯 1% = 1%p)",
            "만점: 기본 옵션 = 그대로 · 주문서 = 횟수 × 최상 주문서 · 추가옵션 = 그 레벨 불꽃 최대 조합 · 스타포스 = 22성 · 잠재 = 캐릭터별 최상 조합 (23성 이상·익셉셔널은 만점을 넘는 가산)",
        ]
        notes.extend(context.notes())
        return notes

    def value_fields(axes, raw):
        """%p 점수 필드. axes: 축 → (현재 %p, 만점 %p), raw: 축 → 원래 수치(급수·별·강화 횟수)"""
        axes = {key: axes[key] for key in AXIS_LABEL if axes.get(key) is not None}
        value, best = sum(v for v, _ in axes.values()), sum(m for _, m in axes.values())
        return {
            "total_score": round(value, 1),
            "score_ratio": round(100 * value / best, 1) if best > 0 else 0.0,
            "benchmark_total": round(best, 1),
            "detail": {key: round(v, 1) for key, (v, _) in axes.items()},
            "bar_max": {key: round(m or v, 1) for key, (v, m) in axes.items()},
            "breakdown": [{"key": key, "label": AXIS_LABEL[key], "value": round(v, 1), "max": round(m, 1), "raw": raw.get(key)}
                          for key, (v, m) in axes.items()],
        }

    def option_pct(option, weaponish):
        """고정 옵션(기본 옵션·주문서)의 주스탯 %p. 방어구·장신구는 급수 × per_flat, 무기류는 공% 환산 ÷ per_unit.
        보공·방무는 무기 환산과 같은 방식(보스 방어율 기준)으로 더합니다."""
        option = option or {}
        boss, ied = float(option.get("boss_damage") or 0), float(option.get("ignore_monster_armor") or 0)
        extra = (weapon_context.ied_value(ied) if ied else 0.0) / per_unit
        if weaponish:  # 보공·데미지는 weapon_add_value 가 포함
            return weapon_add_value(option, weapon_context, char_level)[0] / per_unit + extra
        return (calculate_item_score(option, char_class, main_stat=main_stat, context=context) * per_flat
                + boss * weapon_context.boss_per_pct / per_unit + extra)

    def best_scroll_option(slot, part, slots):
        """최상 주문서 slots 장을 바른 스탯·공/마 (scoring.toml [scroll])"""
        rules = SCORING.scroll.raw
        rule = rules["weapon"] if slot == "무기" else rules["heart"] if "기계 심장" in part else rules["default"]
        option = {"magic_power" if main_stat == "int" else "attack_power": rule["attack"] * slots}
        if "all_stat" in rule:
            option.update(dict.fromkeys(("str", "dex", "int", "luk"), rule["all_stat"] * slots))
        else:
            option.update(dict.fromkeys(("str", "dex", "luk") if main_stat == "all_stat" else (main_stat,), rule["main_stat"] * slots))
        return option

    def exceptional_pct(item):
        option = item.get("item_exceptional_option") or {}
        return (calculate_item_score(option, char_class, main_stat=main_stat, context=context) * per_flat, 0.0) if option else None

    for item in items:
        pct_axes, raw_axes = {}, {}   # 축 → (현재 %p, 만점 %p), 축 → 원래 수치(급수·별·강화 횟수)
        is_noljang = False

        slot = item.get("item_equipment_slot", "")
        part = item.get("item_equipment_part", "")
        name = item.get("item_name", "")
        icon = item.get("item_icon", "")
        shape_icon = item.get("item_shape_icon") or icon  # 외형 변경(앤빌)이 있으면 인게임 모습
        star = int(item.get("starforce", 0))
        item_req_level = int(item.get("item_base_option", {}).get("base_equipment_level", 0))

        is_weapon = any(k in slot for k in special_items.wse_slots)
        is_special = any(k in part for k in special_items.special_parts)
        main_stat_label = main_stat.upper()

        raw_options_dict = {
            "base": item.get("item_base_option"),
            "add": item.get("item_add_option"),
            "etc": item.get("item_etc_option"),
            "starforce": item.get("item_starforce_option"),
            "potential_grade": item.get("potential_option_grade"),
            "potential_options": get_potential_options(item, "potential"),
            "additional_grade": item.get("additional_potential_option_grade"),
            "additional_options": get_potential_options(item, "additional_potential"),
            "exceptional": item.get("item_exceptional_option")
        }

        if is_special or is_seed_ring(item):
            # 훈장·뱃지·포켓·칭호: 고정 옵션을 유효 스탯% 로 환산하고 부위별 최상(캐릭터별 환산)을 만점으로 둡니다 (app/special.py).
            # 시드링(특수 반지·예비 특수 반지): 효과의 주스탯 %p, 만렙 효과가 만점입니다.
            expired, benchmark, lines, add_entries = False, 0.0, [], []
            analysis = {"recognized": [], "ignored": [], "unsupported": []}
            if is_seed_ring(item):
                part = POTENTIAL.special.ring_part
                ring_level = int(float(item.get("special_ring_level") or 0))
                pct_axes["special"] = seed_ring_value(name, ring_level, char_class, context, damage_total)
                if pct_axes["special"]:
                    analysis["recognized"] = [{"option": f"{name} Lv.{ring_level}", "value": round(pct_axes["special"][0], 2),
                                               "rule": "시드링 효과의 주스탯 %p"}]
                else:
                    analysis["unsupported"] = [f"{name} Lv.{ring_level}"]
                notes = ["시드링: 리스트레인트(구간 딜 비중 × 공/마)·컨티뉴어스(상시 보공·공/마) 효과를 주스탯 %p 로 환산, 만렙 효과가 만점"]
            elif part in POTENTIAL.special.add_option_parts:
                # 훈장·포켓: 환생의 불꽃 급수(캐릭터 효율 계수). 만점은 최상 아이템 레벨(potential.toml [special.best] flame_level)의 불꽃 최대 급수
                flame_level = POTENTIAL.special.best.raw[part]["flame_level"]
                value = calculate_item_score(item.get("item_add_option") or {}, char_class, main_stat=main_stat, context=context)
                benchmark = calculate_item_score(flame_best_add_option(flame_level, char_class, main_stat, context.sub_names, context),
                                                 char_class, main_stat=main_stat, context=context)
                pct_axes["add"] = (value * per_flat, benchmark * per_flat)
                raw_axes["add"] = round(value, 1)
                add_entries = [{**entry, "category": "추가옵션"}
                               for entry in add_option_entries(item.get("item_add_option") or {}, char_class, main_stat=main_stat, context=context)]
                notes = [f"추가옵션 급수 {value:g} × {per_flat:.3g}%p · 만점 = {flame_level}제 불꽃 최대 급수 {benchmark:g} (기본 옵션은 보지 않음)"]
            else:
                lines, expired = special_lines(item, weapon_context)
                analysis = analyze_special_lines(lines, weapon_context, char_level)
                benchmark = best_total(part, weapon_context, char_level)
                pct_axes["special"] = (analysis["score"], benchmark)  # 이미 유효 스탯%
                _log_unsupported_options(name, "special", analysis, unsupported)
                notes = [f"고유 옵션 유효 스탯% 합 · 만점 = 이 캐릭터의 부위 최상({benchmark:.2f}%p)"] + context.notes()
            if expired:
                notes.insert(0, "칭호 옵션 적용 기간 만료 · 스탯을 인정하지 않습니다")
            upgrades = exceptional_axis(item, part)
            if upgrades:  # 강화가 없으면 축을 보여주지 않습니다
                pct_axes["exceptional"] = exceptional_pct(item)
                raw_axes["exceptional"] = upgrades
                notes.append(f"익셉셔널: 오른 스탯을 환산한 가산 (현재 {upgrades}회)")
            fields = value_fields(pct_axes, raw_axes)
            special_guide = get_special_part_guide(fields["score_ratio"], part, name, expired)
            special_guide_tag, special_guide_message = split_guide(special_guide)
            evaluate_list.append({
                "is_wse": False, "is_special": True, "is_noljang": False, "slot": slot, "part": part, "name": name, "icon": icon, "shape_icon": shape_icon, "star": 0, "level": item_req_level,
                "total_score": fields["total_score"],
                "score_ratio": fields["score_ratio"],
                "benchmark_total": fields["benchmark_total"],
                # 훈장·뱃지는 테두리 색을 매기지 않고, 뱃지는 점수도 보여주지 않으며 총평에서 뺍니다 (potential.toml [special]).
                "graded": part not in POTENTIAL.special.no_grade_parts,
                "scored": part not in POTENTIAL.special.no_score_parts,
                "guide": special_guide,
                "guide_tag": special_guide_tag,
                "guide_message": special_guide_message,
                "detail": fields["detail"],
                "bar_max": fields["bar_max"],
                "raw_options": {**raw_options_dict, "fixed_options": lines, "expired": expired, "special_ring_level": item.get("special_ring_level")},
                "scoring_basis": {
                    "main_stat": main_stat_label,
                    "recognized_options": [{**entry, "category": "고유 옵션"} for entry in analysis["recognized"]] + add_entries,
                    "unrecognized_options": [
                        {"category": "고유 옵션", "option": entry["option"], "reason": entry["reason"]} for entry in analysis["ignored"]
                    ] + [{"category": "고유 옵션", "option": option, "reason": "unsupported"} for option in analysis["unsupported"]],
                    "weights": notes,
                    "breakdown": fields["breakdown"],
                    "benchmarks": {"special": benchmark},
                },
            })
            continue

        etc_ops = item.get("item_etc_option") or {}
        star_ops = item.get("item_starforce_option") or {}

        def safe_int(val):
            try: return int(val) if val is not None else 0
            except (TypeError, ValueError): return 0

        etc_stats_max = max(safe_int(etc_ops.get(s)) for s in ["str", "dex", "int", "luk"])
        etc_atk_max = max(safe_int(etc_ops.get("attack_power")), safe_int(etc_ops.get("magic_power")))
        star_stats_max = max(safe_int(star_ops.get(s)) for s in ["str", "dex", "int", "luk"])

        is_superior = special_items.superior_keyword in name

        if noljang.star_min <= star <= noljang.star_max and not is_superior and item_req_level <= noljang.max_level:
            if etc_stats_max > noljang.etc_stat_strong and etc_atk_max > noljang.etc_attack_strong:
                is_noljang = True
            elif star_stats_max == 0 and (etc_stats_max > noljang.etc_stat_weak or etc_atk_max > noljang.etc_attack_weak):
                is_noljang = True

        # 스타포스 %p: 스타포스로 오른 스탯·공/마 ÷ 22성 값 (같은 단위). 놀장·주문서 강화는 스타포스 옵션이 비어 놀장 표로 계산합니다.
        # 23성 이상과 슈페리얼은 실제 값이라 만점을 넘을 수 있습니다. 무기·보조무기는 이 아이템의 순수 공/마로 22성 값을 계산합니다.
        raw_axes["star"] = star
        if any(k in slot for k in ("무기", "보조무기")) and not is_superior:
            attack_key = "magic_power" if main_stat == "int" else "attack_power"
            pure = safe_int((item.get("item_base_option") or {}).get(attack_key)) + safe_int(etc_ops.get(attack_key))
            weapon_like = "보조무기" not in slot or safe_int((item.get("item_base_option") or {}).get(attack_key)) >= \
                SCORING.starforce.weapon.weapon_like_min_attack
            bench_option = weapon_starforce_option(item_req_level, SCORING.starforce.benchmark.benchmark_star, pure, main_stat,
                                                   context.sub_names, weapon_like)
            if bench_option:
                pct_axes["star"] = (weapon_add_value(item.get("item_starforce_option") or {}, weapon_context, char_level)[0] / per_unit,
                                    weapon_add_value(bench_option, weapon_context, char_level)[0] / per_unit)
        elif not is_weapon:
            best_star = min(SCORING.starforce.benchmark.benchmark_star, max_starforce(item_req_level, name, is_superior))
            star_bench = starforce_benchmark_option(item_req_level, main_stat, context.sub_names, part) or \
                starforce_option(item_req_level, best_star, main_stat, context.sub_names, part)
            if star_bench:
                source = noljang_option(item_req_level, star, main_stat, context.sub_names) \
                    if is_noljang or item.get("starforce_scroll_flag") == "사용" else item.get("item_starforce_option")
                pct_axes["star"] = (calculate_item_score(source or {}, char_class, main_stat=main_stat, context=context) * per_flat,
                                    calculate_item_score(star_bench, char_class, main_stat=main_stat, context=context) * per_flat)

        # 기본 옵션: 만점 = 같은 값(같은 장비면 기본 옵션이 같아 테두리 비율에는 고정분). 주문서: 만점 = 횟수 × 최상 주문서.
        base_option = {key: value for key, value in (item.get("item_base_option") or {}).items() if key != "base_equipment_level"}
        base_value = option_pct(base_option, is_weapon)
        if base_value > 0:
            pct_axes["base"] = (base_value, base_value)
        slots = sum(safe_int(item.get(key)) for key in ("scroll_upgrade", "scroll_upgradeable_count", "scroll_resilience_count"))
        if slots > 0:
            scroll_option = dict(etc_ops)
            if is_noljang or item.get("starforce_scroll_flag") == "사용":  # 주문서 칸의 놀장 스타포스 스탯을 뺍니다
                for key, amount in noljang_option(item_req_level, star, main_stat, context.sub_names).items():
                    scroll_option[key] = max(0, safe_int(scroll_option.get(key)) - amount)
            scroll_value = option_pct(scroll_option, is_weapon)
            # 데몬어벤져(HP 주문서)는 최상 기준을 따로 정할 때까지 현재 값을 만점으로 둡니다
            scroll_best = scroll_value if main_stat == "hp" else option_pct(best_scroll_option(slot, part, slots), is_weapon)
            pct_axes["scroll"] = (scroll_value, scroll_best)
            raw_axes["scroll"] = f"{safe_int(item.get('scroll_upgrade'))}/{slots}"

        soul_analysis, add_recognized, add_attack_tier = None, [], None
        add_evaluated = not any(k in part or k in slot for k in GUIDES.axes.no_add_parts)
        if is_weapon:
            # 무기류는 공격력% 단위(app/weapon.py) → ÷ per_unit 로 %p. 무기는 추옵·스타포스·윗잠·에디·소울, 보조무기·엠블렘은 윗잠·에디(·보조무기 스타포스).
            group, tier = part_group(slot, part), tier_of(name)
            if group == "weapon":
                add_value, add_recognized = weapon_add_value(item.get("item_add_option"), weapon_context, char_level)
                attack_amount = float((item.get("item_add_option") or {}).get("magic_power" if weapon_context.attack_label == "마력" else "attack_power") or 0)
                add_attack_tier = weapon_attack_tier(item, weapon_context.attack_label, attack_amount) if attack_amount else None
                pct_axes["add"] = (add_value / per_unit, weapon_add_best(item, weapon_context, char_level) / per_unit)
                soul_analysis = analyze_weapon_item(item, "soul", weapon_context, char_level)
                pct_axes["soul"] = (max(soul_analysis["score"], 0) / per_unit,
                                    best_lines_total("soul", group, weapon_context, char_level, tier) / per_unit)
            pot_analysis = analyze_weapon_item(item, "potential", weapon_context, char_level)
            eddy_analysis = analyze_weapon_item(item, "additional_potential", weapon_context, char_level)
            benchmarks = {  # 공% 단위 캐릭터별 최상 조합
                "pot": best_lines_total("potential", group, weapon_context, char_level, tier),
                "pot_additional": best_lines_total("additional_potential", group, weapon_context, char_level, tier),
            }
            pct_axes["pot"] = (max(pot_analysis["score"], 0) / per_unit, benchmarks["pot"] / per_unit)
            pct_axes["pot_additional"] = (max(eddy_analysis["score"], 0) / per_unit, benchmarks["pot_additional"] / per_unit)
        else:
            # 추옵이 없는 부위(반지·어깨장식·기계 심장)는 추옵 축 자체를 두지 않습니다.
            # 급수의 공/마·올스탯%·부스탯 계수는 잠재 환산과 같은 캐릭터 효율(context)입니다. 만점은 그 레벨 불꽃 최대 조합.
            if add_evaluated:
                add_option = item.get("item_add_option") or {}
                add_value = calculate_item_score(add_option, char_class, main_stat=main_stat, context=context)
                add_recognized = add_option_entries(add_option, char_class, main_stat=main_stat, context=context)
                add_best = calculate_item_score(flame_best_add_option(item_req_level, char_class, main_stat, context.sub_names, context),
                                                char_class, main_stat=main_stat, context=context)
                pct_axes["add"] = (add_value * per_flat, add_best * per_flat)
                raw_axes["add"] = round(add_value, 1)  # 급수
            benchmarks = {  # 유효 스탯% 단위 캐릭터별 최상 조합 (스펙업 잠재 추천의 목표 상한으로도 씁니다)
                "pot": best_armor_total("potential", part, context, char_level, item_req_level),
                "pot_additional": best_armor_total("additional_potential", part, context, char_level, item_req_level),
            }
            pot_analysis = analyze_potential_score(item, "potential", char_class, char_level, main_stat=main_stat, context=context)
            eddy_analysis = analyze_potential_score(item, "additional_potential", char_class, char_level, main_stat=main_stat, context=context)
            pct_axes["pot"] = (max(pot_analysis["score"], 0), benchmarks["pot"])  # 이미 유효 스탯%
            pct_axes["pot_additional"] = (max(eddy_analysis["score"], 0), benchmarks["pot_additional"])

        _log_unsupported_options(name, "potential", pot_analysis, unsupported)
        _log_unsupported_options(name, "additional_potential", eddy_analysis, unsupported)
        recognized_options = [
            {**entry, "category": category}
            for analysis, category in ((pot_analysis, "윗잠재"), (eddy_analysis, "에디셔널"),
                                       (soul_analysis or {"recognized": []}, "소울"), ({"recognized": add_recognized}, "추가옵션"))
            for entry in analysis["recognized"]
        ]

        def unrecognized_entries(analysis, category):
            # 점수에 반영되지 않은 옵션과 그 이유. 화면에서 "왜 점수가 낮은지"를 보여주는 근거입니다.
            return [
                {"category": category, "option": entry["option"], "reason": entry["reason"]} for entry in analysis["ignored"]
            ] + [{"category": category, "option": option, "reason": "unsupported"} for option in analysis["unsupported"]]

        unrecognized_options = unrecognized_entries(pot_analysis, "윗잠재") + unrecognized_entries(eddy_analysis, "에디셔널")
        if soul_analysis:
            unrecognized_options += unrecognized_entries(soul_analysis, "소울")

        upgrades = exceptional_axis(item, part) if not is_weapon else None
        if upgrades:  # 강화가 없으면 축을 보여주지 않습니다
            pct_axes["exceptional"] = exceptional_pct(item)
            raw_axes["exceptional"] = upgrades

        if pot_analysis["score"] != -1:
            fields = value_fields(pct_axes, raw_axes)
            guide_text = get_dynamic_guide(pct_axes, star, part, name, fields["score_ratio"], item_req_level, is_noljang)
            guide_tag, guide_message = split_guide(guide_text)
            evaluate_list.append({
                "is_wse": is_weapon, "is_special": False, "is_noljang": is_noljang, "slot": slot, "part": part, "name": name, "icon": icon, "shape_icon": shape_icon, "star": star, "level": item_req_level,
                "total_score": fields["total_score"],
                "score_ratio": fields["score_ratio"],
                "benchmark_total": fields["benchmark_total"],
                "graded": True,
                "scored": True,
                "guide": guide_text,
                "guide_tag": guide_tag,
                "guide_message": guide_message,
                "detail": fields["detail"],
                "bar_max": fields["bar_max"],
                "raw_options": {**raw_options_dict, "soul_grade": item.get("soul_potential_grade"),
                                "soul_stage": item.get("soul_potential_amplified_grade"), "soul_options": [e["option"] for e in (soul_analysis or {}).get("recognized", []) if not e.get("hidden")] + (soul_analysis or {}).get("unsupported", [])},
                "scoring_basis": {
                    "main_stat": main_stat_label,
                    "recognized_options": recognized_options,
                    "unrecognized_options": unrecognized_options,
                    "add_attack_tier": add_attack_tier,
                    "weights": weight_notes()
                    + ([f"무기류: 공격력 1% = 주스탯 {1 / per_unit:.3g}%p · 보공 1% = 공 {weapon_context.boss_per_pct:.3g}%, 만점은 캐릭터별 최상 조합"] if is_weapon else [])
                    + ([f"익셉셔널: 오른 스탯을 환산한 가산 (현재 {upgrades}회)"] if upgrades else []),
                    "breakdown": fields["breakdown"],
                    "benchmarks": benchmarks,
                },
            })

    return evaluate_list


def generate_overall_review(evaluate_list):
    # 총평: 장비 달성률 = 주스탯 %p 합 ÷ 만점 %p 합. 만점이 큰 부위(무기 등)일수록 크게 반영됩니다.
    # 특수부위·시드링은 포함하고 칭호와 점수를 매기지 않는 부위(뱃지)는 뺍니다.
    counted = [item for item in evaluate_list if item["part"] != "칭호" and item.get("scored", True)]
    total = sum(item["total_score"] for item in counted)
    best = sum(item["benchmark_total"] for item in counted)
    achievement = 100 * total / best if best > 0 else 0.0
    all_sorted_results = sorted(evaluate_list, key=lambda x: x["score_ratio"])

    # 랭크 컷은 app/data/grades.toml 의 overall 표. 달성률이 min 이상인 첫 항목, 없으면 마지막 항목을 씁니다.
    entry = next((candidate for candidate in GRADES.overall if achievement >= candidate["min"]), GRADES.overall[-1])
    overall_review = {"rank": entry["rank"], "achievement": round(achievement, 1), "total": round(total, 1), "max": round(best, 1)}

    return overall_review, all_sorted_results


def _power_estimate_limit(char_class, equipped, selected):
    if char_class in ("제로", "데몬어벤져", "제논"):
        return "직업별 전투력 보정 계수 또는 제외 옵션 미검증"
    def weapon(rows, slot):
        return next((item for item in rows if item.get("item_equipment_slot") == slot), {})
    fields = ("item_name", "item_base_option", "item_add_option", "starforce", "starforce_scroll_flag", "item_starforce_option")
    a, b = weapon(equipped, "무기"), weapon(selected, "무기")
    if any(a.get(k) != b.get(k) for k in fields):
        return "무기 교체·강화에 필요한 기본 공/마·추옵·스타포스 표준화 미검증"
    a, b = weapon(equipped, "보조무기"), weapon(selected, "보조무기")
    if a != b and any(x.get("item_name") == "아스트라 아워글라스" for x in (a, b)):
        return "아스트라 아워글라스 기본 보공 제외 보정 미검증"
    return None


def build_combat_power_basis(stat_data, current_context, optimal_context, optimization, char_class, equipped_items, extras, selected_items=None) -> dict:
    """API 전투력을 최적 세팅 기준으로 보정합니다 (app/combat_power.py). 최적화 미지원이면 API 값을 그대로 둡니다.

    크뎀은 API 값 대신 성장 요소 합산(app/crit_damage.py, 일반 스킬·버프 제외)을 쓰고,
    최적 세팅은 그 값에 최적화기가 계산한 크뎀 차이(장비·하이퍼·유니온·세트 등)를 더합니다.
    """
    api_power = final_stat_float(stat_data, "전투력") or 0.0
    boss = final_stat_float(stat_data, "보스 몬스터 데미지") or 0.0
    supported = optimization.get("method") == "saved_presets_boss_stat_index" and current_context.main_stat in ("str", "dex", "int", "luk")
    limit = _power_estimate_limit(char_class, equipped_items, selected_items if selected_items is not None else equipped_items)
    if not supported or limit:
        return {"api": api_power, "adjusted": api_power, "status": "unsupported", "ratio": 1.0,
                "reason": limit or "프리셋 투영에 필요한 입력 부족"}

    def inputs(context, boss_damage):
        return {"main_stat": context.final_main, "sub_stat": context.final_sub, "attack": context.attack,
                "boss_damage_pct": boss_damage, "crit_damage_pct": context.crit_damage}

    # 링크 스킬은 전투력에 반영되지 않으므로 최적화기가 링크만 현재로 고정해 투영한 값을 씁니다.
    optimal = dict(optimization.get("combat_power_inputs") or inputs(optimal_context, optimization.get("boss_damage", boss)))
    current = inputs(current_context, boss)
    crit, crit_breakdown = growth_crit_damage(char_class, equipped_items, extras)
    optimal["crit_damage_pct"] = crit + optimal["crit_damage_pct"] - current["crit_damage_pct"]
    current["crit_damage_pct"] = crit
    basis = adjust_combat_power(api_power, current, optimal, bool(optimization.get("combat_power_is_current", True)))
    return {**basis, "crit_damage": {"current": crit, "optimal": round(optimal["crit_damage_pct"], 2), "breakdown": crit_breakdown}}


def extract_combat_power(stat_data):
    """스탯 응답에서 전투력 값을 꺼냅니다. 없으면 0을 돌려줍니다."""
    for stat in (stat_data or {}).get("final_stat") or []:
        if stat.get("stat_name") == "전투력":
            return stat.get("stat_value", 0)
    return 0


def select_preset_items(item_data, preset_idx):
    """선택된 프리셋의 장비 목록을 돌려주고, 비어 있으면 현재 착용 장비로 대체합니다."""
    items = item_data.get(f"item_equipment_preset_{preset_idx}")
    return items or item_data.get("item_equipment", [])


def _preset_main_flat_delta(equipped, selected, main_stat):
    """API 장비 합계의 고정 주스탯 차이만 투영합니다. 특수 직업은 기존 추정을 유지합니다."""
    if not equipped or not selected or main_stat not in ("str", "dex", "int", "luk"):
        return 0.0
    def total(items):
        return sum(float((item.get("item_total_option") or {}).get(main_stat) or 0)
                   for item in items)
    # 한쪽 응답에 합계가 없으면 누락을 스탯 감소로 오인하지 않습니다.
    if not all(main_stat in (item.get("item_total_option") or {})
               for item in [*equipped, *selected]):
        return 0.0
    return total(selected) - total(equipped)


def build_report(character_name, basic_info, item_data, stat_data, unsupported=None, extras=None):
    """기본정보·장비·스탯 응답으로 진단 결과 전체를 만듭니다.

    /check-items 응답이 이 함수를 그대로 사용합니다.
    unsupported 에 Counter 를 주면 점수에 반영하지 못한 옵션을 (종류, 옵션)별로 세어 넣습니다.
    extras 는 심볼·하이퍼스탯·유니온·유니온 아티팩트·어빌리티·링크·헥사 스탯·세트 효과 응답(scraper.STAT_EXTRA_ENDPOINTS 의 키)입니다. 없으면 레벨로 추정합니다.
    """
    char_class = basic_info.get("character_class")
    char_level = int(basic_info.get("character_level") or 0)
    char_image = basic_info.get("character_image", "")
    # 주스탯은 여기서 한 번만 정합니다. 바이퍼·캐논마스터·캡틴은 스탯 응답의 STR/DEX 비교로 전환 세팅을 반영합니다.
    main_stat = get_main_stat(char_class, stat_data)
    # 자본대(전투력)와 잠재 환산 기준(StatContext)은 현재 착용 장비·스탯 응답·추가 조회로 한 번만 정합니다.
    equipped_items = active_items(item_data.get("item_equipment", []))
    profile = build_character_profile(char_class, main_stat, char_level, equipped_items, stat_data, extras)

    best_preset_idx = get_best_preset(item_data, char_class, char_level, main_stat=main_stat, context=profile["context"])
    items = select_preset_items(item_data, best_preset_idx)

    # 공통 기준으로 프리셋을 선택한 뒤 선택 장비로 최종 환산 기준을 재구성합니다.
    equipped_context = profile["context"]
    selected_profile = build_character_profile(char_class, main_stat, char_level, active_items(items), stat_data, extras)
    selected_context = selected_profile["context"]
    # 현재 최종 스탯을 다른 프리셋의 %로 나누면 기본 스탯이 왜곡됩니다.
    # 선택 장비의 %뿐 아니라 API 장비 합계에 명시된 고정 주스탯 차이도 반영합니다.
    selected_profile["context"] = project_context(equipped_context, selected_context,
        equipped_items, items, char_level, (extras or {}).get("set_effect"),
        same_preset=str(item_data.get("preset_no")) == str(best_preset_idx))
    profile = selected_profile
    profile["conversion_preset"] = best_preset_idx
    profile["equipped_preset"] = item_data.get("preset_no")
    optimized = optimize_presets(char_class, main_stat, char_level, item_data, stat_data,
                                 extras, equipped_context)
    if optimized is not None:
        best_preset_idx, items, profile["context"], optimization = optimized
        profile["conversion_preset"] = best_preset_idx
        profile["optimization"] = optimization
    else:
        profile["optimization"] = {"method": "legacy_equipment_score_fallback",
            "limitations": ["필수 스탯 누락 또는 특수 직업은 조합 최적화 미지원: 기존 장비 점수 기준 유지"]}
    profile["context"].sources["equipment_basis"] = "selected_preset"
    profile["context"].sources["final_stats_basis"] = "projected_saved_presets" if optimized else "currently_equipped"
    combat_power_basis = build_combat_power_basis(stat_data, equipped_context, profile["context"], profile["optimization"],
                                                  char_class, equipped_items, extras, items)
    # 무기류 환산(공격력% 단위)에 쓰는 보공·데미지·방무는 스탯 응답에서 읽습니다.
    profile["weapon"] = WeaponContext(
        profile["context"],
        damage=final_stat_float(stat_data, "데미지") or 0.0,
        boss_damage=final_stat_float(stat_data, "보스 몬스터 데미지") or 0.0,
        ignore_defense=final_stat_float(stat_data, "방어율 무시") or 0.0,
    )

    # 칭호는 장비 목록이 아니라 응답의 title 필드에 있고, 예비 특수 반지는 프리셋 투영에서 빠지므로 평가 목록에 따로 넣습니다.
    title = title_item(item_data.get("title") or {})
    spare_rings = [item for item in select_preset_items(item_data, best_preset_idx) if item.get("item_equipment_slot") == "예비 특수 반지"]
    extra_items = [item for item in spare_rings if item not in items] + ([title] if title else [])
    evaluate_list = evaluate_equipment([*items, *extra_items], char_class, char_level, unsupported=unsupported, main_stat=main_stat, profile=profile)
    attach_values(evaluate_list, items, equipped_items, profile, char_level, (extras or {}).get("set_effect"))
    # 스펙업 추천: 아이템별 축 강화 행동과 전체 상위 (100억 메소당 주스탯%, app/spec_up.py)
    damage_total = (final_stat_float(stat_data, "데미지") or 0.0) + (final_stat_float(stat_data, "보스 몬스터 데미지") or 0.0)
    if profile["optimization"].get("method") == "saved_presets_boss_stat_index":
        damage_total = profile["optimization"]["damage"] + profile["optimization"]["boss_damage"]
    spec_up = attach_spec_up(evaluate_list, [*items, *extra_items], profile["context"], char_class, main_stat, char_level,
                             GUIDES.names.limited, damage_total, profile["weapon"])
    overall_review, all_sorted_results = generate_overall_review(evaluate_list)

    return {
        "character": character_name,
        "class": char_class,
        "level": char_level,
        "character_image": char_image,
        "combat_power": extract_combat_power(stat_data),
        "combat_power_adjusted": combat_power_basis["adjusted"],
        "combat_power_basis": combat_power_basis,
        "best_preset": best_preset_idx,
        "profile": {
            **{key: value for key, value in profile.items() if key not in ("context", "weapon")},
            "context": profile["context"].as_dict(),
            "weapon": profile["weapon"].as_dict(),
        },
        "overall": overall_review,
        "spec_up": spec_up,
        "results": all_sorted_results,
    }
