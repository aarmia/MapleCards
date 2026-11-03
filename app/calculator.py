import math
import re
from dataclasses import dataclass

from app.scoring_config import CLASSES, SCORING


@dataclass(frozen=True)
class Conversion:
    """고정 수치 옵션을 주스탯 % 로 바꾸는 캐릭터별 계수. None 이면 scoring.toml 의 기본값을 씁니다."""

    stat_flat: float  # 고정 주스탯 1 당 %
    attack_flat: float  # 공격력·마력 1 당 %
    crit_damage: float | None = None  # 크리티컬 데미지 1% 당 %. None 이면 고정 계수
    crit_rate_total: float | None = None  # 캐릭터의 현재 크리티컬 확률(%). None 이면 크확 옵션은 미지원으로 남김
    crit_rate_value: float = 0.0  # 100% 까지 채우는 크확 1% 당 %. 캐릭터 크확이 기준 이상이면 0
    crit_excess_value: float = 0.0  # 100% 를 넘는 크확 1% 당 %. 궁수만 0 보다 큼


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


def classify_unrecognized(options: list[str], recognized: list[dict], main_stat: str) -> tuple[list[dict], list[str]]:
    """점수에 반영되지 않은 옵션을 나눕니다.

    - ignored: 점수에 넣지 않기로 한 옵션 (scoring.toml potential.ignored_keywords)
    - other_stat: 다른 직업의 스탯을 올리는 옵션 (relevant_keywords 에 없는 스탯)
    - unsupported: 어디에도 속하지 않는, 엔진이 모르는 옵션. 로그 집계 대상은 이것뿐입니다.
    """
    config = SCORING.potential
    recognized_text = {entry["option"] for entry in recognized}
    relevant = config.relevant_keywords.raw.get(main_stat, [])
    ignored, unsupported = [], []
    for option in options:
        if option in recognized_text:
            continue
        if any(keyword in option for keyword in config.ignored_keywords):
            ignored.append({"option": option, "reason": "ignored"})
        elif any(keyword in option for keyword in config.stat_keywords) and not any(keyword in option for keyword in relevant):
            ignored.append({"option": option, "reason": "other_stat"})
        else:
            unsupported.append(option)
    return ignored, unsupported


def _score_detail(score: float, recognized: list[dict], options: list[str], main_stat: str = "") -> dict:
    ignored, unsupported = classify_unrecognized(options, recognized, main_stat)
    return {"score": round(score, 2), "recognized": recognized, "ignored": ignored, "unsupported": unsupported}

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

def calculate_item_score(add_option: dict, class_name: str, main_stat: str | None = None) -> int:
    main_stat = main_stat or get_main_stat(class_name)
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

def get_advanced_add_score(actual_급수, level, part_name, char_name_class, target_multiplier=1.0):
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

    # 캐릭터 자본대(전투력)에 따른 목표 배율. build_report 가 profile 로 정합니다.
    target = target * target_multiplier

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

def analyze_potential_score(
    item_data: dict,
    potential_type: str,
    class_name: str,
    char_level: int,
    main_stat: str | None = None,
    conversion: Conversion | None = None,
) -> dict:
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")
    config = SCORING.potential
    # 고정 수치 옵션의 환산 계수. 캐릭터 스탯으로 만든 conversion 이 있으면 그것을, 없으면 기본값을 씁니다.
    stat_flat = conversion.stat_flat if conversion else config.stat_flat
    attack_flat = conversion.attack_flat if conversion else config.attack_flat

    is_excluded = any(k in slot for k in config.excluded_slots) or any(k in part for k in config.excluded_slots)
    main_stat = (main_stat or get_main_stat(class_name)).upper()
    options = get_potential_options(item_data, potential_type)

    if is_excluded:
        return _score_detail(-1, [], options, main_stat)

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

        if "크리티컬 확률" in opt:
            val_match = re.search(r'\+(\d+)%', opt)
            # 캐릭터의 현재 크확을 알 때만 평가합니다. 모르면 미지원으로 남겨 로그에 집계됩니다.
            if val_match and conversion is not None and conversion.crit_rate_total is not None:
                val = int(val_match.group(1))
                current = conversion.crit_rate_total
                useful = max(0.0, min(val, 100 - current))  # 100% 까지 채우는 부분
                excess = max(0.0, min(val, current + val - 100))  # 100% 를 넘는 부분. 궁수의 크리티컬 리인포스만 가치가 있음
                points = useful * conversion.crit_rate_value + excess * conversion.crit_excess_value
                parts = []
                if useful:
                    parts.append(f"크확 {useful:g}% × {conversion.crit_rate_value:.3g}")
                if excess:
                    parts.append(f"초과 {excess:g}% × {conversion.crit_excess_value:.3g}")
                rule = " + ".join(parts) if parts else "크확 100% 달성"
                total_special_score += points
                recognized.append({"option": opt, "value": round(points, 2), "rule": rule})
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
                    interval_match = re.search(r'(\d+)레벨', opt)
                    interval = int(interval_match.group(1)) if interval_match else config.level_stat_interval_default
                    if char_level > 0 and interval > 0:
                        # 캐릭터 레벨에 비례합니다. 9레벨당 +1 은 Lv.285 에서 고정 주스탯 +31 과 같습니다.
                        flat_total = val * (char_level // interval)
                        add_option(opt, flat_total * stat_flat, f"{interval}레벨당 {val} → 고정 {flat_total} × {stat_flat:.3g}")
                    else:
                        add_option(opt, val * config.level_stat_fallback, f"레벨당 주스탯 × {config.level_stat_fallback:g}")
            elif main_stat in opt and "%" in opt and "회복" not in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)), "주스탯%")
            elif ("공격력" in opt or "마력" in opt) and "%" not in opt:
                atk_key = "마력" if main_stat == "INT" else "공격력"
                if atk_key in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        add_option(opt, int(val_match.group(1)) * attack_flat, f"{atk_key} × {attack_flat:.3g}")
            elif main_stat in opt and "%" not in opt and "회복" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    add_option(opt, int(val_match.group(1)) * stat_flat, f"고정 주스탯 × {stat_flat:.3g}")

    cd_points = 0
    crit = config.crit_damage
    crit_rule = "크리티컬 데미지 환산"
    if conversion and conversion.crit_damage:
        # 캐릭터의 크뎀 총합에 따른 체감 감소를 반영한 계수. 줄 수나 윗잠·에디 구분 없이 1% 당 같은 가치입니다.
        cd_points = crit_val_sum * conversion.crit_damage
        crit_rule = f"크리티컬 데미지 × {conversion.crit_damage:.3g}"
    elif potential_type == "potential":
        if crit_count == 1:
            cd_points = crit_val_sum * crit.single
        elif crit_count >= 2:
            cd_points = crit_val_sum * crit.multi
    elif potential_type == "additional_potential":
        cd_points = crit_val_sum * crit.additional

    total_special_score += cd_points
    if cd_points:
        for option in crit_options:
            recognized.append({"option": option, "value": round(cd_points / len(crit_options), 2), "rule": crit_rule})

    if main_stat == "ALL_STAT":
        total_stat_score *= config.xenon.multiplier

    if class_name == "데몬어벤져" and potential_type == "additional_potential":
        total_stat_score *= config.demon_avenger_additional

    return _score_detail(total_stat_score + total_special_score, recognized, options, main_stat)


