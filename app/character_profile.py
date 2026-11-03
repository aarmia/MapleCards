"""캐릭터 스탯 응답과 추가 조회(심볼·하이퍼스탯·유니온·어빌리티·링크)로 유효 스탯% 환산의 기준(StatContext)과 자본대 프로필을 만듭니다.

스탯 응답의 최종 주스탯 S 에는 %가 적용되지 않는 스탯 c 가 섞여 있으므로, 추가 조회에서 c 와 주스탯% 소스를 모아
깡스탯 a = (S − c) ÷ b 로 되돌립니다. 심볼 조회가 없으면 레벨로 c 를 추정하고(basis = estimated), 스탯 응답 자체가 없으면
potential.toml 의 기준 캐릭터를 씁니다(basis = reference).
"""

import re

from app.calculator import final_stat_float, final_stat_value, get_potential_options
from app.potential import StatContext, is_archer, main_names, reference_context, secondary_names, stat_weight
from app.scoring_config import POTENTIAL, SCORING

EXTRA_KEYS = ("symbol", "hyper_stat", "union_raider", "ability", "link_skill")

# 유니온 "STR, DEX, LUK 40 증가" · 하이퍼스탯 "민첩성 90 증가" · 어빌리티 "최대 HP 5% 증가" · 링크 "모든 능력치 10% 증가", "힘 15 민첩 15 ... 증가" 같은 효과 문구.
# 스탯 이름 바로 뒤에 숫자가 와야 하므로 "HP가 30%인 몬스터" 나 "룬의 힘 지속시간" 은 읽지 않습니다.
_ALL_STAT_WORDS = ("올스탯", "모든 스탯", "모든 능력치", "ALLSTAT")
_STAT_ALIASES = {"힘": "STR", "민첩성": "DEX", "민첩": "DEX", "지력": "INT", "운": "LUK", "최대 HP": "HP"}
_STAT_WORD = r"최대 HP|HP|STR|DEX|INT|LUK|힘|민첩성|민첩|지력|운|올스탯|모든 스탯|모든 능력치|ALLSTAT"
_STAT_WORDS = re.compile(_STAT_WORD)
_EFFECT = re.compile(rf"(?P<stats>(?:(?:{_STAT_WORD})[,\s]*)+)\+?\s*(?P<value>\d[\d,]*)\s*(?P<pct>%?)")
_PCT_LINE = re.compile(r"^(STR|DEX|INT|LUK|올스탯|최대 HP) \+(\d+)%$")


def _number(value) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _effect_stats(texts, names: tuple[str, ...]) -> tuple[float, float]:
    """효과 문구 목록에서 주스탯에 해당하는 (고정 증가 합, % 증가 합)을 모읍니다. 제논처럼 주스탯이 여럿이면 % 는 평균입니다."""
    flat = pct = 0.0
    for text in texts:
        for match in _EFFECT.finditer(text or ""):
            value, hits = _number(match.group("value")), 0
            for word in _STAT_WORDS.findall(match.group("stats")):
                if word in _ALL_STAT_WORDS:
                    hits += sum(1 for name in names if name != "HP")
                elif _STAT_ALIASES.get(word, word) in names:
                    hits += 1
            if match.group("pct"):
                pct += value * hits / len(names)
            else:
                flat += value * hits
    return flat, pct


def _symbol_stat(data, names) -> float:
    return sum(_number(symbol.get(f"symbol_{name.lower()}")) for symbol in (data or {}).get("symbol") or [] for name in names)


def _hyper_stat(data, names) -> tuple[float, float]:
    data = data or {}
    preset = data.get(f"hyper_stat_preset_{data.get('use_preset_no') or 1}") or []
    flat = pct = 0.0
    for entry in preset:
        level = _number(entry.get("stat_level"))
        if level <= 0:
            continue
        parsed = _effect_stats([entry.get("stat_increase")], names)
        if parsed != (0.0, 0.0):
            flat, pct = flat + parsed[0], pct + parsed[1]
        elif entry.get("stat_type") in names and entry.get("stat_type") != "HP":
            flat += level * POTENTIAL.hyper_stat.main_stat_per_level
    return flat, pct


def _union_texts(data) -> list:
    data = data or {}
    return list(data.get("union_raider_stat") or []) + list(data.get("union_occupied_stat") or [])


def _ability_texts(data) -> list:
    return [entry.get("ability_value") for entry in (data or {}).get("ability_info") or []]


def _link_texts(data) -> list:
    data = data or {}
    owned = data.get("character_owned_link_skill")
    entries = list(data.get("character_link_skill") or []) + (owned if isinstance(owned, list) else [owned] if owned else [])
    return [entry.get("skill_effect") for entry in entries if isinstance(entry, dict)]


