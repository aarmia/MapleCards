"""무기·보조무기·엠블렘의 잠재·에디셔널·소울·추가옵션을 공격력(마력) 1% 단위로 환산합니다.

단위 1 = 공격력 1% 가 주는 상대 데미지 증가(0.01 ÷ 전투 중 공격력 배율). 보공·데미지·방무는 각자의 배율식에서,
주스탯·올스탯·크뎀 같은 스탯 계열은 app/potential.py 의 유효 스탯% 환산값에 "유효 스탯 1% 의 공% 가치"를 곱해 얻습니다.
상수와 전투 중 가정은 app/data/potential.toml 의 [weapon] 에 있습니다.
"""

import re
from dataclasses import dataclass

from app.calculator import get_potential_options
from app.potential import StatContext, convert_line, score_detail
from app.scoring_config import POTENTIAL, SCORING

_ATTACK_PCT = re.compile(r"^(공격력|마력) \+(\d+(?:\.\d+)?)%$")
_ATTACK_FLAT = re.compile(r"^(공격력|마력) \+(\d+)$")
_BOSS = re.compile(r"^보스 몬스터 (?:공격 시 )?데미지 \+(\d+(?:\.\d+)?)%$")
_DAMAGE = re.compile(r"^데미지 \+(\d+(?:\.\d+)?)%$")
_IED = re.compile(r"^몬스터 방어율 무시 \+(\d+(?:\.\d+)?)%$")
# 소울 옵션(soul_option, 예: 공격력 +3%)은 점수와 화면 모두에서 제외하고 소울 잠재 3줄만 봅니다.
SOUL_KEYS = ("soul_potential_option_1", "soul_potential_option_2", "soul_potential_option_3")


@dataclass(frozen=True)
class WeaponContext:
    """무기류 환산에 필요한 캐릭터 수치. 스탯 계열은 stats(StatContext)에서, 보공·데미지·방무는 스탯 응답에서 옵니다."""

    stats: StatContext
    damage: float = 0.0  # 스탯창 데미지 %
    boss_damage: float = 0.0  # 스탯창 보스 몬스터 데미지 %
    ignore_defense: float = 0.0  # 스탯창 방어율 무시 %

    @property
    def attack_label(self) -> str:
        return "마력" if self.stats.main_stat == "int" else "공격력"

    @property
    def attack_multiplier(self) -> float:
        """전투 중 공격력 % 배율: 장비·소울 % + 버프 % + 스킬 패시브 등 추정분"""
        return self.stats.attack_multiplier_effective + POTENTIAL.weapon.buff.attack_pct_extra / 100

    @property
    def unit(self) -> float:
        """공격력 1% 의 상대 데미지 증가"""
        return 0.01 / self.attack_multiplier

    @property
    def stat_pct_unit(self) -> float:
        """유효 스탯 1%(주스탯 1%) 의 공% 가치"""
        stats = self.stats
        return (stats.stat_weight * stats.base_stat_effective / 100 / stats.stat_attack) / self.unit

    @property
    def boss_per_pct(self) -> float:
        """보공·데미지 1% 의 공% 가치 (보스 기준)"""
        total = self.damage + self.boss_damage + POTENTIAL.weapon.buff.boss_damage_extra
        return (0.01 / (1 + total / 100)) / self.unit

    @property
    def remaining_defense(self) -> float:
        """전투 중 남은 방어율 비율: (1 − 방무) × (1 − 추가 방무 가정)"""
        return (1 - self.ignore_defense / 100) * (1 - POTENTIAL.weapon.buff.ied_extra_pct / 100)

    def ied_value(self, pct: float) -> float:
        """방무 +pct% 줄의 공% 가치. 보스 방어율 boss_defense 에서 남은 방어율이 (1 − pct) 배로 줄어드는 효과"""
        defense = POTENTIAL.weapon.boss_defense / 100
        before = max(0.0, 1 - defense * self.remaining_defense)
        after = max(0.0, 1 - defense * self.remaining_defense * (1 - pct / 100))
        return ((after / before) - 1) / self.unit if before > 0 else 0.0

    def flat_attack_value(self, amount: float) -> float:
        """고정 공/마 +amount 의 공% 가치: 배율이 같이 곱해지므로 기본 공격력(도핑 포함) 대비 비율"""
        base = self.stats.attack / self.stats.attack_multiplier + POTENTIAL.buff.attack_flat
        return (amount / base) / self.unit

    def as_dict(self) -> dict:
        return {
            "unit": "공격력 1%",
            "attack_multiplier": round(self.attack_multiplier, 3),
            "damage": self.damage,
            "boss_damage": self.boss_damage,
            "ignore_defense": self.ignore_defense,
            "boss_per_pct": round(self.boss_per_pct, 4),
            "ied_per_40": round(self.ied_value(40), 3),
            "stat_pct_unit": round(self.stat_pct_unit, 4),
            "flat_attack_unit": round(self.flat_attack_value(1), 4),
            "buff": POTENTIAL.weapon.buff.raw,
        }


