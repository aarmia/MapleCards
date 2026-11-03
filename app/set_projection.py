"""Validated, deliberately limited equipment-set projection.

Membership catalog: three-character regression scope. Numeric tables come from
the current Nexon set_option_full response; Dawn fallback is the official notice
https://maplestory.nexon.com/News/Update/656 . Unknowns never mean 'no set'.
"""
import re
from collections import Counter
from dataclasses import replace

PITCHED = "칠흑의 보스 세트"
DAWN = "여명의 보스 세트"
RADIANT = "광휘의 보스 세트"
BOSS = "보스 장신구 세트"
PARTY = "파티 퀘스트 세트"
MEISTER = "마이스터 세트"
MEMBERS = {
    PITCHED: {"루즈 컨트롤 머신 마크", "마력이 깃든 안대", "커맨더 포스 이어링", "몽환의 벨트",
              "거대한 공포", "고통의 근원", "창세의 뱃지", "컴플리트 언더컨트롤",
              "저주받은 황의 마도서", "저주받은 녹의 마도서", "저주받은 적의 마도서", "저주받은 청의 마도서",
              "미트라의 분노 : 전사", "미트라의 분노 : 마법사", "미트라의 분노 : 궁수", "미트라의 분노 : 도적", "미트라의 분노 : 해적"},
    DAWN: {"트와일라이트 마크", "에스텔라 이어링", "여명의 가디언 엔젤 링", "데이브레이크 펜던트"},
    RADIANT: {"근원의 속삭임", "황홀한 악몽", "죽음의 맹세", "불멸의 유산"},
    BOSS: {"매커네이터 펜던트", "가디언 엔젤 링"},
    PARTY: {"베어스 퍼플 펜던트"},
    MEISTER: {"마이스터링", "마이스터 이어링", "마이스터 숄더"},
}
# Explicitly reviewed non-set items, not name-substring guesses.
NO_SET = {"리스트레인트 링", "컨티뉴어스 링", "레드 시프 마이스터 심볼", "샤이니 레드 시프 마이스터 심볼",
          "미카엘라의 새 안경", "하프 이어링", "테네브리스 원정대 반지", "글로리온 링 : 슈프림",
          "SS급 마스터 쥬얼링", "카오스 링", "THE RED", "아스트라 옵시디언", "아스트라 헥스시커", "아스트라 소울링"}
DAWN_TABLE = [
    {"set_count": 2, "set_option": "올스탯 +10, 최대 HP +250, 공격력 +10, 마력 +10, 보스 몬스터 데미지 +10%"},
    {"set_count": 3, "set_option": "올스탯 +10, 최대 HP +250, 공격력 +10, 마력 +10"},
    {"set_count": 4, "set_option": "올스탯 +10, 최대 HP +250, 공격력 +10, 마력 +10, 몬스터 방어율 무시 +10%, 방어력 +100"},
]
FIELDS = ("str", "dex", "int", "luk", "str_pct", "dex_pct", "int_pct", "luk_pct",
          "attack", "magic", "attack_pct", "magic_pct", "damage", "boss", "crit", "rate", "hp", "hp_pct")


def _name(value):
    return " ".join(value.split())


def membership(item):
    name = _name(item.get("item_name") or "")
    for set_name, members in MEMBERS.items():
        if name in members:
            return set_name
    for token, group in (("시프", "도적"), ("파이렛", "해적")):
        if name.startswith("에테르넬 " + token) and item.get("item_equipment_slot") in {"모자", "상의", "하의", "신발", "장갑", "망토", "어깨장식"}:
            return f"에테르넬 세트({group})"
    if item.get("item_equipment_slot") == "무기":
        if name in {"데스티니 가즈", "데스티니 차크람"}:
            return "에테르넬 세트(도적)"
        if name == "데스티니 소울슈터":
            return "에테르넬 세트(해적)"
    return "" if name in NO_SET else None


def _items(items):
    return [i for i in items if i.get("item_equipment_slot") != "예비 특수 반지"]


def _counts(items):
    return Counter(value for item in _items(items) if (value := membership(item)))


def _zero():
    return {**dict.fromkeys(FIELDS, 0.0), "ied_ratio": 1.0}


def _parse(text):
    result, unknown = _zero(), []
    aliases = {"STR": "str", "DEX": "dex", "INT": "int", "LUK": "luk", "공격력": "attack",
               "마력": "magic", "데미지": "damage", "보스 몬스터 데미지": "boss",
               "보스 몬스터 공격 시 데미지": "boss", "크리티컬 데미지": "crit", "크리티컬 확률": "rate", "최대 HP": "hp"}
    # API set clauses are comma-delimited independent options, not skill descriptions.
    for clause in text.split(","):
        clause = clause.strip()
        match = re.fullmatch(r"(.+?)\s*\+\s*(\d+(?:\.\d+)?)(%?)", clause)
        if not match:
            unknown.append(clause)
            continue
        label, value, pct = match[1].strip(), float(match[2]), bool(match[3])
        if label in ("몬스터 방어율 무시", "방어율 무시") and pct and value < 100:
            result["ied_ratio"] *= 1 - value / 100
        elif label == "올스탯":
            for stat in ("str", "dex", "int", "luk"):
                result[stat + ("_pct" if pct else "")] += value
        elif label in aliases:
            key = aliases[label]
            if key in ("str", "dex", "int", "luk", "attack", "magic", "hp"):
                result[key + ("_pct" if pct else "")] += value
            elif pct:
                result[key] += value
            else:
                unknown.append(clause)
        elif label not in ("최대 MP", "방어력", "이동속도", "점프력"):
            unknown.append(clause)
    return result, unknown


