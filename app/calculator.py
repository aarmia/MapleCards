import math
import re

from app.potential import StatContext, analyze_lines, reference_context, score_detail, secondary_names
from app.scoring_config import CLASSES, SCORING


def get_potential_options(item_data: dict, potential_type: str) -> list[str]:
    """존재하는 잠재능력 줄만 번호 순서대로 반환합니다.

    도전자의 장비처럼 옵션이 2줄만 제공되거나 향후 3줄을 초과하는
    응답이 추가되어도, 비어 있는 줄 때문에 전체 옵션을 잃지 않습니다.
    """
    option_pattern = re.compile(
        rf"^{re.escape(potential_type)}_option_(\d+)$"
    )
    indexed_options = []

    for key, value in item_data.items():
        match = option_pattern.match(key)
        if not match or not isinstance(value, str) or not value.strip():
            continue
        indexed_options.append((int(match.group(1)), value.strip()))

    indexed_options.sort(key=lambda option: option[0])
    return [value for _, value in indexed_options]


def final_stat_float(stat_data: dict, stat_name: str) -> float | None:
    """스탯 응답(final_stat)에서 stat_name 값을 실수로 꺼냅니다. 항목이 없거나 숫자가 아니면 None 입니다."""
    for stat in (stat_data or {}).get("final_stat") or []:
        if stat.get("stat_name") == stat_name:
            try:
                return float(str(stat.get("stat_value", "0")).replace(",", ""))
            except (TypeError, ValueError):
                return None
    return None


def final_stat_value(stat_data: dict, stat_name: str) -> int:
    """스탯 응답(final_stat)에서 stat_name 값을 정수로 꺼냅니다. 없거나 숫자가 아니면 0 입니다."""
    value = final_stat_float(stat_data, stat_name)
    return int(value) if value is not None else 0


def get_main_stat(class_name: str, stat_data: dict = None) -> str:
    """직업명을 받아 주스탯 키워드를 반환합니다. 표는 app/data/classes.toml 에 있습니다.

    stat_data(스탯 응답)를 주면 전환 가능 직업(classes.toml switchable)은 STR/DEX 중 높은 쪽으로 평가합니다.
    build_report 가 한 번 계산해 파이프라인 전체에 넘기므로, 다른 함수에서 직접 부를 때는 직업명 기준입니다.
    """
    target_class = class_name.replace(" ", "")

    special = CLASSES.special.raw
    if target_class in special:
        return special[target_class]

    # [추가 기능] 주스탯 전환 가능 직업군 판별 로직. 스탯 응답이 주어졌을 때만 작동합니다.
    rule = CLASSES.switchable.raw.get(target_class)
    if rule and stat_data:
        base_value = final_stat_value(stat_data, rule["base"].upper())
        alternative_value = final_stat_value(stat_data, rule["alternative"].upper())
        return rule["alternative"] if alternative_value > base_value else rule["base"]

    for stat, classes in CLASSES.main_stat.raw.items():
        if target_class in classes:
            return stat

    return CLASSES.default

def calculate_item_score(add_option: dict, class_name: str, main_stat: str | None = None) -> float:
    main_stat = main_stat or get_main_stat(class_name)
    grade = SCORING.add_option.grade

    all_stat_pct = int(add_option.get("all_stat", 0))
    attack_pwr = int(add_option.get("attack_power", 0))
    magic_pwr = int(add_option.get("magic_power", 0))
    # 부스탯은 ÷ sub_stat_divisor 로 더합니다 (듀블·섀도어·카데나는 STR·DEX 둘 다)
    sub_total = sum(int(add_option.get(name.lower(), 0) or 0) for name in secondary_names(class_name, main_stat)) / grade.sub_stat_divisor

    if main_stat == "hp":
        hp_val = int(add_option.get("max_hp", 0))
        return hp_val + (attack_pwr * grade.attack_hp) + sub_total

    if main_stat == "all_stat":
        s_val = int(add_option.get("str", 0))
        d_val = int(add_option.get("dex", 0))
        l_val = int(add_option.get("luk", 0))
        return s_val + d_val + l_val + (all_stat_pct * grade.all_stat_pct_all_stat) + (attack_pwr * grade.attack_all_stat)

    if main_stat == "int":
        int_val = int(add_option.get("int", 0))
        return int_val + (all_stat_pct * grade.all_stat_pct_default) + (magic_pwr * grade.magic_int) + sub_total

    stat_val = int(add_option.get(main_stat, 0))
    return stat_val + (all_stat_pct * grade.all_stat_pct_default) + (attack_pwr * grade.attack_default) + sub_total