def convert_weapon_line(option: str, context: WeaponContext, char_level: int) -> tuple[float, str] | None:
    """한 줄을 (공% 환산값, 근거) 로 바꿉니다. 규칙이 없는 줄은 None"""
    match = _ATTACK_PCT.match(option)
    if match:
        if match.group(1) != context.attack_label:
            return None
        return float(match.group(2)), f"{context.attack_label}%"
    match = _ATTACK_FLAT.match(option)
    if match:
        if match.group(1) != context.attack_label:
            return None
        value = context.flat_attack_value(float(match.group(2)))
        return value, f"고정 {context.attack_label} × {context.flat_attack_value(1):.3g}"
    match = _BOSS.match(option) or _DAMAGE.match(option)
    if match:
        return float(match.group(1)) * context.boss_per_pct, f"보공·데미지% × {context.boss_per_pct:.3g}"
    match = _IED.match(option)
    if match:
        pct = float(match.group(1))
        return context.ied_value(pct), f"방무 {pct:g}% → 남은 방어율 ×{1 - pct / 100:.2f}"
    converted = convert_line(option, context.stats, char_level)
    if converted is None:
        return None
    value, rule = converted
    return value * context.stat_pct_unit, f"{rule} → 유효 스탯 1% = 공 {context.stat_pct_unit:.3g}%"


def analyze_weapon_lines(options: list[str], context: WeaponContext, char_level: int) -> dict:
    recognized, total = [], 0.0
    for option in options:
        converted = convert_weapon_line(option, context, char_level) if option else None
        if converted is None:
            continue
        value, rule = converted
        total += value
        recognized.append({"option": option, "value": round(value, 2), "rule": rule})
    return score_detail(total, recognized, options, context.stats.main_label)


def soul_lines(item_data: dict) -> list[str]:
    """소울 잠재(최대 3줄). 소울 옵션 줄은 세지 않습니다."""
    return [str(item_data.get(key)).strip() for key in SOUL_KEYS if item_data.get(key) and str(item_data.get(key)).strip()]


def analyze_weapon_item(item_data: dict, kind: str, context: WeaponContext, char_level: int) -> dict:
    """kind: potential · additional_potential · soul"""
    options = soul_lines(item_data) if kind == "soul" else get_potential_options(item_data, kind)
    return analyze_weapon_lines(options, context, char_level)


def weapon_add_value(add_option: dict, context: WeaponContext, char_level: int) -> tuple[float, list[dict]]:
    """무기 추가옵션(환생의 불꽃)을 공% 단위 유효 급수로 합산합니다."""
    add_option = add_option or {}
    label = context.attack_label
    main = context.stats.main_label
    lines = [
        (f"{label} +{add_option.get('magic_power' if label == '마력' else 'attack_power') or 0}", None),
        (f"보스 몬스터 데미지 +{add_option.get('boss_damage') or 0}%", None),
        (f"데미지 +{add_option.get('damage') or 0}%", None),
        (f"올스탯 +{add_option.get('all_stat') or 0}", None),
    ]
    if main in ("STR", "DEX", "INT", "LUK"):
        lines.append((f"{main} +{add_option.get(main.lower()) or 0}", None))
    recognized, total = [], 0.0
    for text, _ in lines:
        if text.endswith(" +0") or text.endswith(" +0%"):
            continue
        converted = convert_weapon_line(text, context, char_level)
        if converted is None:
            continue
        value, rule = converted
        total += value
        recognized.append({"option": text, "value": round(value, 2), "rule": rule})
    return round(total, 2), recognized


def tier_of(item_name: str) -> str:
    """최상 줄 표의 단계: 이름에 destiny_keyword 가 있으면 destiny, 아니면 default"""
    return "destiny" if POTENTIAL.weapon.best_lines.destiny_keyword in (item_name or "") else "default"