def analyze_sets(equipped, selected, data):
    equipped, selected = _items(equipped), _items(selected)
    before, after = _counts(equipped), _counts(selected)
    api = {_name(row["set_name"]): row for row in (data or {}).get("set_effect", [])}
    checks, changes, reasons, delta = [], [], [], _zero()

    def identity(item):
        return (item.get("item_equipment_slot"), item.get("item_name"))

    changed_ids = {identity(i) for i in equipped} ^ {identity(i) for i in selected}
    unknown = sorted({i.get("item_name", "") for i in equipped + selected
                      if identity(i) in changed_ids and membership(i) is None})
    if unknown:
        reasons.append("미등록 교체 장비: " + ", ".join(unknown))
    if not api:
        reasons.append("현재 세트 API 응답 없음")
    for name in sorted(set(before) | set(after) | (set(api) & set(MEMBERS))):
        row = api.get(name)
        observed = int(row.get("total_set_count", 0)) if row else 0
        count_ok = before[name] == observed
        table = (row or {}).get("set_option_full")
        source = "api"
        if not table and name == DAWN:
            table, source = DAWN_TABLE, "official_notice_656"
        # One Meister piece cannot activate a set; don't invent its unobserved higher tiers.
        if not table and name == MEISTER and max(before[name], after[name]) <= 1:
            table, source = [], "below_activation_threshold"
        table_by_count = {int(x["set_count"]): x["set_option"] for x in table or []}
        expected_active = {n: re.sub(r"\s+", "", text) for n, text in table_by_count.items() if n <= before[name]}
        actual_active = {int(x["set_count"]): re.sub(r"\s+", "", x["set_option"]) for x in (row or {}).get("set_effect_info", [])}
        effects_ok = expected_active == actual_active
        checks.append({"set": name, "calculated_count": before[name], "api_count": observed,
                       "count_matches": count_ok, "active_effects_match": effects_ok})
        if not count_ok or not effects_ok:
            reasons.append(f"{name}: 현재 API 대조 불일치")
        if before[name] == after[name]:
            continue
        changes.append({"set": name, "before": before[name], "after": after[name], "source": source})
        if table is None:
            reasons.append(f"{name}: 선택 프리셋 단계별 효과 정보 없음")
            continue
        thresholds = ({PITCHED: range(2, 11), DAWN: range(2, 5), RADIANT: range(2, 7),
                       BOSS: (3, 5, 7, 9), PARTY: range(2, 8), MEISTER: (2, 3)}.get(name)
                      or (range(2, 9) if name.startswith("에테르넬 세트(") else ()))
        if any(n <= max(before[name], after[name]) and n not in table_by_count for n in thresholds):
            reasons.append(f"{name}: 필요한 세트 단계 누락")
            continue
        if after[name] > max(table_by_count, default=1) and name != MEISTER:
            reasons.append(f"{name}: 알려진 단계 초과")
            continue
        for n, text in table_by_count.items():
            sign = int(n <= after[name]) - int(n <= before[name])
            if not sign:
                continue
            values, unparsed = _parse(text)
            if unparsed:
                reasons.append(f"{name} {n}세트 미지원 효과: " + "; ".join(unparsed))
            for key in FIELDS:
                delta[key] += sign * values[key]
            delta["ied_ratio"] *= values["ied_ratio"] ** sign
    if reasons:
        delta = _zero()  # Fail closed for the whole projection; never silently apply half a set.
    return {"status": "unresolved" if reasons else "applied" if changes else "verified_unchanged",
            "validation": checks, "changes": changes, "reasons": reasons, "delta": delta,
            "preserved_sets": [name for name in api if name not in before and name not in after and name not in MEMBERS]}


def apply_sets(context, report):
    sources = {**context.sources, "set_projection": report, "set_projection_status": report["status"]}
    if report["status"] != "applied":
        return replace(context, sources=sources)
    if context.main_stat not in ("str", "dex", "int", "luk") or len(context.sub_names) != 1:
        blocked = {**report, "status": "unresolved", "reasons": ["특수 직업 또는 복수 부스탯 세트 환산 미지원"], "delta": _zero()}
        return replace(context, sources={**sources, "set_projection": blocked, "set_projection_status": "unresolved"})
    d = report["delta"]
    main, sub = context.main_stat, context.sub_names[0].lower()
    base, mult = context.base_stat + d[main], context.stat_pct + d[main + "_pct"] / 100
    attack_key = "magic" if main == "int" else "attack"
    attack_mult = context.attack_multiplier + d[attack_key + "_pct"] / 100
    attack = (context.attack / context.attack_multiplier + d[attack_key]) * attack_mult
    sub_base = context.sources.get("sub_base_estimate", 0) + d[sub]
    sub_mult = context.sources.get("sub_multiplier", 1) + d[sub + "_pct"] / 100
    if min(base, mult, attack_mult, attack, sub_mult) <= 0:
        blocked = {**report, "status": "unresolved", "reasons": ["세트 보정 후 유효 범위를 벗어난 스탯"], "delta": _zero()}
        return replace(context, sources={**sources, "set_projection": blocked, "set_projection_status": "unresolved"})
    sources.update(sub_base_estimate=sub_base, sub_multiplier=sub_mult)
    return replace(context, base_stat=base, stat_pct=mult, final_main=base * mult + context.exempt_stat,
        final_sub=sub_base * sub_mult + context.sources.get("sub_exempt", 0), attack=attack,
        attack_multiplier=attack_mult, crit_damage=context.crit_damage + d["crit"],
        crit_rate=context.crit_rate + d["rate"] if context.crit_rate is not None else None,
        sub_pct_ratio=sub_base / (context.stat_weight * base), sub_flat_ratio=sub_mult / (context.stat_weight * mult), sources=sources)
