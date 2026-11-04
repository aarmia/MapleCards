import logging
import operator
import re
from collections import Counter

from app.calculator import (
    analyze_potential_score,
    calculate_item_score,
    calculate_potential_score,
    final_stat_float,
    get_advanced_add_score,
    get_main_stat,
    get_potential_options,
    get_starforce_score,
)
from app.character_profile import build_character_profile
from app.potential import StatContext, reference_context
from app.preset_optimizer import optimize_presets
from app.preset_projection import active_items, project_context
from app.scoring_config import GRADES, POTENTIAL, SCORING, load_guides
from app.special import analyze_special_lines, best_total, is_seed_ring, ring_score, special_lines, title_item
from app.weapon import WeaponContext, analyze_weapon_item, best_lines_total, part_group, tier_of, weapon_add_value

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
    "total", "add", "pot", "eddy", "starscore", "star", "star_next", "max_star", "at_cap", "level", "noljang", "limited",
    "add_evaluated", "star_evaluated", "worst", "next", "min_ratio", "target_add", "target_star_330", "target_star_17",
    "target_star_21",
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


def build_guide_context(scores, star_val, part_name, item_name, total_score, item_req_level, is_noljang, benchmarks=None) -> dict:
    """규칙 표가 비교할 값들을 계산합니다. 파생값(최대 스타포스, 최저 축, 목표치)은 코드가 맡고 규칙 표는 조건과 문구만 가집니다."""
    cap = GUIDES.star_cap
    max_star = cap.default
    for level_limit, star_limit in cap.bands:
        if item_req_level < level_limit:
            max_star = star_limit
            break

    axes = GUIDES.axes
    add_evaluated = not any(k in part_name for k in axes.no_add_parts)
    star_evaluated = not any(k in part_name for k in axes.no_star_parts)
    eval_indices = [1, 2] + ([0] if add_evaluated else []) + ([3] if star_evaluated else [])
    # 무기류는 캐릭터별 최상 조합(benchmarks)을 윗잠·에디 기준점으로 씁니다.
    marks = list(axes.benchmarks)
    for index, key in ((1, "pot"), (2, "pot_additional")):
        if benchmarks and benchmarks.get(key):
            marks[index] = benchmarks[key]
    ratios = [scores[i] / marks[i] for i in range(4)]
    ranked = sorted(((ratios[i], axes.labels[i]) for i in eval_indices), key=lambda entry: entry[0])

    targets = GUIDES.targets
    return {
        "total": total_score,
        "add": scores[0], "pot": scores[1], "eddy": scores[2], "starscore": scores[3],
        "star": star_val, "star_next": star_val + 1, "max_star": max_star,
        "at_cap": star_val >= SCORING.starforce.base_star or star_val >= max_star,
        "level": item_req_level, "noljang": is_noljang,
        "limited": any(keyword in item_name for keyword in GUIDES.names.limited),
        "add_evaluated": add_evaluated, "star_evaluated": star_evaluated,
        "worst": ranked[0][1], "next": ranked[1][1] if len(ranked) > 1 else ranked[0][1], "min_ratio": ranked[0][0],
        "target_add": targets.add_low if item_req_level <= targets.add_level_split else targets.add_high,
        "target_star_330": min(
            targets.star_330_low if item_req_level <= targets.star_330_level_split else targets.star_330_high, max_star
        ),
        "target_star_17": min(targets.star_mid, max_star),
        "target_star_21": targets.star_step if max_star >= targets.star_step else max_star,
    }


def get_special_part_guide(total_score, part_name, item_name, expired=False):
    return select_guide(GUIDES.special_rule, {"total": total_score, "expired": expired}, GUIDES.names.raw, item_name, part_name)


def get_dynamic_guide(scores, star_val, part_name, total_score, item_name, item_req_level, is_noljang=False, benchmarks=None):
    context = build_guide_context(scores, star_val, part_name, item_name, total_score, item_req_level, is_noljang, benchmarks)
    return select_guide(GUIDES.rule, context, GUIDES.names.raw, item_name)


