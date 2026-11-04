"""훈장·뱃지·포켓 아이템·칭호를 유효 스탯%(주스탯 1% = 1)로 환산합니다.

잠재·스타포스가 없는 부위라 고정 옵션(item_total_option, 칭호는 설명 문구)만 줄로 풀어 잠재 환산기(app/potential.py)와
무기 환산기(app/weapon.py, 보공·방무)로 값을 구하고, 부위별 최상 아이템(potential.toml [special.best])을 그 캐릭터 기준으로
환산한 값을 100 으로 두어 점수를 냅니다.
"""

import re
from datetime import datetime, timedelta, timezone

from app.potential import convert_line, score_detail
from app.scoring_config import POTENTIAL
from app.weapon import WeaponContext, convert_weapon_line

KST = timezone(timedelta(hours=9))
TITLE_PART = "칭호"
_STAT_KEYS = ("str", "dex", "int", "luk")
# 칭호 설명 문구: "올스탯 +20", "공격력/마력+30", "보스 몬스터 데미지+10%", "몬스터 방어율 무시+30%", "최대 HP/최대 MP +1000"
_TITLE_PATTERNS = (
    (re.compile(r"올스탯\s*\+?\s*(\d+)"), "올스탯 +{}"),
    (re.compile(r"공격력/마력\s*\+?\s*(\d+)"), "{attack} +{}"),
    (re.compile(r"보스 몬스터(?: 공격 시)? 데미지\s*\+?\s*(\d+)%"), "보스 몬스터 데미지 +{}%"),
    (re.compile(r"몬스터 방어율 무시\s*\+?\s*(\d+)%"), "몬스터 방어율 무시 +{}%"),
    (re.compile(r"최대 HP/(?:최대 )?MP\s*\+?\s*(\d+)"), "최대 HP +{}"),
)


def _number(value) -> float:
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def option_lines(total_option: dict, attack_label: str) -> list[str]:
    """item_total_option 을 잠재 줄과 같은 문구로 풉니다. 이동속도·점프처럼 점수와 무관한 키는 뺍니다."""
    total = total_option or {}
    ignored = set(POTENTIAL.special.ignored_keys)
    lines = []
    for key in _STAT_KEYS:
        if key not in ignored and _number(total.get(key)) > 0:
            lines.append(f"{key.upper()} +{_number(total.get(key)):g}")
    if _number(total.get("all_stat")) > 0:
        lines.append(f"올스탯 +{_number(total.get('all_stat')):g}")
    attack_key = "magic_power" if attack_label == "마력" else "attack_power"
    if _number(total.get(attack_key)) > 0:
        lines.append(f"{attack_label} +{_number(total.get(attack_key)):g}")
    for key, text in (("boss_damage", "보스 몬스터 데미지 +{:g}%"), ("damage", "데미지 +{:g}%"), ("ignore_monster_armor", "몬스터 방어율 무시 +{:g}%"), ("max_hp", "최대 HP +{:g}")):
        if key not in ignored and _number(total.get(key)) > 0:
            lines.append(text.format(_number(total.get(key))))
    return lines


def title_lines(description: str, attack_label: str) -> list[str]:
    lines = []
    for pattern, template in _TITLE_PATTERNS:
        for match in pattern.finditer(description or ""):
            lines.append(template.format(match.group(1), attack=attack_label))
    return lines


def title_expired(item: dict, now: datetime | None = None) -> bool:
    """칭호 옵션 적용 기간이 지났으면 True. 만료일이 없으면 상시 옵션으로 봅니다."""
    expire = item.get("date_option_expire")
    if not expire:
        return False
    try:
        deadline = datetime.fromisoformat(str(expire))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=KST)
    return (now or datetime.now(KST)) >= deadline


def convert_special_line(option: str, context: WeaponContext, char_level: int) -> tuple[float, str] | None:
    """고정 옵션 한 줄을 유효 스탯% 로. 스탯·공/마·HP 는 잠재 규칙, 보공·방무·데미지는 무기 규칙(공% 단위)을 유효 스탯% 로 되돌립니다."""
    converted = convert_line(option, context.stats, char_level)
    if converted is not None:
        return converted
    converted = convert_weapon_line(option, context, char_level)
    if converted is None:
        return None
    value, rule = converted
    return value / context.stat_pct_unit, f"{rule} → 공 {context.stat_pct_unit:.3g}% = 유효 스탯 1%"