def _gear_pct(equipped_items, main_stat: str, names) -> float:
    """착용 장비 잠재·에디의 주스탯% 합. 제논은 개별 스탯% 를 1/3 로 넣고, 데몬어벤져는 최대 HP% 만 셉니다."""
    total = 0.0
    for item in equipped_items or []:
        for potential_type in ("potential", "additional_potential"):
            for line in get_potential_options(item, potential_type):
                match = _PCT_LINE.match(line)
                if not match:
                    continue
                stat, value = match.group(1), float(match.group(2))
                if stat == "올스탯":
                    total += value if main_stat != "hp" else 0.0
                elif stat == "최대 HP":
                    total += value if main_stat == "hp" else 0.0
                elif stat in names:
                    total += value / 3 if main_stat == "all_stat" else value
    return total


def _estimated_exempt(char_level: int) -> float:
    bands = POTENTIAL.exempt_estimate.bands
    return float(next((value for limit, value in bands if char_level <= limit), bands[-1][1]))


def build_stat_context(char_class, main_stat: str, char_level: int, equipped_items, stat_data, extras=None) -> StatContext:
    names, subs = main_names(main_stat), secondary_names(char_class, main_stat)
    final_main = sum(final_stat_value(stat_data, name) for name in names)
    attack = final_stat_value(stat_data, "마력" if main_stat == "int" else "공격력")
    if final_main <= 0 or attack <= 0:
        return reference_context(char_class, main_stat)

    extras = extras or {}
    missing = [key for key in EXTRA_KEYS if not extras.get(key)]
    gear_pct = _gear_pct(equipped_items, main_stat, names)
    sources: dict = {"gear_pct": gear_pct}
    if "symbol" in missing:
        exempt, other_pct = _estimated_exempt(char_level), 0.0
        sources["estimated_exempt"] = exempt
    else:
        symbol = _symbol_stat(extras["symbol"], names)
        hyper_flat, hyper_pct = _hyper_stat(extras.get("hyper_stat"), names)
        union_flat, union_pct = _effect_stats(_union_texts(extras.get("union_raider")), names)
        ability_flat, ability_pct = _effect_stats(_ability_texts(extras.get("ability")), names)
        link_flat, link_pct = _effect_stats(_link_texts(extras.get("link_skill")), names)
        exempt = symbol + hyper_flat + union_flat + ability_flat + link_flat
        other_pct = hyper_pct + union_pct + ability_pct + link_pct
        sources.update(symbol=symbol, hyper=hyper_flat, union=union_flat, ability=ability_flat, link=link_flat, other_pct=other_pct)
    if missing:
        sources["missing"] = missing

    stat_pct = 1 + (gear_pct + other_pct) / 100
    base_stat = (final_main - exempt) / stat_pct
    if base_stat <= 0:
        # 추정치가 실제보다 커서 깡스탯이 음수가 되면 %미적용 스탯을 무시합니다.
        exempt, base_stat = 0.0, final_main / stat_pct
        sources["exempt_dropped"] = True
    crit_damage = final_stat_float(stat_data, "크리티컬 데미지")
    return StatContext(
        main_stat=main_stat,
        main_names=names,
        sub_names=subs,
        stat_weight=stat_weight(char_class),
        base_stat=base_stat,
        stat_pct=stat_pct,
        exempt_stat=exempt,
        final_main=float(final_main),
        final_sub=float(sum(final_stat_value(stat_data, name) for name in subs)),
        attack=float(attack),
        crit_damage=crit_damage if crit_damage is not None else float(POTENTIAL.reference.crit_damage),
        crit_rate=final_stat_float(stat_data, "크리티컬 확률"),
        is_archer=is_archer(char_class),
        basis="estimated" if missing else "stat",
        sources=sources,
    )


def build_character_profile(char_class, main_stat: str, char_level: int, equipped_items, stat_data, extras=None) -> dict:
    """캐릭터별 기준을 한 번에 정합니다.

    - tier / add_target_multiplier: 전투력으로 자본대를 나눠 추옵 목표 급수에 곱할 배율 (scoring.toml add_option.tiers)
    - context: 잠재·에디셔널을 유효 스탯% 로 바꾸는 캐릭터 수치 (StatContext)
    """
    tiers = SCORING.add_option.tiers.bands
    combat_power = final_stat_value(stat_data, "전투력")
    tier_name, multiplier = tiers[-1][1], tiers[-1][2]
    for min_power, name, factor in tiers:
        if combat_power >= min_power:
            tier_name, multiplier = name, factor
            break
    return {
        "main_stat": main_stat.upper(),
        "combat_power": combat_power,
        "tier": tier_name,
        "add_target_multiplier": multiplier,
        "context": build_stat_context(char_class, main_stat, char_level, equipped_items, stat_data, extras),
    }
