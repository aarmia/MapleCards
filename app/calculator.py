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

def add_option_entries(add_option: dict, class_name: str, main_stat: str | None = None) -> list[dict]:
    """calculate_item_score 의 급수를 줄별로 나눈 근거. 값의 합은 calculate_item_score 와 같습니다 (화면 표시용)."""
    main_stat = main_stat or get_main_stat(class_name)
    grade = SCORING.add_option.grade
    labels = {"str": "STR", "dex": "DEX", "int": "INT", "luk": "LUK", "max_hp": "최대 HP"}

    def num(key):
        return int(add_option.get(key, 0) or 0)

    entries = []

    def add(option, value, rule):
        if value:
            entries.append({"option": option, "value": round(value, 2), "rule": rule})

    if main_stat == "all_stat":
        for key in ("str", "dex", "luk"):
            add(f"{labels[key]} +{num(key)}", num(key), "주스탯")
        add(f"올스탯 +{num('all_stat')}%", num("all_stat") * grade.all_stat_pct_all_stat, f"올스탯% × {grade.all_stat_pct_all_stat:g}")
        add(f"공격력 +{num('attack_power')}", num("attack_power") * grade.attack_all_stat, f"공격력 × {grade.attack_all_stat:g}")
        return entries
    if main_stat == "hp":
        add(f"최대 HP +{num('max_hp')}", num("max_hp"), "주스탯")
        add(f"공격력 +{num('attack_power')}", num("attack_power") * grade.attack_hp, f"공격력 × {grade.attack_hp:g}")
    elif main_stat == "int":
        add(f"INT +{num('int')}", num("int"), "주스탯")
        add(f"올스탯 +{num('all_stat')}%", num("all_stat") * grade.all_stat_pct_default, f"올스탯% × {grade.all_stat_pct_default:g}")
        add(f"마력 +{num('magic_power')}", num("magic_power") * grade.magic_int, f"마력 × {grade.magic_int:g}")
    else:
        add(f"{labels[main_stat]} +{num(main_stat)}", num(main_stat), "주스탯")
        add(f"올스탯 +{num('all_stat')}%", num("all_stat") * grade.all_stat_pct_default, f"올스탯% × {grade.all_stat_pct_default:g}")
        add(f"공격력 +{num('attack_power')}", num("attack_power") * grade.attack_default, f"공격력 × {grade.attack_default:g}")
    for name in secondary_names(class_name, main_stat):
        add(f"{name} +{num(name.lower())}", num(name.lower()) / grade.sub_stat_divisor, f"부스탯 ÷ {grade.sub_stat_divisor:g}")
    return entries


_FLAME_STATS = ("str", "dex", "int", "luk")
_FLAME_STAT_OPTIONS = [(s,) for s in _FLAME_STATS] + [
    (a, b) for i, a in enumerate(_FLAME_STATS) for b in _FLAME_STATS[i + 1:]
]
# 스탯이 아닌 불꽃 옵션. 한 줄씩 차지하므로 남은 줄 수 계산에 씁니다.
_FLAME_OTHER_KEYS = ("max_hp", "max_mp", "attack_power", "magic_power", "armor", "speed", "jump",
                     "boss_damage", "damage", "all_stat", "equipment_level_decrease")


def _flame_stat_per_tier(level: int) -> tuple[int, int]:
    """불꽃 단일·이중 스탯의 등급당 수치. 140제 이상은 scoring.toml 표, 그 아래는 레벨 ÷ 20 + 1 · 레벨 ÷ 40 + 1.

    scoring.toml 의 [0, 7, 4] 줄은 점수 기준점(최대치) 용이라 저레벨 실제 수치와 다릅니다. 기준점은 바꾸지 않습니다.
    """
    flame = SCORING.add_option.flame
    if level >= 140:
        band = next(band for band in flame.bands if level >= band[0])
        return int(band[1]), int(band[2])
    return level // 20 + 1, level // 40 + 1


