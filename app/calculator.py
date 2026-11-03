import math
import re

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


def _score_detail(score: float, recognized: list[dict], options: list[str]) -> dict:
    recognized_text = {entry["option"] for entry in recognized}
    return {
        "score": round(score, 2),
        "recognized": recognized,
        "unsupported": [option for option in options if option not in recognized_text],
    }

def get_main_stat(class_name: str, stat_data: dict = None) -> str:
    """직업명을 받아 주스탯 키워드를 반환합니다. 표는 app/data/classes.toml 에 있습니다."""
    target_class = class_name.replace(" ", "")

    special = CLASSES.special.raw
    if target_class in special:
        return special[target_class]

    # [추가 기능] 주스탯 전환 가능 직업군 판별 로직. 스탯 응답이 주어졌을 때만 작동합니다.
    rule = CLASSES.switchable.raw.get(target_class)
    if rule and stat_data and "final_stat" in stat_data:
        final_stats = {s['stat_name']: int(s['stat_value']) for s in stat_data.get('final_stat', [])}
        base_value = final_stats.get(rule["base"].upper(), 0)
        alternative_value = final_stats.get(rule["alternative"].upper(), 0)
        return rule["alternative"] if alternative_value > base_value else rule["base"]

    for stat, classes in CLASSES.main_stat.raw.items():
        if target_class in classes:
            return stat

    return CLASSES.default

def calculate_item_score(add_option: dict, class_name: str) -> int:
    main_stat = get_main_stat(class_name)
    grade = SCORING.add_option.grade

    all_stat_pct = int(add_option.get("all_stat", 0))
    attack_pwr = int(add_option.get("attack_power", 0))
    magic_pwr = int(add_option.get("magic_power", 0))

    if main_stat == "hp":
        hp_val = int(add_option.get("max_hp", 0))
        return hp_val + (attack_pwr * grade.attack_hp)

    if main_stat == "all_stat":
        s_val = int(add_option.get("str", 0))
        d_val = int(add_option.get("dex", 0))
        l_val = int(add_option.get("luk", 0))
        return s_val + d_val + l_val + (all_stat_pct * grade.all_stat_pct_all_stat) + (attack_pwr * grade.attack_all_stat)

    if main_stat == "int":
        int_val = int(add_option.get("int", 0))
        return int_val + (all_stat_pct * grade.all_stat_pct_default) + (magic_pwr * grade.magic_int)

    stat_val = int(add_option.get(main_stat, 0))
    return stat_val + (all_stat_pct * grade.all_stat_pct_default) + (attack_pwr * grade.attack_default)

def get_advanced_add_score(actual_급수, level, part_name, char_name_class):
    add_config = SCORING.add_option
    if any(k in part_name for k in add_config.no_add_parts):
        return 100.0

    targets = add_config.targets
    if char_name_class == "제논":
        target_map, fallback = targets.xenon, targets.xenon_fallback
    elif char_name_class == "데몬어벤져":
        target_map, fallback = targets.demon_avenger, targets.demon_avenger_fallback
    else:
        target_map, fallback = targets.default, targets.default_fallback

    target = target_map.get(level)
    if target is None:
        if level >= fallback.min_level:
            target = (level * fallback.slope) + fallback.offset
        else:
            return 100.0

    diff_percent = ((actual_급수 - target) / target) * 100.0
    score = 100.0
    rem = abs(diff_percent)

    curve = add_config.curve
    if diff_percent > 0:
        mult = curve.above.mult
        decay = curve.above.decay
    else:
        mult = curve.below.mult
        decay = curve.below.decay

    while rem > 0:
        chunk = min(curve.chunk, rem)
        if diff_percent > 0:
            score += (chunk * mult)
            mult *= decay
        else:
            score -= (chunk * mult)
            mult = min(mult * decay, curve.below.cap)
        rem -= chunk

    return round(max(0, score), 2)