def analyze_special_lines(options: list[str], context: WeaponContext, char_level: int) -> dict:
    recognized, total = [], 0.0
    for option in options:
        converted = convert_special_line(option, context, char_level) if option else None
        if converted is None:
            continue
        value, rule = converted
        total += value
        recognized.append({"option": option, "value": round(value, 2), "rule": rule})
    return score_detail(total, recognized, options, context.stats.main_label)


def best_total(part: str, context: WeaponContext, char_level: int) -> float:
    """부위별 최상 아이템(potential.toml [special.best])을 이 캐릭터의 유효 스탯% 로 환산한 합"""
    table = POTENTIAL.special.best.raw.get(part) or {}
    stats = context.stats
    main = "최대 HP" if stats.main_stat == "hp" else ("STR" if stats.main_stat == "all_stat" else stats.main_label)
    sub = stats.sub_names[0] if stats.sub_names else None
    lines = []
    for key, value in table.items():
        if key == "main_stat_hp":
            continue
        if key == "main_stat":
            if stats.main_stat == "hp" and "main_stat_hp" in table:
                value = table["main_stat_hp"]
            lines.append(f"{main} +{value:g}")
        elif key == "sub_stat" and sub:
            lines.append(f"{sub} +{value:g}")
        elif key == "all_stat":
            lines.append(f"올스탯 +{value:g}")
        elif key == "attack":
            lines.append(f"{context.attack_label} +{value:g}")
        elif key == "boss_damage":
            lines.append(f"보스 몬스터 데미지 +{value:g}%")
        elif key == "ignore_defense":
            lines.append(f"몬스터 방어율 무시 +{value:g}%")
    return round(analyze_special_lines(lines, context, char_level)["score"], 2)


def special_lines(item: dict, context: WeaponContext) -> tuple[list[str], bool]:
    """아이템의 고정 옵션 줄과 만료 여부. 칭호는 설명 문구에서, 훈장·포켓은 추가옵션만, 나머지는 전체 옵션에서 읽습니다."""
    part = item.get("item_equipment_part")
    if part == TITLE_PART:
        expired = title_expired(item)
        return ([] if expired else title_lines(item.get("title_description", ""), context.attack_label)), expired
    source = item.get("item_add_option") if part in POTENTIAL.special.add_option_parts else item.get("item_total_option")
    return option_lines(source, context.attack_label), False


def is_seed_ring(item: dict) -> bool:
    """시드링(특수 반지): special_ring_level 이 있거나 예비 특수 반지 자리에 있는 아이템"""
    return _number(item.get("special_ring_level")) > 0 or item.get("item_equipment_slot") == "예비 특수 반지"


def ring_score(item: dict) -> tuple[float, dict]:
    """시드링 점수(최상 100): 종류 가중치 × 레벨 ÷ max_level. 리스트레인트·컨티뉴어스 만렙이 100"""
    config = POTENTIAL.special.ring
    name = item.get("item_name") or ""
    weight = next((value for keyword, value in config.weights.raw.items() if keyword in name), config.default)
    level = _number(item.get("special_ring_level"))
    score = round(weight * min(level, config.max_level) / config.max_level, 2)
    rule = f"{name} Lv.{level:g} · 종류 {weight:g} × {level:g}/{config.max_level}"
    return score, {"option": f"{name} Lv.{level:g}", "value": score, "rule": rule}


def title_item(title: dict) -> dict | None:
    """장비 응답의 title 필드를 평가 목록에 넣을 아이템 모양으로 바꿉니다."""
    if not title or not title.get("title_name"):
        return None
    return {
        "item_equipment_part": TITLE_PART,
        "item_equipment_slot": TITLE_PART,
        "item_name": title["title_name"],
        "item_icon": title.get("title_icon") or "",
        "title_description": title.get("title_description") or "",
        "date_option_expire": title.get("date_option_expire"),
        "item_base_option": {},
        "item_add_option": {},
        "item_total_option": {},
    }
