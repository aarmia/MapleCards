import math
import re

from app.potential import StatContext, analyze_lines, convert_line, reference_context, score_detail, secondary_names
from app.scoring_config import CLASSES, POTENTIAL, SCORING


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

# 추옵 dict 키 → 잠재 줄과 같은 문구 (convert_line 으로 캐릭터 효율 환산)
_ADD_LINE_TEXT = (("str", "STR +{}"), ("dex", "DEX +{}"), ("int", "INT +{}"), ("luk", "LUK +{}"),
                  ("all_stat", "올스탯 +{}%"), ("attack_power", "공격력 +{}"), ("magic_power", "마력 +{}"), ("max_hp", "최대 HP +{}"))


def _context_add_rows(add_option: dict, context: StatContext) -> list[tuple[str, float, str]]:
    """추옵 줄을 잠재와 같은 규칙(convert_line)으로 유효 스탯% 로 바꾼 뒤 고정 주스탯 1 의 효율로 나눠 급수로 돌려줍니다.

    급수 단위는 그대로(고정 주스탯 1 = 1급)이고, 공/마·올스탯%·부스탯의 계수만 캐릭터 효율을 따릅니다.
    """
    per_flat = context.pct_per_flat
    rows = []
    for key, template in _ADD_LINE_TEXT:
        try:
            amount = int(float(add_option.get(key) or 0))
        except (TypeError, ValueError):
            continue
        if not amount:
            continue
        text = template.format(amount)
        converted = convert_line(text, context, 0)
        if converted is None or per_flat <= 0:
            continue
        grade = converted[0] / per_flat
        unit = grade / amount
        rule = "주스탯" if abs(unit - 1) < 1e-9 else f"1당 {unit:.3g}급 (캐릭터 효율)"
        rows.append((text, grade, rule))
    return rows


def add_option_entries(add_option: dict, class_name: str, main_stat: str | None = None, context: StatContext | None = None) -> list[dict]:
    """calculate_item_score 의 급수를 줄별로 나눈 근거. 값의 합은 calculate_item_score 와 같습니다 (화면 표시용)."""
    if context is not None:
        return [{"option": text, "value": round(grade, 2), "rule": rule} for text, grade, rule in _context_add_rows(add_option, context)]
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
# _FLAME_STATS_FROM[i]: i 번째 이후 옵션이 다루는 스탯 (탐색 가지치기용)
_FLAME_STATS_FROM = [set().union(*_FLAME_STAT_OPTIONS[i:]) for i in range(len(_FLAME_STAT_OPTIONS))]
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


