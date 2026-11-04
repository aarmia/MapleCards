"""Validated equipment-set projection, applied set by set.

Numeric tables come from the current Nexon set_option_full response; Dawn fallback is the official notice
https://maplestory.nexon.com/News/Update/656 . Unknowns never mean 'no set'. A set whose current state does not
match the API, or whose table is incomplete, is skipped (listed in "skipped") while the other sets still apply,
so a hunting-preset snapshot can be projected onto the boss preset.
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
IMMORTAL = "임모탈 마이스터 히어로 세트"
MEMBERS = {
    PITCHED: {"루즈 컨트롤 머신 마크", "마력이 깃든 안대", "커맨더 포스 이어링", "거대한 공포", "몽환의 벨트",
              "저주받은 황의 마도서", "저주받은 녹의 마도서", "저주받은 적의 마도서", "저주받은 청의 마도서",
              "미트라의 분노 : 전사", "미트라의 분노 : 마법사", "미트라의 분노 : 궁수", "미트라의 분노 : 도적", "미트라의 분노 : 해적",
              "컴플리트 언더컨트롤", "블랙 하트", "창세의 뱃지", "고통의 근원"},
    RADIANT: {"근원의 속삭임", "황홀한 악몽", "오만의 원죄", "굶주리는 핏빛 원혼", "불멸의 유산", "죽음의 맹세"},
    DAWN: {"트와일라이트 마크", "에스텔라 이어링", "여명의 가디언 엔젤 링", "데이브레이크 펜던트"},
    BOSS: {"응축된 힘의 결정석", "아쿠아틱 레터 눈장식", "블랙빈 마크", "파풀라투스 마크", "지옥의 불꽃", "데아 시두스 이어링",
           "실버블라썸 링", "고귀한 이피아의 반지", "가디언 엔젤 링", "혼테일의 목걸이", "카오스 혼테일의 목걸이",
           "매커네이터 펜던트", "도미네이터 펜던트", "골든 클로버 벨트", "분노한 자쿰의 벨트", "로얄 블랙메탈 숄더",
           "핑크빛 성배", "영생의 돌", "크리스탈 웬투스 뱃지"},
    PARTY: {"미카엘라의 새 안경"},   # 수집 데이터 대조: 베어스·정령의 펜던트는 세트 수에 들어가지 않았습니다
    MEISTER: {"마이스터링", "마이스터 이어링", "마이스터 숄더"},
    IMMORTAL: {"버서커의 마이스터 임모탈 링", "가디언의 마이스터 임모탈 링"},
}
ARMOR_SLOTS = {"모자", "상의", "하의", "신발", "장갑", "망토", "어깨장식", "한벌옷"}
# 방어구 세트: "<계열> <직업군 토큰>…" 이름을 "<계열> 세트(직업군)" 으로 셉니다 (에테르넬·아케인셰이드·앱솔랩스)
ARMOR_FAMILIES = ("에테르넬", "아케인셰이드", "앱솔랩스")
ARMOR_GROUPS = (("나이트", "전사"), ("메이지", "마법사"), ("아처", "궁수"), ("시프", "도적"), ("파이렛", "해적"))
# 루타비스(카루타) 세트: 하이네스(모자) · 이글아이(상의) · 트릭스터(하의) + 직업군 토큰, 무기는 파프니르
ROOT_ABYSS_PARTS = ("하이네스", "이글아이", "트릭스터")
ROOT_ABYSS_GROUPS = (("워리어", "전사"), ("던위치", "마법사"), ("레인저", "궁수"), ("어새신", "도적"), ("원더러", "해적"))
# 무기는 이름 앞 단어로 계열을 정하고, API 세트명 중 그 계열 이름이 들어간 세트(캐릭터 직업군의 세트)에 셉니다.
WEAPON_FAMILY = {"파프니르": "루타비스", "앱솔랩스": "앱솔랩스", "아케인셰이드": "아케인셰이드", "데스티니": "에테르넬", "제네시스": "에테르넬"}
# 제네시스·데스티니 무기는 럭키 아이템: 하위 세트를 3개 이상 착용 중이면 그 세트에도 1개로 인정됩니다 (수집 데이터 대조)
LUCKY_WEAPONS = ("제네시스", "데스티니")
LUCKY_TARGETS = ("루타비스", "앱솔랩스", "아케인셰이드")
LUCKY_MIN_PIECES = 3
# 위 표에 없는 아이템은 세트가 없는 것으로 봅니다. 빠진 세트 아이템은 현재 프리셋의 세트 수를 API 와 대조할 때 드러나고,
# 그 세트만 투영에서 건너뜁니다 (analyze_sets 의 skipped).
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
    """방어구·장신구가 속한 세트 이름. 표에 없으면 "" (세트 없음). 무기는 count_sets 가 API 세트명으로 셉니다."""
    name = _name(item.get("item_name") or "")
    slot = item.get("item_equipment_slot")
    for set_name, members in MEMBERS.items():
        if name in members:
            return set_name
    if slot in ARMOR_SLOTS:
        for family in ARMOR_FAMILIES:
            for token, group in ARMOR_GROUPS:
                if name.startswith(f"{family} {token}"):
                    return f"{family} 세트({group})"
        for part in ROOT_ABYSS_PARTS:
            for token, group in ROOT_ABYSS_GROUPS:
                if name.startswith(f"{part} {token}"):
                    return f"루타비스 세트({group})"
    return ""


def _items(items):
    return [i for i in items if i.get("item_equipment_slot") != "예비 특수 반지"]


def count_sets(items, api_names=()):
    """착용 아이템의 세트별 개수. 무기는 계열 이름이 들어간 API 세트명에 세고, 제네시스·데스티니는 럭키 아이템 규칙을 적용합니다."""
    items = _items(items)
    counts = Counter(value for item in items if item.get("item_equipment_slot") != "무기" and (value := membership(item)))

    def family_set(family):
        return next((name for name in api_names if family in name), None)

    for item in items:
        if item.get("item_equipment_slot") != "무기":
            continue
        prefix = _name(item.get("item_name") or "").split(" ")[0]
        own = family_set(WEAPON_FAMILY.get(prefix, ""))
        if own:
            counts[own] += 1
        if prefix in LUCKY_WEAPONS:
            for family in LUCKY_TARGETS:
                target = family_set(family)
                if target and counts[target] >= LUCKY_MIN_PIECES:
                    counts[target] += 1
    return counts


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
    """세트별로 검증해 통과한 세트의 효과 차이만 delta 에 더합니다. 통과하지 못한 세트는 skipped 에 이유와 함께 남깁니다."""
    equipped, selected = _items(equipped), _items(selected)
    api = {_name(row["set_name"]): row for row in (data or {}).get("set_effect", [])}
    before, after = count_sets(equipped, api), count_sets(selected, api)
    checks, changes, reasons, skipped, delta = [], [], [], [], _zero()
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
        if before[name] == after[name]:
            continue
        changes.append({"set": name, "before": before[name], "after": after[name], "source": source})
        problem = None
        thresholds = ({PITCHED: range(2, 11), DAWN: range(2, 5), RADIANT: range(2, 7),
                       BOSS: (3, 5, 7, 9), PARTY: range(2, 8), MEISTER: (2, 3), IMMORTAL: (2,)}.get(name)
                      or (range(2, 9) if name.startswith("에테르넬 세트(") else ())
                      or (range(2, 8) if name.startswith(("아케인셰이드 세트(", "앱솔랩스 세트(")) else ())
                      or (range(2, 5) if name.startswith("루타비스 세트(") else ()))
        if not count_ok or not effects_ok:
            problem = "현재 API 대조 불일치"
        elif table is None:
            problem = "선택 프리셋 단계별 효과 정보 없음"
        elif any(n <= max(before[name], after[name]) and n not in table_by_count for n in thresholds):
            problem = "필요한 세트 단계 누락"
        elif after[name] > max(table_by_count, default=1) and name != MEISTER:
            problem = "알려진 단계 초과"
        if problem:
            skipped.append({"set": name, "reason": problem})
            continue
        set_delta, unparsed_all = _zero(), []
        for n, text in table_by_count.items():
            sign = int(n <= after[name]) - int(n <= before[name])
            if not sign:
                continue
            values, unparsed = _parse(text)
            unparsed_all += [f"{n}세트: {clause}" for clause in unparsed]
            for key in FIELDS:
                set_delta[key] += sign * values[key]
            set_delta["ied_ratio"] *= values["ied_ratio"] ** sign
        if unparsed_all:
            skipped.append({"set": name, "reason": "미지원 효과: " + "; ".join(unparsed_all)})
            continue  # 한 세트를 반만 넣지 않습니다
        for key in FIELDS:
            delta[key] += set_delta[key]
        delta["ied_ratio"] *= set_delta["ied_ratio"]
    applied = [c["set"] for c in changes if c["set"] not in {s["set"] for s in skipped}]
    if reasons:
        delta, applied = _zero(), []  # API 응답 자체가 없으면 아무 세트도 믿지 않습니다
    reasons += [f"{s['set']}: {s['reason']}" for s in skipped]
    status = "applied" if applied else "unresolved" if (changes or reasons) else "verified_unchanged"
    return {"status": status, "validation": checks, "changes": changes, "reasons": reasons, "skipped": skipped,
            "applied_sets": applied, "delta": delta,
            "preserved_sets": [name for name in api if name not in before and name not in after and name not in MEMBERS]}


def apply_sets(context, report):
    sources = {**context.sources, "set_projection": report, "set_projection_status": report["status"]}
    if report["status"] != "applied":
        return replace(context, sources=sources)
    if context.main_stat not in ("str", "dex", "int", "luk") or not context.sub_names:
        blocked = {**report, "status": "unresolved", "reasons": ["특수 직업 세트 환산 미지원"], "delta": _zero()}
        return replace(context, sources={**sources, "set_projection": blocked, "set_projection_status": "unresolved"})
    d = report["delta"]
    main = context.main_stat
    base, mult = context.base_stat + d[main], context.stat_pct + d[main + "_pct"] / 100
    attack_key = "magic" if main == "int" else "attack"
    attack_mult = context.attack_multiplier + d[attack_key + "_pct"] / 100
    attack = (context.attack / context.attack_multiplier + d[attack_key]) * attack_mult
    # 부스탯은 단일 부스탯 직업만 보정합니다. 복수 부스탯(섀도어·듀블·카데나)은 현재 값을 그대로 둡니다.
    modeled_sub = len(context.sub_names) == 1 and "sub_base_estimate" in context.sources
    sub = context.sub_names[0].lower()
    sub_base = context.sources.get("sub_base_estimate", 0) + d[sub]
    sub_mult = context.sources.get("sub_multiplier", 1) + d[sub + "_pct"] / 100
    if min(base, mult, attack_mult, attack, sub_mult if modeled_sub else 1) <= 0:
        blocked = {**report, "status": "unresolved", "reasons": ["세트 보정 후 유효 범위를 벗어난 스탯"], "delta": _zero()}
        return replace(context, sources={**sources, "set_projection": blocked, "set_projection_status": "unresolved"})
    sub_fields = {}
    if modeled_sub:
        sources.update(sub_base_estimate=sub_base, sub_multiplier=sub_mult)
        sub_fields = {"final_sub": sub_base * sub_mult + context.sources.get("sub_exempt", 0),
                      "sub_pct_ratio": sub_base / (context.stat_weight * base), "sub_flat_ratio": sub_mult / (context.stat_weight * mult)}
    return replace(context, base_stat=base, stat_pct=mult, final_main=base * mult + context.exempt_stat, attack=attack,
        attack_multiplier=attack_mult, crit_damage=context.crit_damage + d["crit"],
        crit_rate=context.crit_rate + d["rate"] if context.crit_rate is not None else None, sources=sources, **sub_fields)
