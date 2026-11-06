import logging
import math
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
    get_starforce_score,
    max_starforce,
    noljang_option,
    starforce_benchmark_option,
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
from app.set_projection import MEMBERS, PITCHED
from app.spec_up import attach_spec_up
from app.special import (
    analyze_special_lines,
    best_total,
    is_seed_ring,
    ring_score,
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
    "total", "add", "pot", "eddy", "starscore", "star", "star_next", "max_star", "at_cap", "level", "noljang", "limited",
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


def build_guide_context(scores, star_val, part_name, item_name, total_score, item_req_level, is_noljang, benchmarks=None, ratio=None) -> dict:
    """규칙 표가 비교할 값들을 계산합니다. 파생값(최대 스타포스, 최저 축, 목표치)은 코드가 맡고 규칙 표는 조건과 문구만 가집니다."""
    max_star = max_starforce(item_req_level, item_name, any(k in item_name for k in GUIDES.names.superior))

    axes = GUIDES.axes
    add_evaluated = not any(k in part_name for k in axes.no_add_parts)
    star_evaluated = not any(k in part_name for k in axes.no_star_parts)
    eval_indices = [1, 2] + ([0] if add_evaluated else []) + ([3] if star_evaluated else [])
    # 무기류는 캐릭터별 최상 조합(benchmarks)을 윗잠·에디 기준점으로 씁니다.
    marks = list(axes.benchmarks)
    for index, key in ((0, "add"), (1, "pot"), (2, "pot_additional"), (3, "star")):
        if benchmarks and benchmarks.get(key):
            marks[index] = benchmarks[key]
    ratios = [scores[i] / marks[i] for i in range(4)]
    if ratio is None:  # 직접 호출 시: 평가 축의 기준점 합 대비 총점
        ratio = round(100 * total_score / sum(marks[i] for i in eval_indices), 2)
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
        # 기준 만점 대비 비율. 등급·랭크와 같은 기준이라 부위(축 수·캐릭터별 기준점)에 관계없이 비교할 수 있습니다.
        "ratio": ratio, "add_ratio": ratios[0], "pot_ratio": ratios[1], "eddy_ratio": ratios[2],
        "target_star_330": min(
            targets.star_330_low if item_req_level <= targets.star_330_level_split else targets.star_330_high, max_star
        ),
        "target_star_17": min(targets.star_mid, max_star),
        "target_star_21": targets.star_step if max_star >= targets.star_step else max_star,
    }


def exceptional_axis(item, part_name):
    """익셉셔널 강화 축: 대상 부위(potential.toml [exceptional].parts)면 (강화 횟수, 점수)를, 아니면 None 을 돌려줍니다."""
    config = POTENTIAL.exceptional
    if not any(keyword in (part_name or "") for keyword in config.parts):
        return None
    try:
        count = int((item.get("item_exceptional_option") or {}).get("exceptional_upgrade") or 0)
    except (TypeError, ValueError):
        count = 0
    return count, round(count * config.points_per_upgrade, 2)


def get_special_part_guide(total_score, part_name, item_name, expired=False):
    return select_guide(GUIDES.special_rule, {"total": total_score, "expired": expired}, GUIDES.names.raw, item_name, part_name)