def flame_stat_lines(add_option: dict, level: int, allow_ambiguous: bool = False) -> list[dict] | None:
    """추가옵션의 STR·DEX·INT·LUK 합계를 불꽃 줄(단일·이중 스탯, 1~7단계)로 되돌립니다.

    한 옵션은 한 번만 붙고, 스탯이 아닌 옵션이 차지한 줄을 빼면 최대 4줄입니다.
    해가 정확히 하나일 때만 [{"stats": {"luk": 84}, "tier": 7}, ...] 를 돌려주고, 없거나 여러 개면 None 입니다.
    allow_ambiguous 이면 해가 여러 개일 때 찾은 첫 조합을 돌려주고 각 줄에 "guess": True 를 붙입니다 (화면 표시용).
    140~150제는 단일 8 · 이중 4 처럼 정확히 2배라 같은 합계를 만드는 조합이 여럿인 경우가 많습니다.
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
    line_max = max(single, double) * max_tier  # 한 줄이 한 스탯에 더할 수 있는 최대값
    solutions = []

    def search(index, remaining, chosen):
        if len(solutions) > 1:
            return
        if not any(remaining.values()):
            solutions.append(list(chosen))
            return
        lines_left = max_lines - len(chosen)
        if index == len(_FLAME_STAT_OPTIONS) or lines_left <= 0:
            return
        # 가지치기: 남은 스탯을 이후 옵션이 다루지 않거나 남은 줄로 채울 수 없으면 해가 없습니다
        if any(value and (stat not in _FLAME_STATS_FROM[index] or value > lines_left * line_max)
               for stat, value in remaining.items()):
            return
        search(index + 1, remaining, chosen)
        option = _FLAME_STAT_OPTIONS[index]
        per = single if len(option) == 1 else double
        for tier in range(1, max_tier + 1):
            if any(remaining[s] < per * tier for s in option):
                break
            search(index + 1, {**remaining, **{s: remaining[s] - per * tier for s in option}}, chosen + [(option, tier, per)])

    search(0, totals, [])
    if not solutions or (len(solutions) > 1 and not allow_ambiguous):
        return None
    if len(solutions) > 1:
        return [{"stats": {s: per * tier for s in option}, "tier": tier, "guess": True} for option, tier, per in solutions[0]]
    return [{"stats": {s: per * tier for s in option}, "tier": tier} for option, tier, per in solutions[0]]


def _flame_amount(add_option: dict, key: str) -> int:
    try:
        return int(float(add_option.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _flame_hp_per_tier(level: int) -> int:
    """불꽃 최대 HP·MP 의 단계당 수치. 140제 이상은 scoring.toml 표, 그 아래는 레벨 × 3."""
    if level >= 140:
        return int(next(band for band in SCORING.add_option.flame.bands if level >= band[0])[3])
    return level * 3


def flame_lines(add_option: dict, level: int, class_name: str, main_stat: str | None = None,
                context: StatContext | None = None, values: dict | None = None, attack_tier=None) -> list[dict]:
    """추가옵션의 모든 불꽃 줄: [{"option", "tier", "value"}] (화면 표시용).

    점수에 영향이 없는 줄(다른 직업 스탯·HP·MP·방어력·이동속도 등)도 단계와 수치를 함께 보여주고 value 는 0 입니다.
    스탯 합계를 불꽃 줄로 하나로 되돌릴 수 없으면 스탯별 합계 줄을 단계 None 으로 둡니다.
    value 는 기본이 급수(calculate_item_score)이고, 무기는 values(키 → 공% 환산값)와 attack_tier(공/마 단계 판별)를 넘깁니다.
    """
    main_stat = main_stat or get_main_stat(class_name)
    labels = {"str": "STR", "dex": "DEX", "int": "INT", "luk": "LUK"}
    order = [main_stat, *[name.lower() for name in secondary_names(class_name, main_stat)]]
    totals = {stat: _flame_amount(add_option, stat) for stat in _FLAME_STATS}
    max_tier = SCORING.add_option.flame.max_tier

    def value_of(stats: dict) -> float:
        if values is None:
            return calculate_item_score(stats, class_name, main_stat, context)
        # 무기: 스탯 합계의 환산값을 줄의 몫만큼 나눕니다. 스탯 외 키는 그대로
        return sum(values.get(key, 0.0) * (amount / totals[key] if key in totals and totals[key] else 1) for key, amount in stats.items())

    # 조합이 여러 개면 그중 하나를 보여주고(guess), 합계가 불꽃 줄로 안 맞으면 스탯별 합계 줄을 단계 없이 둡니다.
    stat_lines = flame_stat_lines(add_option, level, allow_ambiguous=True)
    if stat_lines is None:
        stat_lines = [{"stats": {stat: amount}, "tier": None} for stat, amount in totals.items() if amount]
    lines = []
    for line in stat_lines:
        keys = sorted(line["stats"], key=lambda key: order.index(key) if key in order else len(order))
        text = " · ".join(f"{labels[key]} +{line['stats'][key]}" for key in keys)
        entry = {"option": text, "tier": line["tier"], "value": value_of(line["stats"])}
        if line.get("guess"):
            entry["guess"] = True
        lines.append(entry)

    per_other = SCORING.add_option.flame.other_per_tier
    hp_per = _flame_hp_per_tier(level)
    armor_per = _flame_stat_per_tier(level)[0] if level > 0 else None  # 방어력은 단일 스탯과 같은 단계당 수치
    weapon_steps = POTENTIAL.weapon.add_best
    attack_key = "magic_power" if main_stat == "int" else "attack_power"
    others = (
        ("all_stat", "올스탯 +{}%", per_other), ("attack_power", "공격력 +{}", 1), ("magic_power", "마력 +{}", 1),
        ("boss_damage", "보스 몬스터 데미지 +{}%", weapon_steps.boss_damage_per_tier), ("damage", "데미지 +{}%", weapon_steps.damage_per_tier),
        ("max_hp", "최대 HP +{}", hp_per), ("max_mp", "최대 MP +{}", hp_per), ("armor", "방어력 +{}", armor_per),
        ("speed", "이동속도 +{}", 1), ("jump", "점프력 +{}", 1), ("equipment_level_decrease", "착용 레벨 감소 -{}", 5),
    )
    for key, template, per_tier in others:
        amount = _flame_amount(add_option, key)
        if not amount:
            continue
        if attack_tier is not None and key == attack_key:
            tier = attack_tier(amount)
        elif attack_tier is not None and key in ("attack_power", "magic_power"):
            tier = None  # 무기의 다른 쪽 공/마는 단계 기준표가 없습니다
        else:
            tier = amount // per_tier if per_tier and amount % per_tier == 0 and 1 <= amount // per_tier <= max_tier else None
        lines.append({"option": template.format(amount), "tier": tier, "value": value_of({key: amount})})
    lines = [{**line, "value": round(line["value"], 2)} for line in lines]
    return sorted(lines, key=lambda line: -line["value"])


def calculate_item_score(add_option: dict, class_name: str, main_stat: str | None = None, context: StatContext | None = None) -> float:
    """추가옵션 급수 (고정 주스탯 1 = 1급).

    context 가 있으면 공/마·올스탯%·부스탯 계수를 그 캐릭터의 잠재 환산 효율로 씁니다(_context_add_rows).
    없으면(단독 호출) scoring.toml add_option.grade 의 고정 계수: 주스탯 + 올스탯% × 10 + 공 × 4 + 부스탯 ÷ 10.
    """
    if context is not None:
        return sum(grade for _, grade, _ in _context_add_rows(add_option, context))
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

def flame_best_add_option(level: int, class_name: str, main_stat: str | None = None, sub_names=(),
                          context: StatContext | None = None) -> dict:
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
    return max(candidates, key=lambda option: calculate_item_score(option, class_name, main_stat=main_stat, context=context))


def _stat_option(stat: float, attack: float, main_stat: str, sub_names) -> dict:
    stats = ("str", "dex", "luk") if main_stat == "all_stat" else (main_stat, *[name.lower() for name in sub_names])
    option = {key: stat for key in stats}
    option["magic_power" if main_stat == "int" else "attack_power"] = attack
    return option


def starforce_benchmark_option(level: int, main_stat: str, sub_names=(), part: str = "") -> dict | None:
    """만점 스타포스(scoring.toml starforce.benchmark, 기본 25성)의 스탯·공/마 합계를 item_starforce_option 모양으로.
    장갑은 공/마 보너스를 더합니다. 표의 최저 레벨 미만이거나 데몬어벤져(HP 기준 없음)이면 None 입니다."""
    if main_stat == "hp":
        return None
    config = SCORING.starforce.benchmark
    band = next((band for band in config.bands if level >= band[0]), None)
    if band is None:
        return None
    _, stat, attack = band
    return _stat_option(stat, attack + (config.glove_attack_bonus if part == "장갑" else 0), main_stat, sub_names)


def starforce_option(level: int, star: int, main_stat: str, sub_names=(), part: str = "") -> dict | None:
    """star 성까지 스타포스로 오른 스탯·공/마 합계 (scoring.toml starforce.gain) 를 item_starforce_option 모양으로.
    표의 최저 레벨 미만, 그 레벨의 최대 성 초과, 데몬어벤져(HP 기준 없음)이면 None 입니다."""
    gain = SCORING.starforce.gain
    index = next((i for i, low in enumerate(gain.levels) if level >= low), None)
    if main_stat == "hp" or index is None:
        return None
    stat_16, attack_16 = gain.stat_16[index], gain.attack_16[index]
    star = max(0, int(star))
    if star > 15 + len(attack_16):
        return None
    stat = sum(gain.stat_1_15[:star]) + sum(stat_16[:max(0, star - 15)])
    attack = sum(attack_16[:max(0, star - 15)])
    if part == "장갑":
        attack += sum(1 for glove_star in gain.glove_attack_stars if star >= glove_star)
    return _stat_option(stat, attack, main_stat, sub_names)


def noljang_option(level: int, star: int, main_stat: str, sub_names=()) -> dict:
    """놀장 star 성의 스탯·공/마 합계 (scoring.toml starforce.noljang). 놀장은 API 스타포스 옵션이 비고 작과 같은 칸에 들어 있어 표로 계산합니다."""
    config = SCORING.starforce.noljang
    levels = config.levels

    def row(table, lv):
        index = max(i for i, low in enumerate(levels) if lv >= low)
        return table[index]

    if level % 10 == 5 and levels[0] < level - 5 and level + 5 <= levels[-1]:
        # 일의 자리 5 레벨: 스탯은 앞뒤 레벨 평균의 정수, 공/마는 낮은 쪽
        stats = [int((a + b) / 2) for a, b in zip(row(config.stat, level - 5), row(config.stat, level + 5))]
    else:
        stats = row(config.stat, level)
    attacks = row(config.attack, level - 5 if level % 10 == 5 else level)
    star = max(0, min(int(star), 15))
    return _stat_option(sum(stats[:min(star, 5)]), sum(attacks[:max(0, star - 5)]), main_stat, sub_names)


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