def flame_stat_lines(add_option: dict, level: int) -> list[dict] | None:
    """추가옵션의 STR·DEX·INT·LUK 합계를 불꽃 줄(단일·이중 스탯, 1~7단계)로 되돌립니다.

    한 옵션은 한 번만 붙고, 스탯이 아닌 옵션이 차지한 줄을 빼면 최대 4줄입니다.
    해가 정확히 하나일 때만 [{"stats": {"luk": 84}, "tier": 7}, ...] 를 돌려주고, 없거나 여러 개면 None 입니다.
    """
    def num(key):
        try:
            return int(float(add_option.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    totals = {s: num(s) for s in _FLAME_STATS}
    if not any(totals.values()):
        return []
    if level <= 0:
        return None
    max_lines = 4 - sum(1 for key in _FLAME_OTHER_KEYS if num(key))
    single, double = _flame_stat_per_tier(level)
    max_tier = SCORING.add_option.flame.max_tier
    solutions = []

    def search(index, remaining, chosen):
        if len(solutions) > 1 or len(chosen) > max_lines:
            return
        if not any(remaining.values()):
            solutions.append(list(chosen))
            return
        if index == len(_FLAME_STAT_OPTIONS):
            return
        search(index + 1, remaining, chosen)
        option = _FLAME_STAT_OPTIONS[index]
        per = single if len(option) == 1 else double
        for tier in range(1, max_tier + 1):
            if any(remaining[s] < per * tier for s in option):
                break
            search(index + 1, {**remaining, **{s: remaining[s] - per * tier for s in option}}, chosen + [(option, tier, per)])

    search(0, totals, [])
    if len(solutions) != 1:
        return None
    return [{"stats": {s: per * tier for s in option}, "tier": tier} for option, tier, per in solutions[0]]


def flame_lines(add_option: dict, level: int, class_name: str, main_stat: str | None = None) -> list[dict] | None:
    """방어구·장신구 추가옵션을 불꽃 줄 단위로: [{"option", "tier", "value"(급수)}]. 급수 0 인 줄은 뺍니다.

    스탯 줄을 하나로 되돌릴 수 없으면 None 입니다(화면은 단계 없이 합계 줄로 표시).
    """
    main_stat = main_stat or get_main_stat(class_name)
    stat_lines = flame_stat_lines(add_option, level)
    if stat_lines is None:
        return None
    labels = {"str": "STR", "dex": "DEX", "int": "INT", "luk": "LUK"}
    order = [main_stat, *[name.lower() for name in secondary_names(class_name, main_stat)]]
    per_other = SCORING.add_option.flame.other_per_tier
    lines = []
    for line in stat_lines:
        keys = sorted(line["stats"], key=lambda key: order.index(key) if key in order else len(order))
        text = " · ".join(f"{labels[key]} +{line['stats'][key]}" for key in keys)
        lines.append({"option": text, "tier": line["tier"], "value": calculate_item_score(line["stats"], class_name, main_stat)})
    for key, text in (("all_stat", "올스탯 +{}%"), ("attack_power", "공격력 +{}"), ("magic_power", "마력 +{}")):
        amount = int(float(add_option.get(key) or 0))
        if amount:
            lines.append({"option": text.format(amount), "tier": amount // per_other if amount % per_other == 0 else None,
                          "value": calculate_item_score({key: amount}, class_name, main_stat)})
    hp = int(float(add_option.get("max_hp") or 0))
    if hp and main_stat == "hp":
        per_hp = next(band for band in SCORING.add_option.flame.bands if level >= band[0])[3]
        lines.append({"option": f"최대 HP +{hp}", "tier": hp // per_hp if hp % per_hp == 0 else None,
                      "value": calculate_item_score({"max_hp": hp}, class_name, main_stat)})
    lines = [{**line, "value": round(line["value"], 2)} for line in lines if line["value"]]
    return sorted(lines, key=lambda line: -line["value"])


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