def get_dynamic_guide(scores, star_val, part_name, total_score, item_name, item_req_level, is_noljang=False, benchmarks=None, ratio=None):
    context = build_guide_context(scores, star_val, part_name, item_name, total_score, item_req_level, is_noljang, benchmarks, ratio)
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
    weapon_context = (profile or {}).get("weapon") or WeaponContext(context)

    def weight_notes(item_req_level, star_from_stats=False):
        star_weight = round(1.0 + (item_req_level - SCORING.starforce.reference_level) / SCORING.starforce.level_weight_divisor, 3)
        notes = [
            f"추가옵션 급수 ÷ {item_req_level}제 불꽃 최대 급수 × 100 × {weights.add:g}",
            "윗잠재·에디셔널 환산값 합 (곱상수 없음)",
            (f"스타포스: 스타포스로 오른 스탯·공/마를 환산 ÷ {item_req_level}제 22성 값 × 100, "
             f"23성부터 100 + {SCORING.starforce.benchmark.over_star_log_weight:g} × ln(별 − 21)"
             if star_from_stats
             else f"스타포스 레벨 가중치 × {star_weight if item_req_level > 0 else 0} (기준 만점은 최대 스타포스)"),
        ]
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
            # 훈장·뱃지·포켓·칭호: 고정 옵션을 유효 스탯% 로 환산하고 부위별 최상(캐릭터별 환산)을 100 으로 둡니다 (app/special.py).
            # 시드링(특수 반지·예비 특수 반지): 종류와 레벨로 점수를 냅니다.
            expired, benchmark, lines = False, 0.0, []
            axis_key, axis_label = "special", "고유 옵션"
            if is_seed_ring(item):
                part = POTENTIAL.special.ring_part
                total_item_score, entry = ring_score(item)
                value, analysis = total_item_score, {"recognized": [entry], "ignored": [], "unsupported": []}
                notes = [f"시드링 · 종류 가중치 × 레벨 ÷ {POTENTIAL.special.ring.max_level} (리스트레인트·컨티뉴어스 만렙 = 100)"]
            elif part in POTENTIAL.special.add_option_parts:
                # 훈장·포켓: 추옵 로직. 환생의 불꽃 급수(캐릭터 효율 계수)를
                # 최상 아이템 레벨(potential.toml [special.best] flame_level)의 불꽃 최대 급수와 비교합니다.
                axis_key, axis_label = "add", "추가옵션"
                flame_level = POTENTIAL.special.best.raw[part]["flame_level"]
                value = calculate_item_score(item.get("item_add_option") or {}, char_class, main_stat=main_stat, context=context)
                benchmark = calculate_item_score(flame_best_add_option(flame_level, char_class, main_stat, context.sub_names, context),
                                                 char_class, main_stat=main_stat, context=context)
                total_item_score = round(100 * value / benchmark, 2) if benchmark > 0 else 0.0
                analysis = {"recognized": [], "ignored": [], "unsupported": []}
                notes = [f"추가옵션 급수 {value:g} ÷ {flame_level}제 불꽃 최대 급수 {benchmark:g} × 100 (기본 옵션은 보지 않음)"]
            else:
                lines, expired = special_lines(item, weapon_context)
                analysis = analyze_special_lines(lines, weapon_context, char_level)
                value = analysis["score"]
                benchmark = best_total(part, weapon_context, char_level)
                total_item_score = round(100 * value / benchmark, 2) if benchmark > 0 else 0.0
                _log_unsupported_options(name, "special", analysis, unsupported)
                notes = [f"고유 옵션 유효 스탯% 합 ÷ 이 캐릭터의 부위 최상({benchmark:.2f}) × 100"] + context.notes()
            factor = (total_item_score / value) if value else 0
            special_guide = get_special_part_guide(total_item_score, part, name, expired)
            special_guide_tag, special_guide_message = split_guide(special_guide)
            if expired:
                notes.insert(0, "칭호 옵션 적용 기간 만료 · 스탯을 인정하지 않습니다")
            exceptional = exceptional_axis(item, part)
            special_detail = {axis_key: round(total_item_score, 1)}
            special_breakdown = [{"key": axis_key, "label": axis_label, "raw_score": round(value, 2), "weighted_score": total_item_score}]
            if exceptional and exceptional[0] > 0:  # 강화가 없으면 축을 보여주지 않습니다
                upgrades, points = exceptional
                total_item_score = round(total_item_score + points, 2)
                special_detail["exceptional"] = points
                special_breakdown.append({"key": "exceptional", "label": "익셉셔널", "raw_score": upgrades, "weighted_score": points})
                notes.append(f"익셉셔널 강화 1회당 {POTENTIAL.exceptional.points_per_upgrade:g}점 (현재 {upgrades}회)")
            evaluate_list.append({
                "is_wse": False, "is_special": True, "is_noljang": False, "slot": slot, "part": part, "name": name, "icon": icon, "shape_icon": shape_icon, "star": 0, "level": item_req_level,
                "total_score": total_item_score,
                "score_ratio": total_item_score,
                "benchmark_total": 100.0,
                # 훈장·뱃지는 등급을 매기지 않고, 뱃지는 점수도 보여주지 않으며 전체 랭크에서 뺍니다 (potential.toml [special]).
                "graded": part not in POTENTIAL.special.no_grade_parts,
                "scored": part not in POTENTIAL.special.no_score_parts,
                "guide": special_guide,
                "guide_tag": special_guide_tag,
                "guide_message": special_guide_message,
                "detail": special_detail,
                "bar_max": bar_max(item_req_level, name),
                "raw_options": {**raw_options_dict, "fixed_options": lines, "expired": expired, "special_ring_level": item.get("special_ring_level")},
                "scoring_basis": {
                    "main_stat": main_stat_label,
                    "recognized_options": [
                        {**entry, "category": "고유 옵션", "weighted_value": round(entry["value"] * factor, 2), "weight": round(factor, 3)}
                        for entry in analysis["recognized"]
                    ] + ([
                        # 훈장·포켓은 추가옵션 급수가 점수라 줄별 급수를 함께 보여줍니다.
                        {**entry, "category": "추가옵션", "weighted_value": round(entry["value"] * factor, 2), "weight": round(factor, 3)}
                        for entry in add_option_entries(item.get("item_add_option") or {}, char_class, main_stat=main_stat, context=context)
                    ] if axis_key == "add" else []),
                    "unrecognized_options": [
                        {"category": "고유 옵션", "option": entry["option"], "reason": entry["reason"]} for entry in analysis["ignored"]
                    ] + [{"category": "고유 옵션", "option": option, "reason": "unsupported"} for option in analysis["unsupported"]],
                    "weights": notes,
                    "breakdown": special_breakdown,
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
        # 140제 이상 방어구·장신구: 22성 이하는 스타포스로 오른 실제 스탯을 주스탯% 로 환산해 22성 합계 대비 × 100,
        # 23성 이상은 100 + 로그 가산(scoring.toml starforce.benchmark). 놀장은 스타포스 옵션이 비고 스탯이 작과 같은 칸에 있어
        # 놀장 표로 계산합니다. 슈페리얼은 실제 스탯이라 배수가 없습니다.
        star_from_stats = False
        star_benchmark_option = None if any(k in slot for k in special_items.wse_slots) else starforce_benchmark_option(
            item_req_level, main_stat, context.sub_names, part)
        if star_benchmark_option:
            if is_noljang or item.get("starforce_scroll_flag") == "사용":
                source = noljang_option(item_req_level, star, main_stat, context.sub_names)
            else:
                source = item.get("item_starforce_option")
            star_value = calculate_item_score(source or {}, char_class, main_stat=main_stat, context=context)
            star_best = calculate_item_score(star_benchmark_option, char_class, main_stat=main_stat, context=context)
            if star_best > 0:
                adv_star_score, star_from_stats = 100 * star_value / star_best, True
                sf_bench = SCORING.starforce.benchmark
                if star > sf_bench.benchmark_star and not is_noljang and not is_superior:
                    adv_star_score = 100 + sf_bench.over_star_log_weight * math.log(star - sf_bench.benchmark_star + 1)
        # 무기·보조무기(엠블렘 제외): 같은 방식, 단위만 공% (무기 성능 환산). 22성 값은 이 아이템의 순수 공/마로 계산합니다.
        if any(k in slot for k in ("무기", "보조무기")) and not is_superior:
            attack_key = "magic_power" if main_stat == "int" else "attack_power"
            pure = safe_int((item.get("item_base_option") or {}).get(attack_key)) + safe_int(etc_ops.get(attack_key))
            weapon_like = "보조무기" not in slot or safe_int((item.get("item_base_option") or {}).get(attack_key)) >= \
                SCORING.starforce.weapon.weapon_like_min_attack
            bench_option = weapon_starforce_option(item_req_level, SCORING.starforce.benchmark.benchmark_star, pure, main_stat,
                                                   context.sub_names, weapon_like)
            if bench_option:
                star_value = weapon_add_value(item.get("item_starforce_option") or {}, weapon_context, char_level)[0]
                star_best = weapon_add_value(bench_option, weapon_context, char_level)[0]
                if star_best > 0:
                    adv_star_score, star_from_stats = 100 * star_value / star_best, True
                    sf_bench = SCORING.starforce.benchmark
                    if star > sf_bench.benchmark_star:
                        adv_star_score = 100 + sf_bench.over_star_log_weight * math.log(star - sf_bench.benchmark_star + 1)

        soul_analysis, soul_val, soul_score, benchmarks, add_recognized = None, 0.0, 0.0, None, []
        add_attack_tier = None  # 무기 추옵 공/마의 불꽃 단계 (화면 "N추(M단계)")
        axes = GUIDES.axes
        add_evaluated = not any(k in part or k in slot for k in axes.no_add_parts)
        star_evaluated = not any(k in part or k in slot for k in axes.no_star_parts)
        if is_weapon:
            # 무기류는 공격력% 단위(app/weapon.py). 무기는 추옵·스타포스·윗잠·에디·소울, 보조무기·엠블렘은 윗잠·에디만 평가합니다.
            group = part_group(slot, part)
            if group == "weapon":
                # 무기 추옵 = 공% 환산값 ÷ 레벨별 불꽃 최대 조합 × 100 × weights.add (방어구와 같은 척도)
                add_raw_score, add_recognized = weapon_add_value(item.get("item_add_option"), weapon_context, char_level)
                attack_amount = float((item.get("item_add_option") or {}).get("magic_power" if weapon_context.attack_label == "마력" else "attack_power") or 0)
                add_attack_tier = weapon_attack_tier(item, weapon_context.attack_label, attack_amount) if attack_amount else None
                add_best = weapon_add_best(item, weapon_context, char_level)
                add_score = (100 * add_raw_score / add_best * weights.add) if add_best > 0 else 0.0
                soul_analysis = analyze_weapon_item(item, "soul", weapon_context, char_level)
                soul_val = soul_analysis["score"]
                soul_score = max(soul_val, 0)
            else:
                add_raw_score = add_score = 0.0
                # 엠블렘은 스타포스 축이 없습니다. 보조무기는 위의 스탯 방식, 표 밖이면 200제 장비 기준 레벨 가중 공식입니다.
                if group != "secondary":
                    adv_star_score = 0.0
                elif not star_from_stats:
                    adv_star_score = get_starforce_score(star, POTENTIAL.weapon.secondary_star_level)
            pot_analysis = analyze_weapon_item(item, "potential", weapon_context, char_level)
            pot_val = pot_analysis["score"]
            pot_score = max(pot_val, 0)
            eddy_analysis = analyze_weapon_item(item, "additional_potential", weapon_context, char_level)
            eddy_val = eddy_analysis["score"]
            eddy_score = max(eddy_val, 0)
            # 캐릭터별 최상 조합을 기준점으로 둡니다 (가이드의 축 비율과 이후 등급 컷에 사용).
            tier = tier_of(name)
            benchmarks = {
                "tier": tier,
                "pot": best_lines_total("potential", group, weapon_context, char_level, tier),
                "pot_additional": best_lines_total("additional_potential", group, weapon_context, char_level, tier),
            }
            if group == "weapon":
                benchmarks["soul"] = best_lines_total("soul", group, weapon_context, char_level, tier)
                benchmarks["add"] = round(100 * weights.add, 2)
        else:
            # 추옵이 없는 부위(반지·어깨장식·기계 심장)는 추옵 축 자체를 두지 않습니다.
            # 추옵 축 = 급수 ÷ 그 레벨의 불꽃 최대 급수 × 100 × weights.add. 기준 만점은 100 × weights.add 입니다.
            # 급수의 공/마·올스탯%·부스탯 계수는 잠재 환산과 같은 캐릭터 효율(context)입니다.
            if add_evaluated:
                add_option = item.get("item_add_option") or {}
                add_raw_score = calculate_item_score(add_option, char_class, main_stat=main_stat, context=context)
                add_recognized = add_option_entries(add_option, char_class, main_stat=main_stat, context=context)
                add_best = calculate_item_score(flame_best_add_option(item_req_level, char_class, main_stat, context.sub_names, context),
                                                char_class, main_stat=main_stat, context=context)
                add_score = (100 * add_raw_score / add_best * weights.add) if add_best > 0 else 0.0
            benchmarks = {
                "add": round(100 * weights.add, 2),
                "pot": best_armor_total("potential", part, context, char_level, item_req_level),
                "pot_additional": best_armor_total("additional_potential", part, context, char_level, item_req_level),
            }
            pot_analysis = analyze_potential_score(item, "potential", char_class, char_level, main_stat=main_stat, context=context)
            pot_val = pot_analysis["score"]
            pot_score = max(pot_val, 0)
            eddy_analysis = analyze_potential_score(item, "additional_potential", char_class, char_level, main_stat=main_stat, context=context)
            eddy_val = eddy_analysis["score"]
            eddy_score = max(eddy_val, 0)

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
        # 스타포스 기준 만점 = 최대 스타포스(130제 이하 20 · 140제 이상 30 · 제네시스 22 · 데스티니 25 · 슈페리얼 15)의 점수
        star_level = POTENTIAL.weapon.secondary_star_level if group == "secondary" else item_req_level
        if star_from_stats:
            benchmarks["star"] = 100.0  # 22성 스탯 환산 = 100
        else:
            star_best = get_starforce_score(max_starforce(item_req_level, name, is_superior), star_level)
            benchmarks["star"] = round(star_best * (weights.superior_star if is_superior else 1), 2)
        detail = {"pot": round(pot_score, 1), "pot_additional": round(eddy_score, 1)}
        breakdown = [
            {"key": "pot", "label": "윗잠재", "raw_score": round(max(pot_val, 0), 2), "weighted_score": round(pot_score, 2)},
            {"key": "pot_additional", "label": "에디셔널", "raw_score": round(max(eddy_val, 0), 2), "weighted_score": round(eddy_score, 2)},
        ]
        if add_evaluated:
            detail["add"] = round(add_score, 1)
            breakdown.insert(0, {"key": "add", "label": "추가옵션", "raw_score": round(add_raw_score, 2), "weighted_score": round(add_score, 2)})
        if star_evaluated:
            detail["star"] = round(adv_star_score, 1)
            breakdown.append({"key": "star", "label": "스타포스", "raw_score": star, "weighted_score": round(adv_star_score, 2), "star": star})
        if group == "weapon":
            detail["soul"] = round(soul_score, 1)
            breakdown.insert(3, {"key": "soul", "label": "소울", "raw_score": round(max(soul_val, 0), 2), "weighted_score": round(soul_score, 2)})
        # 기준 만점 = 평가하는 축의 기준점 합. 등급·랭크는 총점이 아니라 이 비율로 매깁니다 (익셉셔널은 만점에 넣지 않는 가산).
        benchmark_total = benchmarks["pot"] + benchmarks["pot_additional"] + benchmarks.get("soul", 0)
        if add_evaluated:
            benchmark_total += benchmarks["add"]
        if star_evaluated:
            benchmark_total += benchmarks["star"]
        exceptional = exceptional_axis(item, part) if not is_weapon else None
        if exceptional and exceptional[0] > 0:  # 강화가 없으면 축을 보여주지 않습니다
            upgrades, exceptional_points = exceptional
            total_item_score += exceptional_points
            detail["exceptional"] = exceptional_points
            breakdown.append({"key": "exceptional", "label": "익셉셔널", "raw_score": upgrades, "weighted_score": exceptional_points})

        if pot_val != -1:
            score_ratio = round(100 * total_item_score / benchmark_total, 2) if benchmark_total > 0 else 0.0
            guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part, total_item_score, name, item_req_level, is_noljang, benchmarks, score_ratio)
            guide_tag, guide_message = split_guide(guide_text)
            evaluate_list.append({
                "is_wse": is_weapon, "is_special": False, "is_noljang": is_noljang, "slot": slot, "part": part, "name": name, "icon": icon, "shape_icon": shape_icon, "star": star, "level": item_req_level,
                "total_score": round(total_item_score, 2),
                "score_ratio": score_ratio,
                "benchmark_total": round(benchmark_total, 2),
                "graded": True,
                "scored": True,
                "guide": guide_text,
                "guide_tag": guide_tag,
                "guide_message": guide_message,
                "detail": detail,
                "bar_max": bar_max(item_req_level, name, part, benchmarks),
                "raw_options": {**raw_options_dict, "soul_grade": item.get("soul_potential_grade"),
                                "soul_stage": item.get("soul_potential_amplified_grade"), "soul_options": [e["option"] for e in (soul_analysis or {}).get("recognized", []) if not e.get("hidden")] + (soul_analysis or {}).get("unsupported", [])},
                "scoring_basis": {
                    "main_stat": main_stat_label,
                    "recognized_options": recognized_options,
                    "unrecognized_options": unrecognized_options,
                    "add_attack_tier": add_attack_tier,
                    "weights": weight_notes(item_req_level, star_from_stats)
                    + ([f"무기류 공격력% 단위 환산 · 보공 1% = 공 {weapon_context.boss_per_pct:.3g}%, 유효 스탯 1% = 공 {weapon_context.stat_pct_unit:.3g}%, 기준점은 캐릭터별 최상 조합"] if is_weapon else [])
                    + ([f"익셉셔널 강화 1회당 {POTENTIAL.exceptional.points_per_upgrade:g}점 (현재 {exceptional[0]}회)"] if exceptional and exceptional[0] > 0 else []),
                    "breakdown": breakdown,
                    "benchmarks": benchmarks,
                    "star_score": round(adv_star_score, 2),
                },
            })

    return evaluate_list


def generate_overall_review(evaluate_list):
    # 전체 랭크는 기준 만점 대비 비율의 평균입니다. 특수부위·시드링은 포함하고 칭호와 점수를 매기지 않는 부위(뱃지)는 뺍니다.
    counted = [item for item in evaluate_list if item["part"] != "칭호" and item.get("scored", True)]
    avg_score = sum(item["score_ratio"] for item in counted) / len(counted) if counted else 0
    all_sorted_results = sorted(evaluate_list, key=lambda x: x["score_ratio"])

    # 랭크 컷은 app/data/grades.toml 의 overall 표. 평균이 min 이상인 첫 항목, 없으면 마지막 항목을 씁니다.
    entry = next((candidate for candidate in GRADES.overall if avg_score >= candidate["min"]), GRADES.overall[-1])
    overall_review = {"rank": entry["rank"], "avg_score": round(avg_score, 1)}

    return overall_review, all_sorted_results


BAR_MAX_FROM_BENCHMARK = ("모자", "장갑")   # 윗잠재 막대 만점을 아이템 기준 만점(쿨감·크뎀 조합)으로 쓰는 부위


def bar_max(level: int, name: str, part: str = "", benchmarks: dict | None = None) -> dict:
    """아이템 카드 막대가 100% 가 되는 값 (grades.toml stat_bar_max). 윗잠·에디는 레벨, 익셉셔널은 칠흑 여부로 고릅니다.
    모자·장갑의 윗잠재는 레벨 고정값 대신 그 아이템의 기준 만점을 씁니다."""
    m = GRADES.stat_bar_max
    low = level <= m.low_level_max
    pot = m.pot_low if low else m.pot
    if part in BAR_MAX_FROM_BENCHMARK and (benchmarks or {}).get("pot"):
        pot = benchmarks["pot"]
    return {
        "add": m.add, "star": m.star, "soul": m.soul, "special": m.special,
        "pot": pot,
        "pot_additional": m.pot_additional_low if low else m.pot_additional,
        "exceptional": m.exceptional_pitched if " ".join((name or "").split()) in MEMBERS[PITCHED] else m.exceptional,
    }


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
