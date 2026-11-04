"""캐릭터 스탯 응답과 추가 조회(심볼·하이퍼스탯·유니온·헥사 스탯·유니온 아티팩트·어빌리티·링크·세트 효과)로 유효 스탯% 환산의 기준(StatContext)과 자본대 프로필을 만듭니다.

스탯 응답의 최종 주스탯 S 에는 %가 적용되지 않는 스탯 c(심볼·하이퍼스탯·유니온·헥사 스탯)가 섞여 있으므로, 추가 조회에서 c 와
주스탯% 소스(장비 잠재·추옵 올스탯%·아티팩트·어빌리티·링크)를 모아 깡스탯 a = (S − c) ÷ b 로 되돌립니다. 아티팩트·링크의 고정 스탯은
%가 적용되는 쪽, 어빌리티의 고정 스탯은 미적용 쪽으로 봅니다. 고정 공격력에는 장비·소울의 공격력 % 배율을 곱합니다. 심볼 조회가 없으면 레벨로 c 를 추정하고(basis = estimated), 스탯 응답 자체가 없으면
potential.toml 의 기준 캐릭터를 씁니다(basis = reference).
"""

import re

from app.calculator import final_stat_float, final_stat_value, get_potential_options
from app.potential import StatContext, ap_stat, is_archer, main_names, reference_context, secondary_names, stat_weight
from app.scoring_config import POTENTIAL

EXTRA_KEYS = ("symbol", "hyper_stat", "union_raider", "union_artifact", "ability", "link_skill", "hexa_stat")


def _hexa_main_stat(data, main_stat):
    # 활성 코어만 합산합니다. preset_hexa_stat_core*는 저장 슬롯이므로 중복 합산하지 않습니다.
    main_values = (0, 100, 200, 300, 400, 600, 800, 1000, 1300, 1600, 2000)
    total = 0
    for key in ("character_hexa_stat_core", "character_hexa_stat_core_2", "character_hexa_stat_core_3"):
        for core in (data or {}).get(key) or []:
            for name_key, level_key, primary in (
                ("main_stat_name", "main_stat_level", True),
                ("sub_stat_name_1", "sub_stat_level_1", False),
                ("sub_stat_name_2", "sub_stat_level_2", False),
            ):
                if core.get(name_key) != "주력 스탯 증가":
                    continue
                level = max(0, min(10, int(core.get(level_key) or 0)))
                total += main_values[level] if primary else level * 100
    # 특수 직업 환산은 여기서 일반 직업 수치로 추정하지 않습니다.
    return total if main_stat not in ("hp", "all_stat") else 0

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
    # New API separates the saved stat board from the legacy occupied grid.
    board = data.get("union_state_stat") if "union_state_stat" in data else data.get("union_occupied_stat")
    return list(data.get("union_raider_stat") or []) + list(board or [])


def _artifact_texts(data) -> list:
    """적용 중인 아티팩트 효과 합계("올스탯 150 증가", "최대 HP 3750, 최대 MP 3750 증가"). 만료된 크리스탈은 여기에 없습니다."""
    return [entry.get("name") for entry in (data or {}).get("union_artifact_effect") or []]


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
        if main_stat != "hp":
            total += _number((item.get("item_add_option") or {}).get("all_stat"))
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


def _gear_attack_pct_sources(items, main_stat):
    label = "마력" if main_stat == "int" else "공격력"
    pattern = re.compile(rf"^{label} \+(\d+)%$")
    totals = dict.fromkeys(("potential", "additional_potential", "soul_potential", "soul_option"), 0.0)
    for item in items or []:
        for kind in totals:
            lines = [item.get("soul_option") or ""] if kind == "soul_option" else get_potential_options(item, kind)
            for line in lines:
                if match := pattern.fullmatch(line.strip()):
                    totals[kind] += float(match.group(1))
    # soul_pad / soul_mad are flat values, not attack/magic percentages.
    return totals


