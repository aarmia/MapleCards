import re
import math

def get_main_stat(class_name: str, stat_data: dict = None) -> str:
    """직업명을 받아 주스탯 키워드를 반환합니다."""
    stat_map = {
        "str": ["히어로", "팔라딘", "다크나이트", "소울마스터", "미하일", "데몬슬레이어", "아란", "카이저", "제로", "블래스터", "렌", "아델", "바이퍼",
                "스트라이커", "은월", "캐논마스터", "아크"],
        "dex": ["보우마스터", "신궁", "패스파인더", "윈드브레이커", "와일드헌터", "메르세데스", "카인", "캡틴", "메카닉", "엔젤릭버스터"],
        "int": ["아크메이지(불,독)", "아크메이지(썬,콜)", "비숍", "플레임위자드", "에반", "루미너스", "배틀메이지", "일리움", "라라", "키네시스"],
        "luk": ["나이트로드", "섀도어", "듀얼블레이더", "나이트워커", "팬텀", "카데나", "칼리", "호영"]
    }

    target_class = class_name.replace(" ", "")

    if target_class == "데몬어벤져": return "hp"
    if target_class == "제논": return "all_stat"

    # [추가 기능] 주스탯 전환 가능 직업군 판별 로직
    # 바이퍼, 캐논마스터는 기본 STR이나 DEX가 높으면 DEX로 전환
    # 캡틴은 기본 DEX이나 STR이 높으면 STR로 전환
    if target_class in ["바이퍼", "캐논마스터", "캡틴"] and stat_data and "final_stat" in stat_data:
        final_stats = {s['stat_name']: int(s['stat_value']) for s in stat_data.get('final_stat', [])}
        char_str = final_stats.get('STR', 0)
        char_dex = final_stats.get('DEX', 0)

        if target_class in ["바이퍼", "캐논마스터"]:
            return "dex" if char_dex > char_str else "str"
        if target_class == "캡틴":
            return "str" if char_str > char_dex else "dex"

    for stat, classes in stat_map.items():
        if target_class in classes:
            return stat

    return "str"

def calculate_item_score(add_option: dict, class_name: str) -> int:
    main_stat = get_main_stat(class_name)

    all_stat_pct = int(add_option.get("all_stat", 0))
    attack_pwr = int(add_option.get("attack_power", 0))
    magic_pwr = int(add_option.get("magic_power", 0))

    if main_stat == "hp":
        hp_val = int(add_option.get("max_hp", 0))
        return hp_val + (attack_pwr * 15)

    if main_stat == "all_stat":
        s_val = int(add_option.get("str", 0))
        d_val = int(add_option.get("dex", 0))
        l_val = int(add_option.get("luk", 0))
        return s_val + d_val + l_val + (all_stat_pct * 20) + (attack_pwr * 5)

    if main_stat == "int":
        int_val = int(add_option.get("int", 0))
        return int_val + (all_stat_pct * 10) + (magic_pwr * 3)

    stat_val = int(add_option.get(main_stat, 0))
    return stat_val + (all_stat_pct * 10) + (attack_pwr * 4)

def get_advanced_add_score(actual_급수, level, part_name, char_name_class):
    no_add_slots = ["반지", "어깨장식", "기계 심장", "훈장", "뱃지", "포켓 아이템", "엠블렘", "보조무기", "무기"]
    if any(k in part_name for k in no_add_slots):
        return 100.0

    if char_name_class == "제논":
        target_map = {250: 300, 200: 265, 160: 240, 150: 220}
        target = target_map.get(level)
        if target is None:
            if level >= 100:
                target = (level * 1.2) + 40
            else:
                return 100.0
    elif char_name_class == "데몬어벤져":
        target_map = {250: 4200, 200: 3600, 160: 2880, 150: 2700}
        target = target_map.get(level)
        if target is None:
            if level >= 100:
                target = level * 18
            else:
                return 100.0
    else:
        target_map = {250: 186, 200: 162, 160: 144, 150: 132, 140: 126, 135: 123, 130: 120}
        target = target_map.get(level)
        if target is None:
            if level >= 100:
                target = (level * 0.6) + 42
            else:
                return 100.0

    diff_percent = ((actual_급수 - target) / target) * 100.0
    score = 100.0
    rem = abs(diff_percent)

    if diff_percent > 0:
        mult = 0.5
        decay = 0.9
    else:
        mult = 1.2
        decay = 1.05

    while rem > 0:
        chunk = min(5.0, rem)
        if diff_percent > 0:
            score += (chunk * mult)
            mult *= decay
        else:
            score -= (chunk * mult)
            mult = min(mult * decay, 3.0)
        rem -= chunk

    return round(max(0, score), 2)