def analyze_potential_score(item_data: dict, potential_type: str, class_name: str, char_level: int) -> dict:
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")
    config = SCORING.potential

    is_excluded = any(k in slot for k in config.excluded_slots) or any(k in part for k in config.excluded_slots)

    if is_excluded:
        return _score_detail(-1, [], get_potential_options(item_data, potential_type))

    main_stat = get_main_stat(class_name).upper()

    options = get_potential_options(item_data, potential_type)

    total_stat_score = 0.0
    total_special_score = 0.0

    crit_count = 0
    crit_val_sum = 0
    crit_options = []
    recognized = []

    def add_option(option, points, rule):
        nonlocal total_stat_score
        total_stat_score += points
        recognized.append({"option": option, "value": round(points, 2), "rule": rule})

    for opt in options:
        if not opt: continue

        if "크리티컬 데미지" in opt:
            val_match = re.search(r'\+(\d+)%', opt)
            if val_match:
                crit_count += 1
                crit_val_sum += int(val_match.group(1))
                crit_options.append(opt)
            continue

        if "스킬 재사용 대기시간" in opt:
            val_match = re.search(r'(\d+)초', opt)
            if val_match:
                points = int(val_match.group(1)) * config.cooldown_per_second
                total_special_score += points
                recognized.append({"option": opt, "value": round(points, 2), "rule": f"초당 {config.cooldown_per_second:g}"})
            continue

        if main_stat == "ALL_STAT":
            xenon = config.xenon
            if "올스탯" in opt and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)), "올스탯%")
            elif "레벨" in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    val = int(val_match.group(1))
                    if any(stat in opt for stat in ["STR", "DEX", "LUK"]):
                        add_option(opt, val / xenon.level_stat_divisor, f"제논 레벨당 스탯 ÷ {xenon.level_stat_divisor:g}")
            elif any(stat in opt for stat in ["STR", "DEX", "LUK"]) and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)) / xenon.stat_pct_divisor, f"제논 개별 스탯% ÷ {xenon.stat_pct_divisor:g}")
            elif "공격력" in opt and "%" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)) * xenon.attack_flat, f"공격력 × {xenon.attack_flat:g}")
            elif any(stat in opt for stat in ["STR", "DEX", "LUK"]) and "%" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    add_option(
                        opt,
                        (int(val_match.group(1)) / xenon.stat_pct_divisor) * xenon.stat_flat,
                        f"제논 고정 스탯 ÷ {xenon.stat_pct_divisor:g} × {xenon.stat_flat:g}",
                    )
        else:
            if "올스탯" in opt and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    val = int(val_match.group(1))
                    weight = config.all_stat_pct_boost if class_name in config.all_stat_pct_boost_classes else config.all_stat_pct
                    add_option(opt, val * weight, f"올스탯% × {weight:g}")
            elif "레벨" in opt and main_stat in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    val = int(val_match.group(1))
                    add_option(opt, val * config.level_stat, f"레벨당 주스탯 × {config.level_stat:g}")
            elif main_stat in opt and "%" in opt and "회복" not in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)), "주스탯%")
            elif ("공격력" in opt or "마력" in opt) and "%" not in opt:
                atk_key = "마력" if main_stat == "INT" else "공격력"
                if atk_key in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        add_option(opt, int(val_match.group(1)) * config.attack_flat, f"{atk_key} × {config.attack_flat:g}")
            elif main_stat in opt and "%" not in opt and "회복" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)) * config.stat_flat, f"고정 주스탯 × {config.stat_flat:g}")

    cd_points = 0
    crit = config.crit_damage
    if potential_type == "potential":
        if crit_count == 1:
            cd_points = crit_val_sum * crit.single
        elif crit_count >= 2:
            cd_points = crit_val_sum * crit.multi
    elif potential_type == "additional_potential":
        cd_points = crit_val_sum * crit.additional

    total_special_score += cd_points
    if cd_points:
        for option in crit_options:
            recognized.append({"option": option, "value": round(cd_points / len(crit_options), 2), "rule": "크리티컬 데미지 환산"})

    if main_stat == "ALL_STAT":
        total_stat_score *= config.xenon.multiplier

    if class_name == "데몬어벤져" and potential_type == "additional_potential":
        total_stat_score *= config.demon_avenger_additional

    return _score_detail(total_stat_score + total_special_score, recognized, options)