def _gear_attack_pct(items, main_stat):
    return sum(_gear_attack_pct_sources(items, main_stat).values())


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
    sources["gear_attack_pct_breakdown"] = _gear_attack_pct_sources(equipped_items, main_stat)
    sources["gear_attack_pct"] = sum(sources["gear_attack_pct_breakdown"].values())
    if "symbol" in missing:
        exempt, other_pct = _estimated_exempt(char_level), 0.0
        sources["estimated_exempt"] = exempt
    else:
        symbol = _symbol_stat(extras["symbol"], names)
        hyper_flat, hyper_pct = _hyper_stat(extras.get("hyper_stat"), names)
        union_flat, union_pct = _effect_stats(_union_texts(extras.get("union_raider")), names)
        artifact_flat, artifact_pct = _effect_stats(_artifact_texts(extras.get("union_artifact")), names)
        ability_flat, ability_pct = _effect_stats(_ability_texts(extras.get("ability")), names)
        link_flat, link_pct = _effect_stats(_link_texts(extras.get("link_skill")), names)
        hexa = _hexa_main_stat(extras.get("hexa_stat"), main_stat)
        # 어빌리티의 고정 스탯은 %가 적용되지 않는 쪽, 아티팩트·링크의 고정 스탯은 적용되는 쪽으로 봅니다.
        exempt = symbol + hyper_flat + union_flat + hexa + ability_flat
        other_pct = hyper_pct + union_pct + artifact_pct + ability_pct + link_pct
        sources.update(
            symbol=symbol, hyper=hyper_flat, union=union_flat, hexa=hexa,
            ability=ability_flat, artifact_applied=artifact_flat, link_applied=link_flat, other_pct=other_pct
        )
    if missing:
        sources["missing"] = missing
    sources["attack_pct_basis"] = "equipment_only"
    if main_stat in ("hp", "all_stat"):
        sources["hexa_special_stat_unmodeled"] = True

    stat_pct = 1 + (gear_pct + other_pct) / 100
    base_stat = (final_main - exempt) / stat_pct
    if base_stat <= 0:
        # 추정치가 실제보다 커서 깡스탯이 음수가 되면 %미적용 스탯을 무시합니다.
        exempt, base_stat = 0.0, final_main / stat_pct
        sources["exempt_dropped"] = True
    crit_damage = final_stat_float(stat_data, "크리티컬 데미지")
    sub_pct_ratio = sub_flat_ratio = None
    if len(names) == 1 and len(subs) == 1 and main_stat != "hp":
        sub_hyper, sub_hyper_pct = _hyper_stat(extras.get("hyper_stat"), subs)
        sub_union, sub_union_pct = _effect_stats(_union_texts(extras.get("union_raider")), subs)
        sub_other_pct = sum(_effect_stats(texts, subs)[1] for texts in (
            _artifact_texts(extras.get("union_artifact")), _ability_texts(extras.get("ability")),
            _link_texts(extras.get("link_skill")))) + sub_hyper_pct + sub_union_pct
        sub_multiplier = 1 + (_gear_pct(equipped_items, subs[0].lower(), subs) + sub_other_pct) / 100
        sub_exempt = _symbol_stat(extras.get("symbol"), subs) + sub_hyper + sub_union
        sub_base = max(0, final_stat_value(stat_data, subs[0]) - sub_exempt) / sub_multiplier
        sub_pct_ratio = sub_base / (stat_weight(char_class) * base_stat)
        sub_flat_ratio = sub_multiplier / (stat_weight(char_class) * stat_pct)
        sources["sub_multiplier"] = sub_multiplier
        sources["sub_base_estimate"] = sub_base
        sources["sub_exempt"] = sub_exempt
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
        ap_stat=ap_stat(char_level),
        basis="estimated" if missing or main_stat in ("hp", "all_stat") else "stat",
        sources=sources,
        attack_multiplier=1 + sources["gear_attack_pct"] / 100,
        sub_pct_ratio=sub_pct_ratio,
        sub_flat_ratio=sub_flat_ratio,
    )


def build_character_profile(char_class, main_stat: str, char_level: int, equipped_items, stat_data, extras=None) -> dict:
    """캐릭터별 기준을 한 번에 정합니다.

    - context: 잠재·에디셔널을 유효 스탯% 로 바꾸는 캐릭터 수치 (StatContext)
    """
    return {
        "main_stat": main_stat.upper(),
        "combat_power": final_stat_value(stat_data, "전투력"),
        "context": build_stat_context(char_class, main_stat, char_level, equipped_items, stat_data, extras),
    }