def evaluate_equipment(items, char_class, char_level, unsupported=None, main_stat=None, profile=None):
    evaluate_list = []
    special_items = SCORING.special_items
    weights = SCORING.weights
    noljang = SCORING.noljang
    # 주스탯은 호출자(build_report)가 스탯 응답까지 보고 정해 넘깁니다. 없으면 직업명으로 정합니다.
    main_stat = main_stat or get_main_stat(char_class)
    # 잠재 환산 기준. build_report 가 캐릭터 스탯으로 만든 것이 없으면 기준 캐릭터(potential.toml reference)를 씁니다.
    context = profile["context"] if profile else reference_context(char_class, main_stat)
    target_multiplier = profile["add_target_multiplier"] if profile else 1.0
    weapon_context = (profile or {}).get("weapon") or WeaponContext(context)

    def weight_notes(item_req_level):
        star_weight = round(1.0 + (item_req_level - SCORING.starforce.reference_level) / SCORING.starforce.level_weight_divisor, 3)
        notes = [
            f"윗잠재 환산값 × {weights.potential:g}",
            f"에디셔널 환산값 × {weights.additional:g}",
            f"스타포스 레벨 가중치 × {star_weight if item_req_level > 0 else 0}",
        ]
        if profile:
            notes.append(f"추옵 목표 급수 × {target_multiplier:g} ({profile['tier']})")
        notes.extend(context.notes())
        return notes

    for item in items:
        add_score, pot_score, eddy_score, adv_star_score = 0.0, 0.0, 0.0, 0.0
        add_raw_score = 0.0
        is_noljang = False

        slot = item.get("item_equipment_slot", "")
        part = item.get("item_equipment_part", "")
        name = item.get("item_name", "")
        icon = item.get("item_icon", "")
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
            # 훈장·뱃지·포켓·칭호: 고정 옵션을 유효 스탯% 로 환산하고 부위별 최상(캐릭터별 환산)을 100 으로 둡니다 (app/special.py).
            # 시드링(특수 반지·예비 특수 반지): 종류와 레벨로 점수를 냅니다.
            expired, benchmark, lines = False, 0.0, []
            if is_seed_ring(item):
                part = POTENTIAL.special.ring_part
                total_item_score, entry = ring_score(item)
                value, analysis = total_item_score, {"recognized": [entry], "ignored": [], "unsupported": []}
                notes = [f"시드링 · 종류 가중치 × 레벨 ÷ {POTENTIAL.special.ring.max_level} (리스트레인트·컨티뉴어스 만렙 = 100)"]
            else:
                lines, expired = special_lines(item, weapon_context)
                analysis = analyze_special_lines(lines, weapon_context, char_level)
                value = analysis["score"]
                benchmark = best_total(part, weapon_context, char_level)
                total_item_score = round(100 * value / benchmark, 2) if benchmark > 0 else 0.0
                _log_unsupported_options(name, "special", analysis, unsupported)
                notes = [f"고유 옵션 유효 스탯% 합 ÷ 이 캐릭터의 부위 최상({benchmark:.2f}) × 100"] + context.notes()
                if part in POTENTIAL.special.add_option_parts:
                    notes.insert(0, "기본 옵션은 보지 않고 추가옵션(환생의 불꽃)만 환산합니다")
            factor = (total_item_score / value) if value else 0
            special_guide = get_special_part_guide(total_item_score, part, name, expired)
            special_guide_tag, special_guide_message = split_guide(special_guide)
            if expired:
                notes.insert(0, "칭호 옵션 적용 기간 만료 · 스탯을 인정하지 않습니다")
            evaluate_list.append({
                "is_wse": False, "is_special": True, "is_noljang": False, "slot": slot, "part": part, "name": name, "icon": icon, "star": 0, "level": item_req_level,
                "total_score": total_item_score,
                "guide": special_guide,
                "guide_tag": special_guide_tag,
                "guide_message": special_guide_message,
                "detail": {"special": round(total_item_score, 1)},
                "raw_options": {**raw_options_dict, "fixed_options": lines, "expired": expired, "special_ring_level": item.get("special_ring_level")},
                "scoring_basis": {
                    "main_stat": main_stat_label,
                    "recognized_options": [
                        {**entry, "category": "고유 옵션", "weighted_value": round(entry["value"] * factor, 2), "weight": round(factor, 3)}
                        for entry in analysis["recognized"]
                    ],
                    "unrecognized_options": [
                        {"category": "고유 옵션", "option": entry["option"], "reason": entry["reason"]} for entry in analysis["ignored"]
                    ] + [{"category": "고유 옵션", "option": option, "reason": "unsupported"} for option in analysis["unsupported"]],
                    "weights": notes,
                    "breakdown": [{"key": "special", "label": "고유 옵션", "raw_score": round(value, 2), "weighted_score": total_item_score}],
                    "benchmarks": {"special": benchmark},
                    "star_score": 0,
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

        if is_noljang: adv_star_score = get_starforce_score(SCORING.starforce.base_star, item_req_level)
        elif is_superior: adv_star_score = get_starforce_score(star, item_req_level) * weights.superior_star
        else: adv_star_score = get_starforce_score(star, item_req_level)

        soul_analysis, soul_val, soul_score, benchmarks, add_recognized = None, 0.0, 0.0, None, []
        if is_weapon:
            # 무기류는 공격력% 단위(app/weapon.py). 무기는 추옵·스타포스·윗잠·에디·소울, 보조무기·엠블렘은 윗잠·에디만 평가합니다.
            group = part_group(slot, part)
            if group == "weapon":
                add_raw_score, add_recognized = weapon_add_value(item.get("item_add_option"), weapon_context, char_level)
                add_score = add_raw_score * weights.weapon_add
                soul_analysis = analyze_weapon_item(item, "soul", weapon_context, char_level)
                soul_val = soul_analysis["score"]
                soul_score = (soul_val * weights.soul) if soul_val > 0 else 0
            else:
                add_raw_score = add_score = adv_star_score = 0.0
            pot_analysis = analyze_weapon_item(item, "potential", weapon_context, char_level)
            pot_val = pot_analysis["score"]
            pot_score = (pot_val * weights.potential) if pot_val > 0 else 0
            eddy_analysis = analyze_weapon_item(item, "additional_potential", weapon_context, char_level)
            eddy_val = eddy_analysis["score"]
            eddy_score = (eddy_val * weights.additional) if eddy_val > 0 else 0
            # 캐릭터별 최상 조합을 기준점으로 둡니다 (가이드의 축 비율과 이후 등급 컷에 사용).
            tier = tier_of(name)
            benchmarks = {
                "tier": tier,
                "pot": round(best_lines_total("potential", group, weapon_context, char_level, tier) * weights.potential, 2),
                "pot_additional": round(best_lines_total("additional_potential", group, weapon_context, char_level, tier) * weights.additional, 2),
            }
            if group == "weapon":
                benchmarks["soul"] = round(best_lines_total("soul", group, weapon_context, char_level, tier) * weights.soul, 2)
        else:
            actual_add_급수 = calculate_item_score(item.get("item_add_option", {}), char_class, main_stat=main_stat)
            add_raw_score = actual_add_급수
            add_score = get_advanced_add_score(actual_add_급수, item_req_level, part, char_class, target_multiplier=target_multiplier)
            pot_analysis = analyze_potential_score(item, "potential", char_class, char_level, main_stat=main_stat, context=context)
            pot_val = pot_analysis["score"]
            pot_score = (pot_val * weights.potential) if pot_val > 0 else 0
            eddy_analysis = analyze_potential_score(item, "additional_potential", char_class, char_level, main_stat=main_stat, context=context)
            eddy_val = eddy_analysis["score"]
            eddy_score = (eddy_val * weights.additional) if eddy_val > 0 else 0

        _log_unsupported_options(name, "potential", pot_analysis, unsupported)
        _log_unsupported_options(name, "additional_potential", eddy_analysis, unsupported)
        def with_weighted_values(analysis, category, weighted_total):
            raw_total = sum(entry["value"] for entry in analysis["recognized"])
            effective_weight = (weighted_total / raw_total) if raw_total else 0
            return [
                {
                    **entry,
                    "category": category,
                    "weighted_value": round(entry["value"] * effective_weight, 2),
                    "weight": round(effective_weight, 3),
                }
                for entry in analysis["recognized"]
            ]

        recognized_options = (
            with_weighted_values(pot_analysis, "윗잠재", pot_score)
            + with_weighted_values(eddy_analysis, "에디셔널", eddy_score)
            + (with_weighted_values(soul_analysis, "소울", soul_score) if soul_analysis else [])
            + with_weighted_values({"recognized": add_recognized}, "추가옵션", add_score)
        )

        def unrecognized_entries(analysis, category):
            # 점수에 반영되지 않은 옵션과 그 이유. 화면에서 "왜 점수가 낮은지"를 보여주는 근거입니다.
            return [
                {"category": category, "option": entry["option"], "reason": entry["reason"]} for entry in analysis["ignored"]
            ] + [{"category": category, "option": option, "reason": "unsupported"} for option in analysis["unsupported"]]

        unrecognized_options = unrecognized_entries(pot_analysis, "윗잠재") + unrecognized_entries(eddy_analysis, "에디셔널")
        if soul_analysis:
            unrecognized_options += unrecognized_entries(soul_analysis, "소울")

        total_item_score = add_score + pot_score + eddy_score + adv_star_score + soul_score
        group = part_group(slot, part) if is_weapon else None
        detail = {"pot": round(pot_score, 1), "pot_additional": round(eddy_score, 1)}
        breakdown = [
            {"key": "pot", "label": "윗잠재", "raw_score": round(max(pot_val, 0), 2), "weighted_score": round(pot_score, 2)},
            {"key": "pot_additional", "label": "에디셔널", "raw_score": round(max(eddy_val, 0), 2), "weighted_score": round(eddy_score, 2)},
        ]
        if group != "secondary" and group != "emblem":
            detail.update(add=round(add_score, 1), star=round(adv_star_score, 1))
            breakdown.insert(0, {"key": "add", "label": "추가옵션", "raw_score": round(add_raw_score, 2), "weighted_score": round(add_score, 2)})
            breakdown.append({"key": "star", "label": "스타포스", "raw_score": star, "weighted_score": round(adv_star_score, 2), "star": star})
        if group == "weapon":
            detail["soul"] = round(soul_score, 1)
            breakdown.insert(3, {"key": "soul", "label": "소울", "raw_score": round(max(soul_val, 0), 2), "weighted_score": round(soul_score, 2)})

        if pot_val != -1:
            guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part, total_item_score, name, item_req_level, is_noljang, benchmarks)
            guide_tag, guide_message = split_guide(guide_text)
            evaluate_list.append({
                "is_wse": is_weapon, "is_special": False, "is_noljang": is_noljang, "slot": slot, "part": part, "name": name, "icon": icon, "star": star, "level": item_req_level,
                "total_score": round(total_item_score, 2),
                "guide": guide_text,
                "guide_tag": guide_tag,
                "guide_message": guide_message,
                "detail": detail,
                "raw_options": {**raw_options_dict, "soul_options": [e["option"] for e in (soul_analysis or {}).get("recognized", [])] + (soul_analysis or {}).get("unsupported", [])},
                "scoring_basis": {
                    "main_stat": main_stat_label,
                    "recognized_options": recognized_options,
                    "unrecognized_options": unrecognized_options,
                    "weights": weight_notes(item_req_level) + ([f"무기류 공격력% 단위 환산 · 보공 1% = 공 {weapon_context.boss_per_pct:.3g}%, 유효 스탯 1% = 공 {weapon_context.stat_pct_unit:.3g}%, 기준점은 캐릭터별 최상 조합"] if is_weapon else []),
                    "breakdown": breakdown,
                    "benchmarks": benchmarks,
                    "star_score": round(adv_star_score, 2),
                },
            })

    return evaluate_list


def generate_overall_review(evaluate_list):
    total_scores = [item["total_score"] for item in evaluate_list if not item["is_special"]]
    avg_score = sum(total_scores) / len(total_scores) if total_scores else 0
    all_sorted_results = sorted(evaluate_list, key=lambda x: x["total_score"])
    worst_item = next((item for item in all_sorted_results if not item.get("is_special")), None)

    # 랭크 컷과 문구는 app/data/grades.toml 의 overall 표. 평균이 min 이상인 첫 항목, 없으면 마지막 항목을 씁니다.
    entry = next((candidate for candidate in GRADES.overall if avg_score >= candidate["min"]), GRADES.overall[-1])
    rank, comment = entry["rank"], entry["comment"]
    keyword = entry.get("weapon_keyword")
    if keyword and any(keyword in item.get("name", "") for item in evaluate_list):
        comment = entry["with_weapon"]

    overall_review = {
        "rank": rank, "avg_score": round(avg_score, 1), "main_comment": comment,
        "priority_target": worst_item["name"] if worst_item else "없음",
        "next_step": f"'{worst_item['name']}' 부위의 보완이 가장 시급합니다." if worst_item else ""
    }

    return overall_review, all_sorted_results


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
            "limitations": ["필수 스탯 누락 또는 특수 직업·복수 부스탯은 조합 최적화 미지원: 기존 장비 점수 기준 유지"]}
    profile["context"].sources["equipment_basis"] = "selected_preset"
    profile["context"].sources["final_stats_basis"] = "projected_saved_presets" if optimized else "currently_equipped"
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
    overall_review, all_sorted_results = generate_overall_review(evaluate_list)

    return {
        "character": character_name,
        "class": char_class,
        "level": char_level,
        "character_image": char_image,
        "combat_power": extract_combat_power(stat_data),
        "best_preset": best_preset_idx,
        "profile": {
            **{key: value for key, value in profile.items() if key not in ("context", "weapon")},
            "context": profile["context"].as_dict(),
            "weapon": profile["weapon"].as_dict(),
        },
        "overall": overall_review,
        "results": all_sorted_results,
    }
