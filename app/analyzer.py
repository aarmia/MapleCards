import logging
import re
from collections import Counter

from app.calculator import (
    analyze_potential_score,
    analyze_weapon_potential_score,
    calculate_item_score,
    calculate_potential_score,
    calculate_weapon_add_option_score,
    get_advanced_add_score,
    get_main_stat,
    get_potential_options,
    get_starforce_score,
)
from app.scoring_config import SCORING

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


def get_best_preset(item_data: dict, class_name: str, char_level: int) -> int:
    preset_scores = {1: 0.0, 2: 0.0, 3: 0.0}

    for i in range(1, 4):
        preset_items = item_data.get(f"item_equipment_preset_{i}", [])
        if not preset_items:
            continue

        total_score = 0.0
        for item in preset_items:
            score = calculate_potential_score(item, "potential", class_name, char_level)
            add_score = calculate_potential_score(item, "additional_potential", class_name, char_level)

            if score > 0: total_score += score
            if add_score > 0: total_score += add_score

        preset_scores[i] = total_score

    return max(preset_scores, key=preset_scores.get)


def get_special_part_guide(total_score, part_name, item_name):
    if "포켓 아이템" in part_name:
        if total_score >= 280: return "👑 포켓 부위 종결급 추옵입니다."
        if total_score >= 200: return "✅ 준수한 가성비 성배입니다."
        return "🚨 더 높은 급수의 성배(80급 이상)로 교체를 추천합니다."
    if "뱃지" in part_name:
        if any(k in item_name for k in ["창세", "칠요"]): return "👑 상위 티어 뱃지를 착용 중입니다."
        return "💡 칠요의 뱃지 혹은 창세의 뱃지로의 업그레이드 목표를 잡으세요."
    if "훈장" in part_name:
        if any(k in item_name for k in ["칠요", "카루타", "멸살"]): return "👑 종결급 훈장입니다."
        return "💡 더 높은 등급의 훈장 획득을 권장합니다."
    return "✅ 해당 부위는 표준 성능을 보여주고 있습니다."