def calculate_potential_score(item_data: dict, potential_type: str, class_name: str, char_level: int) -> float:
    return analyze_potential_score(item_data, potential_type, class_name, char_level)["score"]

def calculate_weapon_add_option_score(item_data: dict, class_name: str) -> float:
    add_option = item_data.get("item_add_option", {})
    base_option = item_data.get("item_base_option", {})

    if not add_option or not base_option:
        return 0.0

    main_stat = get_main_stat(class_name)
    target_atk_key = "magic_power" if main_stat == "int" else "attack_power"

    base_atk = int(base_option.get(target_atk_key, 0))
    add_atk = int(add_option.get(target_atk_key, 0))

    if base_atk == 0:
        return 0.0

    weights = SCORING.weapon.add
    atk_score = (add_atk / base_atk) * 100
    boss_dmg_score = int(add_option.get("boss_damage", 0)) * weights.boss_damage
    dmg_score = int(add_option.get("damage", 0)) * weights.damage
    all_stat_score = int(add_option.get("all_stat", 0)) * weights.all_stat
    target_stat_score = int(add_option.get(main_stat, 0)) * weights.main_stat

    total_score = atk_score + boss_dmg_score + dmg_score + all_stat_score + target_stat_score
    return round(total_score, 2)

def analyze_weapon_potential_score(item_data: dict, potential_type: str, class_name: str) -> dict:
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")
    is_wse = any(k in slot for k in ["무기", "보조무기"]) or "엠블렘" in part
    if not is_wse:
        return _score_detail(0, [], get_potential_options(item_data, potential_type))

    main_stat = get_main_stat(class_name)
    target_atk = "마력" if main_stat == "int" else "공격력"
    target_stat = main_stat.upper()
    weights = SCORING.weapon.potential

    options = get_potential_options(item_data, potential_type)

    total_stat_score = 0.0
    total_special_score = 0.0
    recognized = []

    for opt in options:
        if not opt: continue
        val_match = re.search(r'\+(\d+)%', opt)
        if not val_match: continue
        val = int(val_match.group(1))

        if "데미지" in opt:
            points, rule = val * weights.damage, f"데미지% × {weights.damage:g}"
            total_special_score += points
        elif "방어율 무시" in opt:
            points, rule = val * weights.ignore_defense, f"방어율 무시% × {weights.ignore_defense:g}"
            total_special_score += points
        elif target_atk in opt and "%" in opt:
            points, rule = val, f"{target_atk}%"
            total_stat_score += points
        elif "올스탯" in opt and "%" in opt:
            points, rule = val * weights.all_stat_pct, f"올스탯% × {weights.all_stat_pct:g}"
            total_stat_score += points
        elif target_stat in opt and "%" in opt and "회복" not in opt:
            points, rule = val * weights.main_stat_pct, f"주스탯% × {weights.main_stat_pct:g}"
            total_stat_score += points
        else:
            continue
        recognized.append({"option": opt, "value": round(points, 2), "rule": rule})

    return _score_detail(total_stat_score + total_special_score, recognized, options)


def calculate_weapon_potential_score(item_data: dict, potential_type: str, class_name: str) -> float:
    return analyze_weapon_potential_score(item_data, potential_type, class_name)["score"]

def get_starforce_score(star, level):
    if level <= 0: return 0.0
    config = SCORING.starforce
    weight = 1.0 + (level - config.reference_level) / config.level_weight_divisor
    base22 = 100.0 * ((min(star, config.base_star) / float(config.base_star)) ** 2) * weight
    if star <= config.base_star:
        return base22
    return base22 + (config.over_star_log_weight * math.log(star - (config.base_star - 1)))