def best_lines_total(kind: str, part_group: str, context: WeaponContext, char_level: int, tier: str = "default") -> float:
    """캐릭터별 기준점: potential.toml [weapon.best_lines] 표의 최상 조합을 공% 단위로 환산한 값"""
    config = POTENTIAL.weapon.best_lines
    fixed = config.raw.get(f"{kind}_total")
    if fixed is not None:  # 고정 기준 만점 (공격력%)
        return float(fixed)
    candidates = config.raw[tier][kind]
    if part_group == "emblem":
        candidates = [text for text in candidates if "보스" not in text]  # 엠블렘에는 보공 줄이 없습니다

    def value_of(text):
        text = text.replace("공격력", context.attack_label) if context.attack_label == "마력" else text
        converted = convert_weapon_line(text, context, char_level)
        return converted[0] if converted else 0.0

    # 캐릭터에게 가장 좋은 줄이 count 줄 모두에 붙은 조합
    return round(max(value_of(text) for text in candidates) * config.raw["count"][kind], 2)


def part_group(slot: str, part: str) -> str:
    if "엠블렘" in part or "엠블렘" in slot:
        return "emblem"
    if "보조무기" in slot:
        return "secondary"
    return "weapon"


def weapon_attack_tier(item_data: dict, attack_label: str, amount: float) -> int | None:
    """무기 공/마 추옵 수치의 불꽃 단계.

    1) 데스티니·제네시스는 무기 종류별 1추~5추 수치표(potential.toml [weapon.flame_attack])에서 같은 값을 찾습니다.
    2) 표에 없으면 7단계 수치(기본 공/마 × attack_ratio)에 대한 비율이 attack_tier_ratio 의 한 단계와
       ±0.03 안에서 맞을 때 그 단계, 아니면 None (1~2단계 포함)입니다.
    """
    name = item_data.get("item_name") or ""
    part = (item_data.get("item_equipment_part") or "").replace(" ", "")
    for set_name, table in POTENTIAL.weapon.flame_attack.raw.items():
        values = table.get(part) if set_name in name else None
        if values and int(amount) == amount and int(amount) in values:
            return 7 - values.index(int(amount))
    config = POTENTIAL.weapon.add_best
    base = item_data.get("item_base_option") or {}
    level = int(base.get("base_equipment_level") or 0)
    base_attack = float(base.get("magic_power" if attack_label == "마력" else "attack_power") or 0)
    if base_attack <= 0 or amount <= 0:
        return None
    ratio = next(value for level_min, value in config.attack_ratio if level >= level_min)
    observed = amount / (base_attack * ratio)
    tier, expected = min(config.attack_tier_ratio, key=lambda row: abs(observed - row[1]))
    return int(tier) if abs(observed - expected) <= 0.03 else None


def weapon_add_best(item_data: dict, context: WeaponContext, char_level: int) -> float:
    """무기 추옵 기준점: 레벨별 불꽃 최대 조합의 공% 환산값 (potential.toml [weapon.add_best]).

    공/마 줄(기본 공격력 × 7등급 비율)은 항상 넣고, 보공·데미지·올스탯·주스탯 단일 7등급 중 환산값이 큰 줄로 나머지를 채웁니다.
    """
    config = POTENTIAL.weapon.add_best
    flame = SCORING.add_option.flame
    tier = flame.max_tier
    base = item_data.get("item_base_option") or {}
    level = int(base.get("base_equipment_level") or 0)
    base_attack = float(base.get("magic_power" if context.attack_label == "마력" else "attack_power") or 0)
    ratio = next(value for level_min, value in config.attack_ratio if level >= level_min)
    single = next(band for band in flame.bands if level >= band[0])[1] * tier
    main = context.stats.main_label

    def value_of(text):
        converted = convert_weapon_line(text, context, char_level)
        return converted[0] if converted else 0.0

    attack = value_of(f"{context.attack_label} +{round(base_attack * ratio)}")
    others = [
        f"보스 몬스터 데미지 +{config.boss_damage_per_tier * tier:g}%",
        f"데미지 +{config.damage_per_tier * tier:g}%",
        f"올스탯 +{config.all_stat_per_tier * tier:g}",
    ]
    if main in ("STR", "DEX", "INT", "LUK"):
        others.append(f"{main} +{single:g}")
    best_others = sorted((value_of(text) for text in others), reverse=True)[: max(config.lines - 1, 0)]
    return round(attack + sum(best_others), 2)