def get_dynamic_guide(scores, star_val, part_name, total_score, item_name, item_req_level, is_noljang=False):
    if is_noljang:
        return "💎 [놀라운 장비 강화 아이템] 놀라운 장비 강화 아이템을 착용중입니다. 교체를 원하신다면 명백한 상위 아이템과 베이스의 아이템으로 교체하세요."

    target_hearts = ["리튬 하트", "페어리 하트", "티타늄 하트", "플라즈마 하트"]
    upgrade_hearts = ["리튬 하트", "페어리 하트", "티타늄 하트"]
    black_heart = ["블랙 하트"]
    if any(heart in item_name for heart in upgrade_hearts):
        if total_score >= 125:
            return "🚨 [업그레이드 권장] 현재 하트는 성능 한계가 명확합니다. 플라즈마 하트로의 강화를 고려하세요."
    if any(heart in item_name for heart in target_hearts):
        if total_score >= 275:
            return "🚨 [교체 권장] 현재 하트는 성능 한계가 명확합니다. 컴플리트 언더컨트롤로의 교체를 고려해야할 시기입니다."
    if any(heart in item_name for heart in black_heart):
        return "🚨 [미지원] 블랙 하트는 점수 환산을 지원하지 않습니다."

    if "타일런트" in item_name:
        if total_score >= 280:
            return "✨ [교체 권장] 어느정도 완성된 슈페리얼 아이템입니다. 교체를 원하신다면 지금의 아이템보다 상위의 아이템으로 교체를 추천합니다."
        else:
            return "⚠️ [교체 권장] 하나에서 두개 정도 부분이 아쉬운 슈페리얼 아이템입니다. 강화를 하시기보다는 동급이나 상위의 아이템으로 교체를 추천합니다."

    max_star_possible = 30
    if item_req_level < 118:
        max_star_possible = 10
    elif item_req_level < 128:
        max_star_possible = 15
    elif item_req_level < 138:
        max_star_possible = 20

    labels = ["추가옵션", "윗잠재", "에디셔널", "스타포스"]
    max_bench = [110, 108, 40, 105]

    eval_indices = [1, 2]
    if not any(k in part_name for k in ["반지", "어깨장식", "기계 심장", "보조무기", "엠블렘"]):
        eval_indices.append(0)
    if not any(k in part_name for k in ["보조무기", "엠블렘"]):
        eval_indices.append(3)

    ratios = [scores[i] / max_bench[i] for i in range(4)]
    indexed_ratios = [(ratios[i], labels[i]) for i in eval_indices]
    indexed_ratios.sort(key=lambda x: x[0])
    worst_label = indexed_ratios[0][1]
    next_label = indexed_ratios[1][1] if len(indexed_ratios) > 1 else worst_label
    min_ratio = indexed_ratios[0][0]

    if total_score >= 350:
        if star_val >= 22 or star_val >= max_star_possible:
            if 0 in eval_indices:
                target_add = 103.5 if item_req_level <= 200 else 101
                if scores[0] < target_add:
                    return "✨ [추가옵션 강화 / 전승] 추가옵션이 완성되지 않았습니다. 강화하거나, 전승을 고려하세요."
            if scores[1] < 101:
                return "✨ [잠재능력 강화 / 전승] 베이스는 완벽합니다. 잠재능력 수치를 극대화하기 위해 강화하거나, 전승을 고려하세요."
            elif scores[2] <= 43:
                return "✨ [에디/교체 권장] 베이스는 완벽하지만, 에디셔널을 강화하거나 상위 매물로 교체하세요"
            return f"👑 [종결] 완벽한 장비입니다. {star_val + 1}성 도전 외엔 투자가 무의미합니다."
        return f"🔍 [미세조정] 종결급이나 '{worst_label}'이(가) 평균보다 낮습니다."

    elif total_score >= 300:
        if total_score >= 330:
            if star_val >= 23:
                return "✨ [종결 / 상위매물 교체] 23성 이상 도달한 장비입니다. 추가 강화보다는 이대로 사용하시거나, 상위 등급의 베이스 아이템 매물로 교체하는 것을 추천합니다."
            target_star = 23 if item_req_level <= 160 else 22
            target_star = min(target_star, max_star_possible)
            if star_val < target_star and 3 in eval_indices:
                return f"⭐ [스타포스 권장] 체급에 걸맞은 '{target_star}성' 달성이 시급합니다. 우선적으로 {star_val + 1}성 강화를 최우선하세요."
            return f"💎 [준종결] 종결급 체급입니다. 마지막 퍼즐인 '{worst_label}' 수치 보완을 추천합니다."

        if 3 in eval_indices:
            if star_val >= max_star_possible:
                return f"✅ [강화 한계] 최대치까지 강화되었습니다. 이제 부족한 '{worst_label}'에 집중하세요."
            if star_val <= 18:
                target_star = 21 if max_star_possible >= 21 else max_star_possible
                return f"⚔️ [단계적 강화] 베이스가 훌륭합니다. 우선 '{target_star}성 안착'을 목표로 강화를 추천합니다."
            if star_val < 22:
                if min_ratio < 0.25:
                    return f"🚨 [밸런스 보완] 스타포스보다 급한 것은 '{worst_label}'입니다. 밸런스를 먼저 맞춰주세요."
                return "📈 [상위 강화] 22성 도전의 가치가 있는 베이스입니다. 22성 강화를 고려하세요."
        return f"🛠️ [강화 권장] 엘리트 장비입니다. 전체적인 밸런스를 위해 '{worst_label}'을 보완하세요."

    elif total_score >= 250:
        target_star = min(17, max_star_possible)
        if star_val < target_star and 3 in eval_indices:
            if max_star_possible < 17:
                return f"📦 [가성비 강화] 해당 아이템의 한계치인 {max_star_possible}성 달성 후 잠재능력을 손보는 것이 효율적입니다."
            else:
                return "📦 [가성비 강화] 최소 17 ~ 18성 달성 후 잠재능력을 손보는 것이 효율적입니다."
        return f"📈 [효율 투자 / 교체] '{worst_label}'부터 차근차근 올리거나, 상위 아이템으로 교체를 추천합니다."

    elif total_score >= 175:
        is_limited = any(h in item_name for h in
                         ["이터널 플레임 링", "어웨이크 링", "테네브리스 원정대 반지", "글로리온 링 : 슈프림", "카오스 링", "SS급 마스터 쥬얼링", "결속의 반지"])

        target_star = min(17, max_star_possible)
        if not is_limited and star_val < target_star and 3 in eval_indices:
            if max_star_possible < 17:
                return f"📦 [가성비 정체] 베이스는 나쁘지 않으나 스타포스가 낮습니다. {max_star_possible}성(한계치) 강화 시 점수가 상승합니다."
            else:
                return "📦 [가성비 정체] 베이스는 나쁘지 않으나 스타포스가 낮습니다. 17성 강화 시 점수가 대폭 상승합니다."

        if is_limited:
            return "📈 [이벤트 아이템] 이벤트 아이템을 강화하기보다는, 무료 재화를 사용하거나 상위 아이템으로의 교체를 추천합니다."

        if scores[1] < 65:
            if scores[2] < 12.5:
                return "🔮 [잠재 / 에디 부족] 스타포스에 비해 잠재능력이 아쉽습니다. 더 높은 잠재능력을 위해 강화하거나 교체를 추천합니다."
            else:
                return "🔮 [잠재 부족] 스타포스에 비해 잠재능력이 아쉽습니다. 더 높은 잠재능력을 위해 강화하거나 교체를 추천합니다."

        if scores[2] < 12.5:
            return "🔮 [에디 부족] 스타포스에 비해 에디셔널 잠재능력이 아쉽습니다. 더 높은 에디셔널 잠재능력을 위해 강화를 추천합니다."

        if scores[0] < 82.5 and 0 in eval_indices:
            return "🌀 [베이스 부실] 추옵이 낮아 투자가 비효율적입니다. 추가옵션의 강화를 추천합니다."

        if is_limited:
            return "✅ [다음 단계 준비] 전반적인 밸런스가 좋습니다. 다음 단계로 넘어가기 위한 업그레이드나 교체를 준비하세요."
        return f"✅ [다음 단계 준비] 전반적인 밸런스가 좋습니다. 다음 단계로 넘어가기 위한 상위 아이템 교체나 {next_label} 강화를 준비하세요."

    else:
        return "🚨 [교체 시급] 현재 세팅에서 가장 효율이 떨어지는 부위입니다. 상위 아이템으로 교체를 추천합니다."