def calculate_potential_score(
    item_data: dict,
    potential_type: str,
    class_name: str,
    char_level: int,
    main_stat: str | None = None,
    conversion: Conversion | None = None,
) -> float:
    return analyze_potential_score(
        item_data, potential_type, class_name, char_level, main_stat=main_stat, conversion=conversion
    )["score"]

def calculate_weapon_add_option_score(item_data: dict, class_name: str, main_stat: str | None = None) -> float:
    add_option = item_data.get("item_add_option", {})
    base_option = item_data.get("item_base_option", {})

    if not add_option or not base_option:
        return 0.0

    main_stat = main_stat or get_main_stat(class_name)
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

def analyze_weapon_potential_score(
    item_data: dict, potential_type: str, class_name: str, main_stat: str | None = None
) -> dict:
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")
    is_wse = any(k in slot for k in ["무기", "보조무기"]) or "엠블렘" in part
    main_stat = main_stat or get_main_stat(class_name)
    target_atk = "마력" if main_stat == "int" else "공격력"
    target_stat = main_stat.upper()
    options = get_potential_options(item_data, potential_type)
    if not is_wse:
        return _score_detail(0, [], options, target_stat)

    weights = SCORING.weapon.potential

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

    return _score_detail(total_stat_score + total_special_score, recognized, options, target_stat)


def calculate_weapon_potential_score(
    item_data: dict, potential_type: str, class_name: str, main_stat: str | None = None
) -> float:
    return analyze_weapon_potential_score(item_data, potential_type, class_name, main_stat=main_stat)["score"]

def starforce_gain(star: int, level: int) -> dict:
    """현재 스타포스까지 누적된 실제 주스탯·공/마 증가량 (일반 방어구·장신구, scoring.toml starforce.gains).

    점수 곡선과는 별개로 상세 모달에 보여주는 참고값입니다. 23성 이후는 공/마 자료가 없어 22성까지만 셉니다.
    """
    gains = SCORING.starforce.gains
    bands = sorted(gains.stat_per_star_16_22.raw)
    band = next((limit for limit in bands if level <= limit), bands[-1])
    counted = max(0, min(int(star), SCORING.starforce.base_star))
    stat = 0
    for current in range(1, counted + 1):
        if current <= 5:
            stat += gains.stat_per_star_1_5
        elif current <= 15:
            stat += gains.stat_per_star_6_15
        else:
            stat += gains.stat_per_star_16_22[band]
    attack = sum(gains.attack_16_22[band][current - 16] for current in range(16, counted + 1))
    return {"stat": stat, "attack": attack, "counted_star": counted}


def get_starforce_score(star, level):
    if level <= 0: return 0.0
    config = SCORING.starforce
    weight = 1.0 + (level - config.reference_level) / config.level_weight_divisor
    base22 = 100.0 * ((min(star, config.base_star) / float(config.base_star)) ** 2) * weight
    if star <= config.base_star:
        return base22
    return base22 + (config.over_star_log_weight * math.log(star - (config.base_star - 1)))