def calculate_potential_score(item_data: dict, potential_type: str, class_name: str, char_level: int) -> float:
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")

    exclude_keywords = ["무기", "보조무기", "엠블렘"]
    is_excluded = any(k in slot for k in exclude_keywords) or any(k in part for k in exclude_keywords)

    if is_excluded:
        return -1

    main_stat = get_main_stat(class_name).upper()

    options = [
        item_data.get(f"{potential_type}_option_1"),
        item_data.get(f"{potential_type}_option_2"),
        item_data.get(f"{potential_type}_option_3")
    ]

    total_stat_score = 0.0
    total_special_score = 0.0

    crit_count = 0
    crit_val_sum = 0

    for opt in options:
        if not opt: continue

        if "크리티컬 데미지" in opt:
            val_match = re.search(r'\+(\d+)%', opt)
            if val_match:
                crit_count += 1
                crit_val_sum += int(val_match.group(1))
            continue

        if "스킬 재사용 대기시간" in opt:
            val_match = re.search(r'(\d+)초', opt)
            if val_match:
                total_special_score += int(val_match.group(1)) * 7.25
            continue

        if main_stat == "ALL_STAT":
            if "올스탯" in opt and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    total_stat_score += int(val_match.group(1))
            elif "레벨" in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    val = int(val_match.group(1))
                    if any(stat in opt for stat in ["STR", "DEX", "LUK"]):
                        total_stat_score += val / 3.0
            elif any(stat in opt for stat in ["STR", "DEX", "LUK"]) and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    total_stat_score += int(val_match.group(1)) / 3.0
            elif "공격력" in opt and "%" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    total_stat_score += int(val_match.group(1)) * 0.3
            elif any(stat in opt for stat in ["STR", "DEX", "LUK"]) and "%" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    total_stat_score += (int(val_match.group(1)) / 3.0) * 0.09
        else:
            if "올스탯" in opt and "%" in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    val = int(val_match.group(1))
                    weight = 1.2 if class_name in ["섀도어", "카데나", "듀얼블레이더"] else 1.1
                    total_stat_score += val * weight
            elif "레벨" in opt and main_stat in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    val = int(val_match.group(1))
                    total_stat_score += val * 3.5
            elif main_stat in opt and "%" in opt and "회복" not in opt:
                val_match = re.search(r'\+(\d+)%', opt)
                if val_match:
                    total_stat_score += int(val_match.group(1))
            elif ("공격력" in opt or "마력" in opt) and "%" not in opt:
                atk_key = "마력" if main_stat == "INT" else "공격력"
                if atk_key in opt:
                    val_match = re.search(r'\+(\d+)', opt)
                    if val_match:
                        total_stat_score += int(val_match.group(1)) * 0.3
            elif main_stat in opt and "%" not in opt and "회복" not in opt:
                val_match = re.search(r'\+(\d+)', opt)
                if val_match:
                    total_stat_score += int(val_match.group(1)) * 0.09

    cd_points = 0
    if potential_type == "potential":
        if crit_count == 1:
            cd_points = crit_val_sum * 1.125
        elif crit_count >= 2:
            cd_points = crit_val_sum * 1.875
    elif potential_type == "additional_potential":
        cd_points = crit_val_sum * 4.0

    total_special_score += cd_points

    if main_stat == "ALL_STAT":
        total_stat_score *= 1.35

    if class_name == "데몬어벤져" and potential_type == "additional_potential":
        total_stat_score *= 0.9

    return round(total_stat_score + total_special_score, 2)

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

    atk_score = (add_atk / base_atk) * 100
    boss_dmg_score = int(add_option.get("boss_damage", 0)) * 0.275
    dmg_score = int(add_option.get("damage", 0)) * 0.275
    all_stat_score = int(add_option.get("all_stat", 0)) * 0.2475
    target_stat_score = int(add_option.get(main_stat, 0)) * 0.05

    total_score = atk_score + boss_dmg_score + dmg_score + all_stat_score + target_stat_score
    return round(total_score, 2)

def calculate_weapon_potential_score(item_data: dict, potential_type: str, class_name: str) -> float:
    slot = item_data.get("item_equipment_slot", "")
    part = item_data.get("item_equipment_part", "")
    is_wse = any(k in slot for k in ["무기", "보조무기"]) or "엠블렘" in part
    if not is_wse:
        return 0.0

    main_stat = get_main_stat(class_name)
    target_atk = "마력" if main_stat == "int" else "공격력"
    target_stat = main_stat.upper()

    options = [
        item_data.get(f"{potential_type}_option_1"),
        item_data.get(f"{potential_type}_option_2"),
        item_data.get(f"{potential_type}_option_3")
    ]

    total_stat_score = 0.0
    total_special_score = 0.0

    for opt in options:
        if not opt: continue
        val_match = re.search(r'\+(\d+)%', opt)
        if not val_match: continue
        val = int(val_match.group(1))

        if "데미지" in opt:
            total_special_score += val * 0.275
        elif "방어율 무시" in opt:
            total_special_score += val * 0.1875
        elif target_atk in opt and "%" in opt:
            total_stat_score += val
        elif "올스탯" in opt and "%" in opt:
            total_stat_score += val * 0.2475
        elif target_stat in opt and "%" in opt and "회복" not in opt:
            total_stat_score += val * 0.225

    return round(total_stat_score + total_special_score, 2)

def get_starforce_score(star, level):
    if level <= 0: return 0.0
    weight = 1.0 + (level - 200) / 600.0
    base22 = 100.0 * ((min(star, 22) / 22.0) ** 2) * weight
    return base22 if star <= 22 else base22 + (3.0 * math.log(star - 21))