def evaluate_equipment(items, char_class, char_level, unsupported=None):
    evaluate_list = []
    special_items = SCORING.special_items
    weights = SCORING.weights
    noljang = SCORING.noljang
    special_rings = special_items.excluded_rings

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
        main_stat = get_main_stat(char_class).upper()

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

        if is_special:
            actual_add_급수 = calculate_item_score(item.get("item_add_option", {}), char_class)
            add_raw_score = actual_add_급수
            if "포켓" in part:
                total_item_score = actual_add_급수
                if char_class == "데몬어벤져" and total_item_score > 0:
                    total_item_score = total_item_score / special_items.pocket_demon_avenger_divisor
            else:
                # 이름에 포함된 키워드 순서대로 첫 일치. 표는 scoring.toml 의 special_items.fixed
                total_item_score = next(
                    (score for keyword, score in special_items.fixed if keyword in name),
                    special_items.default,
                )

            special_guide = get_special_part_guide(total_item_score, part, name)
            special_guide_tag, special_guide_message = split_guide(special_guide)
            evaluate_list.append({
                "is_wse": False, "is_special": True, "is_noljang": False, "slot": slot, "part": part, "name": name, "icon": icon, "star": 0, "level": item_req_level,
                "total_score": round(total_item_score, 2),
                "guide": special_guide,
                "guide_tag": special_guide_tag,
                "guide_message": special_guide_message,
                "detail": {"add": round(total_item_score, 1), "star": 0, "pot": 0, "pot_additional": 0},
                "raw_options": raw_options_dict,
                "scoring_basis": {
                    "main_stat": main_stat,
                    "recognized_options": [],
                    "weights": ["특수 장비 고유 기준"],
                    "breakdown": [
                        {"key": "add", "label": "추가옵션", "raw_score": round(add_raw_score, 2), "weighted_score": round(total_item_score, 2)},
                        {"key": "pot", "label": "윗잠재", "raw_score": 0, "weighted_score": 0},
                        {"key": "pot_additional", "label": "에디셔널", "raw_score": 0, "weighted_score": 0},
                        {"key": "star", "label": "스타포스", "raw_score": 0, "weighted_score": 0, "star": 0},
                    ],
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

        if is_weapon:
            if any(k in slot for k in special_items.secondary_slots):
                add_score = weights.fixed_secondary_emblem
                add_raw_score = weights.fixed_secondary_emblem
                adv_star_score = weights.fixed_secondary_emblem
            else:
                add_raw_score = calculate_weapon_add_option_score(item, char_class)
                add_score = add_raw_score * weights.weapon_add
            pot_analysis = analyze_weapon_potential_score(item, "potential", char_class)
            pot_val = pot_analysis["score"]
            pot_score = (pot_val * weights.potential) if pot_val > 0 else 0
            eddy_analysis = analyze_weapon_potential_score(item, "additional_potential", char_class)
            eddy_val = eddy_analysis["score"]
            eddy_score = (eddy_val * weights.additional) if eddy_val > 0 else 0
        else:
            actual_add_급수 = calculate_item_score(item.get("item_add_option", {}), char_class)
            add_raw_score = actual_add_급수
            add_score = get_advanced_add_score(actual_add_급수, item_req_level, part, char_class)
            pot_analysis = analyze_potential_score(item, "potential", char_class, char_level)
            pot_val = pot_analysis["score"]
            pot_score = (pot_val * weights.potential) if pot_val > 0 else 0
            eddy_analysis = analyze_potential_score(item, "additional_potential", char_class, char_level)
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
        )

        total_item_score = add_score + pot_score + eddy_score + adv_star_score

        if pot_val != -1 and not any(k in name for k in special_rings):
            guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part, total_item_score, name, item_req_level, is_noljang)
            guide_tag, guide_message = split_guide(guide_text)
            evaluate_list.append({
                "is_wse": is_weapon, "is_special": False, "is_noljang": is_noljang, "slot": slot, "part": part, "name": name, "icon": icon, "star": star, "level": item_req_level,
                "total_score": round(total_item_score, 2),
                "guide": guide_text,
                "guide_tag": guide_tag,
                "guide_message": guide_message,
                "detail": {"add": round(add_score, 1), "star": round(adv_star_score, 1), "pot": round(pot_score, 1), "pot_additional": round(eddy_score, 1)},
                "raw_options": raw_options_dict,
                "scoring_basis": {
                    "main_stat": main_stat,
                    "recognized_options": recognized_options,
                    "weights": [
                        f"윗잠재 환산값 × {weights.potential:g}",
                        f"에디셔널 환산값 × {weights.additional:g}",
                        f"스타포스 레벨 가중치 × {round(1.0 + (item_req_level - SCORING.starforce.reference_level) / SCORING.starforce.level_weight_divisor, 3) if item_req_level > 0 else 0}",
                    ],
                    "breakdown": [
                        {"key": "add", "label": "추가옵션", "raw_score": round(add_raw_score, 2), "weighted_score": round(add_score, 2)},
                        {"key": "pot", "label": "윗잠재", "raw_score": round(max(pot_val, 0), 2), "weighted_score": round(pot_score, 2)},
                        {"key": "pot_additional", "label": "에디셔널", "raw_score": round(max(eddy_val, 0), 2), "weighted_score": round(eddy_score, 2)},
                        {"key": "star", "label": "스타포스", "raw_score": star, "weighted_score": round(adv_star_score, 2), "star": star},
                    ],
                    "star_score": round(adv_star_score, 2),
                },
            })

    return evaluate_list


def generate_overall_review(evaluate_list):
    total_scores = [item["total_score"] for item in evaluate_list if not item["is_special"]]
    avg_score = sum(total_scores) / len(total_scores) if total_scores else 0
    all_sorted_results = sorted(evaluate_list, key=lambda x: x["total_score"])
    worst_item = next((item for item in all_sorted_results if not item.get("is_special")), None)

    has_destiny_weapon = any("데스티니" in item.get("name", "") for item in evaluate_list)
    has_genesis_weapon = any("제네시스" in item.get("name", "") for item in evaluate_list)

    if avg_score >= 380:
        rank, comment = "ETERNAL", "전 서버 최상위권 장비입니다. 이제 2차 초월의 영역입니다."
    elif avg_score >= 350:
        if has_destiny_weapon: rank, comment = "DESTINY", "이미 데스티니 무기를 쟁취한 훌륭한 스펙입니다!  부족한 부위를 다듬고 더 높은 곳으로의 성장을 준비하세요!"
        else: rank, comment = "DESTINY", "데스티니 초월에 충분히 도전할만한 스펙입니다.  당신의 가능성을 믿고 초월에 도전하세요!"
    elif avg_score >= 330:
        if has_destiny_weapon: rank, comment = "DESTINY", "이미 데스티니 무기를 쟁취한 훌륭한 스펙입니다!  부족한 부위를 다듬고 더 높은 곳으로의 성장을 준비하세요!"
        else: rank, comment = "DESTINY", "데스티니 초월이 가시권에 들어왔습니다.  천천히 부족한 부위를 강화하고 데스티니 초월에 도전하세요!"
    elif avg_score >= 280:
        rank, comment = "ASTRA", "본격적으로 아스트라 해방에 도전하세요.  2차 해방까지는 가성비의 영역으로 들어섰습니다."
    elif avg_score >= 220:
        rank, comment = "ASTRA", "아스트라 보조 해방을 위한 준비를 할 때 입니다.  천천히 아스트라 해방 준비에 도전하세요."
    elif avg_score >= 200:
        if has_genesis_weapon: rank, comment = "GENESIS", "제네시스 해방에 성공하셨군요!  더 높은 곳을 위한 성장의 준비가 필요한 단계입니다."
        else: rank, comment = "GENESIS", "제네시스 해방을 위한 준비를 할 때 입니다.  해방 준비를 해 보세요."
    else:
        rank, comment = "EPIC", "성장 가능성이 큽니다. 낮은 점수 부위부터 교체해보세요."

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


def build_report(character_name, basic_info, item_data, stat_data, unsupported=None):
    """기본정보·장비·스탯 응답으로 진단 결과 전체를 만듭니다.

    /check-items 응답과 골든 테스트(tests/test_golden.py)가 같은 함수를 사용합니다.
    unsupported 에 Counter 를 주면 점수에 반영하지 못한 옵션을 (종류, 옵션)별로 세어 넣습니다.
    """
    char_class = basic_info.get("character_class")
    char_level = int(basic_info.get("character_level") or 0)
    char_image = basic_info.get("character_image", "")

    best_preset_idx = get_best_preset(item_data, char_class, char_level)
    items = select_preset_items(item_data, best_preset_idx)

    evaluate_list = evaluate_equipment(items, char_class, char_level, unsupported=unsupported)
    overall_review, all_sorted_results = generate_overall_review(evaluate_list)

    return {
        "character": character_name,
        "class": char_class,
        "level": char_level,
        "character_image": char_image,
        "combat_power": extract_combat_power(stat_data),
        "best_preset": best_preset_idx,
        "overall": overall_review,
        "results": all_sorted_results,
    }