def flame_best_add_option(level: int, class_name: str, main_stat: str | None = None, sub_names=()) -> dict:
    """레벨별 환생의 불꽃 최대 조합을 item_add_option 과 같은 dict 로 만듭니다 (scoring.toml add_option.flame).

    주스탯 단일 + 주스탯 이중 2줄(부스탯 포함) + 4번째 줄은 올스탯%·공/마 중 급수가 큰 쪽입니다.
    데몬어벤져는 최대 HP 단일 + 부스탯 단일·이중, 제논은 이중 3줄(STR·DEX·LUK 각 2줄)입니다.
    """
    main_stat = main_stat or get_main_stat(class_name)
    flame = SCORING.add_option.flame
    band = next(entry for entry in flame.bands if level >= entry[0])
    tier = flame.max_tier
    single, dual, hp, other = band[1] * tier, band[2] * tier, band[3] * tier, flame.other_per_tier * tier
    subs = [name.lower() for name in sub_names][:2]
    if main_stat == "hp":
        base = {"max_hp": hp}
        if subs:
            base[subs[0]] = single + dual
    elif main_stat == "all_stat":
        base = {"str": dual * 2, "dex": dual * 2, "luk": dual * 2}
    else:
        base = {main_stat: single + dual * 2}
        for sub in subs:
            base[sub] = dual
    attack = "magic_power" if main_stat == "int" else "attack_power"
    candidates = [{**base, "all_stat": other}, {**base, attack: other}]
    return max(candidates, key=lambda option: calculate_item_score(option, class_name, main_stat=main_stat))


def max_starforce(level: int, name: str = "", superior: bool = False) -> int:
    """기준 만점 스타포스 (scoring.toml starforce.max_star): 제네시스 22 · 데스티니 25 · 슈페리얼 15 · 그 외 레벨 구간"""
    config = SCORING.starforce.max_star
    for keyword, star in config.names.raw.items():
        if keyword in name:
            return star
    if superior:
        return config.superior
    for level_limit, star in config.bands:
        if level < level_limit:
            return star
    return config.default


def analyze_potential_score(
    item_data: dict,
    potential_type: str,
    class_name: str,
    char_level: int,
    main_stat: str | None = None,
    context: StatContext | None = None,
) -> dict:
    """무기류를 제외한 잠재·에디셔널을 유효 스탯% 로 환산합니다. 규칙은 app/potential.py, 상수는 app/data/potential.toml

    context 는 build_report 가 캐릭터 스탯으로 만든 StatContext 입니다. 없으면 potential.toml 의 기준 캐릭터로 환산합니다.
    """
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")
    excluded = SCORING.potential.excluded_slots
    main_stat = main_stat or get_main_stat(class_name)
    options = get_potential_options(item_data, potential_type)
    if any(k in slot for k in excluded) or any(k in part for k in excluded):
        return score_detail(-1, [], options, main_stat.upper())
    context = context or reference_context(class_name, main_stat)
    return analyze_lines(options, context, char_level)


def calculate_potential_score(
    item_data: dict,
    potential_type: str,
    class_name: str,
    char_level: int,
    main_stat: str | None = None,
    context: StatContext | None = None,
) -> float:
    return analyze_potential_score(item_data, potential_type, class_name, char_level, main_stat=main_stat, context=context)["score"]


def get_starforce_score(star, level):
    if level <= 0: return 0.0
    config = SCORING.starforce
    weight = 1.0 + (level - config.reference_level) / config.level_weight_divisor
    base22 = 100.0 * ((min(star, config.base_star) / float(config.base_star)) ** 2) * weight
    if star <= config.base_star:
        return base22
    return base22 + (config.over_star_log_weight * math.log(star - (config.base_star - 1)))
