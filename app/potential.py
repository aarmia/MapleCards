"""무기류(무기·보조무기·엠블렘)를 제외한 잠재·에디셔널 줄을 유효 스탯%로 바꿉니다.

유효 1% = 잠재의 주스탯 1% 가 주는 가치. 캐릭터의 깡스탯 a(주스탯%가 적용되는 주스탯)와 스탯공격력 A = w × 주스탯 + 부스탯 에서
고정 스탯·공/마·크뎀·크확 1 의 가치를 유도합니다. 상수는 app/data/potential.toml, 직업별 계수는 classes.toml 에 있습니다.
"""

import re
from dataclasses import dataclass, field

from app.scoring_config import CLASSES, POTENTIAL, SCORING

_PCT_STAT = re.compile(r"^(STR|DEX|INT|LUK|올스탯|최대 HP) \+(\d+)%$")
_FLAT_STAT = re.compile(r"^(STR|DEX|INT|LUK|올스탯|최대 HP) \+(\d+)$")
_LEVEL_STAT = re.compile(r"(\d+)레벨 당 (STR|DEX|INT|LUK) \+(\d+)$")
_FLAT_ATTACK = re.compile(r"^(공격력|마력) \+(\d+)$")
_CRIT_DAMAGE = re.compile(r"^크리티컬 데미지 \+(\d+)%$")
_CRIT_RATE = re.compile(r"^크리티컬 확률 \+(\d+)%$")
_COOLDOWN = re.compile(r"재사용 대기시간 -(\d+)초")


def class_key(char_class) -> str:
    return (char_class or "").replace(" ", "")


def stat_weight(char_class) -> float:
    """스탯 공식 A = w × 주스탯 + 부스탯 의 w. 일반 직업 4, 제논 3.5, 데몬어벤져 4 ÷ 14 (classes.toml stat_weight)"""
    return CLASSES.stat_weight.raw.get(class_key(char_class), CLASSES.stat_weight.default)


def main_names(main_stat: str) -> tuple[str, ...]:
    """스탯 응답에서 주스탯으로 더할 항목. 제논은 STR·DEX·LUK 의 합이고 INT 는 넣지 않습니다."""
    if main_stat == "all_stat":
        return ("STR", "DEX", "LUK")
    if main_stat == "hp":
        return ("HP",)
    return (main_stat.upper(),)


def secondary_names(char_class, main_stat: str) -> tuple[str, ...]:
    override = CLASSES.secondary_stat_overrides.raw.get(class_key(char_class))
    return tuple(override if override is not None else CLASSES.secondary_stat.raw.get(main_stat, []))


def is_archer(char_class) -> bool:
    return class_key(char_class) in CLASSES.archer_classes


def _clamp(value: float, bounds) -> float:
    low, high = bounds
    return min(high, max(low, value))


@dataclass(frozen=True)
class StatContext:
    """유효 스탯% 환산에 필요한 캐릭터 수치.

    basis 는 stat(스탯 응답 + 추가 조회) · estimated(추가 조회 일부 실패로 %미적용 스탯을 레벨로 추정) · reference(기준 캐릭터) 입니다.
    """

    main_stat: str
    main_names: tuple[str, ...]
    sub_names: tuple[str, ...]
    stat_weight: float  # w
    base_stat: float  # a: 깡스탯
    stat_pct: float  # b = 1 + 주스탯% 합 ÷ 100
    exempt_stat: float  # c: %미적용 주스탯
    final_main: float  # S: 최종 주스탯
    final_sub: float  # d: 최종 부스탯
    attack: float  # 공격력 또는 마력
    crit_damage: float  # 크리티컬 데미지 %
    crit_rate: float | None  # 크리티컬 확률 %. 모르면 None
    is_archer: bool
    ap_stat: float = 0.0  # AP 로 올린 주스탯 (메이플 용사류 버프 가정의 기준)
    basis: str = "stat"
    sources: dict = field(default_factory=dict)
    attack_multiplier: float = 1.0
    sub_pct_ratio: float | None = None
    sub_flat_ratio: float | None = None

    @property
    def main_label(self) -> str:
        return self.main_stat.upper()

    # ── 효율 계산은 전투 중 상태(도핑·버프 포함)를 기준으로 합니다. 가정값은 potential.toml [buff] ──
    @property
    def buff_stat(self) -> float:
        """전투 중 깡스탯에 더해지는 가정치: 주스탯 도핑 + 올스탯 도핑(주스탯 몫) + 메이플 용사류(AP 스탯 × ap_stat_pct)"""
        all_stat_share = {"hp": 0, "all_stat": 3}.get(self.main_stat, 1)  # 데몬어벤져 HP 는 올스탯이 안 붙고, 제논은 세 스탯 모두
        return (POTENTIAL.buff.main_stat_flat + POTENTIAL.buff.all_stat_flat * all_stat_share
                + self.ap_stat * POTENTIAL.buff.ap_stat_pct / 100)

    @property
    def buff_sub(self) -> float:
        """전투 중 부스탯에 더해지는 가정치: 올스탯 도핑 (부스탯 % 는 모르므로 그대로 더함)"""
        return POTENTIAL.buff.all_stat_flat if self.sub_names else 0.0

    @property
    def base_stat_effective(self) -> float:
        """a' = 깡스탯 + 버프 가정치"""
        return self.base_stat + self.buff_stat

    @property
    def stat_attack(self) -> float:
        """A = w × (S + 버프 가정치 × b) + (d + 부스탯 버프)"""
        return self.stat_weight * (self.final_main + self.buff_stat * self.stat_pct) + self.final_sub + self.buff_sub

    @property
    def attack_multiplier_effective(self) -> float:
        """공격력 % 배율 + 버프 공격력 %"""
        return self.attack_multiplier + POTENTIAL.buff.attack_pct / 100

    @property
    def attack_effective(self) -> float:
        """공' = (기본 공격력 + 도핑 고정 공격력) × 배율'"""
        return (self.attack / self.attack_multiplier + POTENTIAL.buff.attack_flat) * self.attack_multiplier_effective

    @property
    def pct_per_flat(self) -> float:
        """고정 주스탯 1 (% 적용) 의 유효 스탯%"""
        return _clamp(100 * self.stat_pct / self.base_stat_effective, POTENTIAL.bounds.pct_per_flat)

    @property
    def pct_per_attack(self) -> float:
        """공격력·마력 1 의 유효 스탯%"""
        value = 100 * self.stat_attack * self.attack_multiplier_effective / (self.stat_weight * self.base_stat_effective * self.attack_effective)
        return _clamp(value, POTENTIAL.bounds.pct_per_attack)

    @property
    def crit_damage_effective(self) -> float:
        """효율 계산에 쓰는 크뎀: 스탯창 값 + 전투 중 켜지는 버프 가정"""
        return self.crit_damage + POTENTIAL.buff.crit_damage_pct

    @property
    def crit_bonus(self) -> float:
        """크리티컬 1회의 추가 배율: (crit_base − 1) + 크뎀"""
        return POTENTIAL.crit_base - 1 + self.crit_damage_effective / 100

    @property
    def pct_per_crit_damage(self) -> float:
        """크리티컬 데미지 1% 의 유효 스탯%"""
        value = self.stat_attack / ((POTENTIAL.crit_base + self.crit_damage_effective / 100) * self.stat_weight * self.base_stat_effective)
        return _clamp(value, POTENTIAL.bounds.pct_per_crit_damage)

    @property
    def pct_per_crit_rate(self) -> float:
        """100% 까지 채우는 크리티컬 확률 1% 의 유효 스탯%. 캐릭터 크확이 기준 이상이면 0"""
        if self.crit_rate is None or self.crit_rate >= POTENTIAL.crit_rate.zero_threshold:
            return 0.0
        rate = self.crit_rate / 100
        return self.stat_attack * self.crit_bonus / (self.stat_weight * self.base_stat_effective * (1 + rate * self.crit_bonus))

    @property
    def pct_per_excess_crit_rate(self) -> float:
        """100% 를 넘는 크확 1% 의 유효 스탯%. 궁수의 크리티컬 리인포스만 가치가 있습니다"""
        if not self.is_archer:
            return 0.0
        config = POTENTIAL.crit_rate
        return config.reinforce_ratio * config.reinforce_uptime * self.pct_per_crit_damage

    def notes(self) -> list[str]:
        """상세 모달의 가중치 목록에 보여줄 환산 기준"""
        lines = [
            "공통 수식 기반 추정 효율 · 직업별 패시브·버프 조건을 별도 보정하지 않아 실제 효율 및 외부 환산 결과와 다를 수 있습니다",
            f"잠재 유효 스탯% 환산 · 고정 스탯 1 = {self.pct_per_flat:.3g}%, 공/마 1 = {self.pct_per_attack:.3g}%, "
            f"크리티컬 데미지 1% = {self.pct_per_crit_damage:.3g}%, 쿨감 1초 = {POTENTIAL.cooldown_per_second:g}%"
        ]
        optimization = self.sources.get("preset_optimization")
        if optimization:
            selected = optimization["selected"]
            labels = {"equipment": "장비", "hyper_stat": "하이퍼", "union": "유니온", "link": "링크", "ability": "어빌리티"}
            lines.append("저장 프리셋 조합 최적화 · " + " / ".join(f"{labels[key]} {value if value != 'current' else '현재'}" for key, value in selected.items()))
            lines.append(f"보스 방어율 {optimization['boss_defense_percent']:g}% 가정 · 실제 DPS가 아닌 스탯 비교 지수 · 조건부 가동률·쿨감·버프 지속시간은 미보정")
        if self.crit_rate is not None:
            lines.append(
                f"크리티컬 확률 (현재 {self.crit_rate:g}%) · 1% = {self.pct_per_crit_rate:.3g}%, "
                f"100% 초과 1% = {self.pct_per_excess_crit_rate:.3g}%"
            )
        if self.basis == "reference":
            lines.append("캐릭터 스탯을 알 수 없어 기준 캐릭터 값으로 환산")
        elif self.basis == "estimated":
            lines.append("일부 수치 또는 프리셋 효과는 추정값을 사용합니다")
        if self.sources.get("attack_pct_basis") == "equipment_only":
            lines.append("공/마% 복원은 장비·소울 옵션 기준 · API 최종 스탯에 포함된 스킬 효과는 그대로 사용하며 별도로 더하거나 빼지 않습니다")
        if self.sources.get("equipment_basis") == "selected_preset":
            if optimization:
                lines.append("현재 API 스탯을 기준으로 선택한 저장 프리셋의 인식 가능한 효과 차이만 반영합니다")
            elif self.sources.get("preset_projection") == "equipped":
                lines.append("최적 프리셋과 현재 착용 장비가 같아 실제 API 스탯을 사용합니다")
            else:
                lines.append("최적 프리셋 추정 환산 · 장비와 잠재의 고정 주스탯·부스탯·공/마 차이 반영 (특수 직업·복수 부스탯·스킬 효과 등은 일부 미보정)")
        if self.sources.get("set_projection_status") == "unresolved":
            reasons = self.sources.get("set_projection", {}).get("reasons", [])
            lines.append("세트 효과 차이 미보정 · " + (" / ".join(reasons) if reasons else "선택 프리셋을 실제 착용한 뒤 다시 조회해 주세요"))
        elif self.sources.get("set_projection_status") == "applied":
            report = self.sources["set_projection"]
            lines.append("API 대조 통과 후 세트 보정 · " + " / ".join(f"{x['set']} {x['before']}→{x['after']}" for x in report["changes"]))
        elif self.sources.get("set_projection_status") == "verified_unchanged":
            lines.append("현재 장비 세트 수·활성 효과 API 대조 통과 · 추가 세트 가산 없음")
        return lines

    def as_dict(self) -> dict:
        return {
            "basis": self.basis,
            "main_stat": self.main_label,
            "base_stat": round(self.base_stat),
            "stat_pct": round(self.stat_pct, 3),
            "exempt_stat": round(self.exempt_stat),
            "final_main": round(self.final_main),
            "final_sub": round(self.final_sub),
            "attack": round(self.attack),
            "attack_multiplier": self.attack_multiplier,
            "base_attack_estimate": round(self.attack / self.attack_multiplier),
            "main_stat_percent": round((self.stat_pct - 1) * 100, 3),
            "sub_stat_percent": round((self.sources["sub_multiplier"] - 1) * 100, 3) if "sub_multiplier" in self.sources else None,
            "sub_base_estimate": round(self.sources["sub_base_estimate"]) if "sub_base_estimate" in self.sources else None,
            "sub_exempt": self.sources.get("sub_exempt"),
            "crit_damage": self.crit_damage,
            "buff": POTENTIAL.buff.raw,
            "crit_rate": self.crit_rate,
            "pct_per_flat": round(self.pct_per_flat, 4),
            "pct_per_attack": round(self.pct_per_attack, 4),
            "pct_per_crit_damage": round(self.pct_per_crit_damage, 4),
            "pct_per_crit_rate": round(self.pct_per_crit_rate, 4),
            "pct_per_excess_crit_rate": round(self.pct_per_excess_crit_rate, 4),
            "sources": self.sources,
        }


def ap_stat(char_level: int) -> float:
    """AP 로 올린 주스탯 추정 (potential.toml [ap])"""
    return float(POTENTIAL.ap.base + POTENTIAL.ap.per_level * max(0, char_level - 1))


def reference_context(char_class, main_stat: str) -> StatContext:
    """스탯 응답이 없을 때(테스트·단독 호출) 쓰는 기준 캐릭터. 값은 potential.toml 의 reference"""
    ref = POTENTIAL.reference
    return StatContext(
        main_stat=main_stat,
        main_names=main_names(main_stat),
        sub_names=secondary_names(char_class, main_stat),
        stat_weight=stat_weight(char_class),
        base_stat=float(ref.base_stat),
        stat_pct=float(ref.stat_pct),
        exempt_stat=0.0,
        final_main=float(ref.base_stat * ref.stat_pct),
        final_sub=0.0,
        attack=float(ref.attack),
        crit_damage=float(ref.crit_damage),
        crit_rate=float(ref.crit_rate),
        is_archer=is_archer(char_class),
        ap_stat=ap_stat(ref.level),
        basis="reference",
    )


def standard_context(char_class, main_stat: str) -> StatContext:
    """메큘레이터 지수의 공통 기준 캐릭터 (potential.toml [standard]). 직업·주스탯 종류만 그 캐릭터 것을 씁니다."""
    std = POTENTIAL.standard
    return StatContext(
        main_stat=main_stat,
        main_names=main_names(main_stat),
        sub_names=secondary_names(char_class, main_stat),
        stat_weight=stat_weight(char_class),
        base_stat=float(std.base_stat),
        stat_pct=float(std.stat_pct),
        exempt_stat=float(std.exempt_stat),
        final_main=float(std.final_main),
        final_sub=float(std.final_sub),
        attack=float(std.attack),
        attack_multiplier=float(std.attack_multiplier),
        crit_damage=float(std.crit_damage),
        crit_rate=float(std.crit_rate),
        is_archer=is_archer(char_class),
        ap_stat=ap_stat(std.level),
        basis="standard",
    )


def _all_stat_factor(context: StatContext) -> float:
    """올스탯 N 의 배율: 주스탯 1 + 부스탯 수 × sub_stat_ratio. 데몬어벤져의 HP 는 올스탯에 들지 않습니다."""
    main_share = 0.0 if context.main_stat == "hp" else 1.0
    ratio = context.sub_pct_ratio if context.sub_pct_ratio is not None else POTENTIAL.sub_stat_ratio
    return main_share + ratio * len(context.sub_names)


def _stat_role(stat: str, context: StatContext) -> str | None:
    """스탯 이름이 이 캐릭터에게 main(주스탯) · xenon(제논 개별 스탯) · sub(부스탯) 중 무엇인지. 무관하면 None"""
    if stat in context.main_names:
        return "xenon" if context.main_stat == "all_stat" else "main"
    if stat in context.sub_names:
        return "sub"
    return None


def convert_line(option: str, context: StatContext, char_level: int) -> tuple[float, str] | None:
    """한 줄을 (유효 스탯%, 환산 근거) 로 바꿉니다. 규칙이 없는 줄은 None 입니다."""
    ratio = POTENTIAL.sub_stat_ratio
    pct_ratio = context.sub_pct_ratio if context.sub_pct_ratio is not None else ratio
    flat_ratio = context.sub_flat_ratio if context.sub_flat_ratio is not None else ratio

    match = _PCT_STAT.match(option)
    if match:
        stat, value = match.group(1), float(match.group(2))
        if stat == "올스탯":
            factor = _all_stat_factor(context)
            return value * factor, f"올스탯% × {factor:g}"
        if stat == "최대 HP":
            return (value, "주스탯%") if context.main_stat == "hp" else None
        role = _stat_role(stat, context)
        if role == "main":
            return value, "주스탯%"
        if role == "xenon":
            return value / 3, "제논 개별 스탯% ÷ 3"
        if role == "sub":
            return value * pct_ratio, f"부스탯% × {pct_ratio:.4g}"
        return None

    match = _FLAT_STAT.match(option)
    if match:
        stat, value = match.group(1), float(match.group(2))
        per_flat = context.pct_per_flat
        if stat == "올스탯":
            factor = _all_stat_factor(context)
            return value * factor * per_flat, f"고정 올스탯 × {factor:g} × {per_flat:.3g}"
        if stat == "최대 HP":
            return (value * per_flat, f"고정 HP × {per_flat:.3g}") if context.main_stat == "hp" else None
        role = _stat_role(stat, context)
        if role == "sub":
            return value * flat_ratio * per_flat, f"고정 부스탯 × {flat_ratio:.4g} × {per_flat:.3g}"
        if role is None:
            return None
        # 제논의 고정 개별 스탯은 세 스탯 합에 그대로 더해지므로 나누지 않습니다.
        return value * per_flat, f"고정 주스탯 × {per_flat:.3g}"

    match = _LEVEL_STAT.search(option)
    if match:
        interval, stat, value = int(match.group(1)), match.group(2), float(match.group(3))
        role = _stat_role(stat, context)
        if role is None or interval <= 0:
            return None
        level = char_level if char_level > 0 else POTENTIAL.reference.level
        flat_total = value * (level // interval)
        per_flat = context.pct_per_flat
        if role == "sub":
            return flat_total * flat_ratio * per_flat, f"{interval}레벨당 {value:g} → 고정 부스탯 {flat_total:g} × {flat_ratio:.4g} × {per_flat:.3g}"
        return flat_total * per_flat, f"{interval}레벨당 {value:g} → 고정 {flat_total:g} × {per_flat:.3g}"

    match = _FLAT_ATTACK.match(option)
    if match:
        kind, value = match.group(1), float(match.group(2))
        if kind != ("마력" if context.main_stat == "int" else "공격력"):
            return None
        return value * context.pct_per_attack, f"{kind} × {context.pct_per_attack:.3g}"

    match = _CRIT_DAMAGE.match(option)
    if match:
        return float(match.group(1)) * context.pct_per_crit_damage, f"크리티컬 데미지 × {context.pct_per_crit_damage:.3g}"

    match = _CRIT_RATE.match(option)
    if match:
        if context.crit_rate is None:
            return None
        value, current = float(match.group(1)), context.crit_rate
        useful = max(0.0, min(value, 100 - current))  # 100% 까지 채우는 부분
        excess = max(0.0, min(value, current + value - 100))  # 100% 를 넘는 부분
        points = useful * context.pct_per_crit_rate + excess * context.pct_per_excess_crit_rate
        parts = []
        if useful:
            parts.append(f"크확 {useful:g}% × {context.pct_per_crit_rate:.3g}")
        if excess:
            parts.append(f"초과 {excess:g}% × {context.pct_per_excess_crit_rate:.3g}")
        return points, " + ".join(parts) if parts else "크확 100% 달성"

    match = _COOLDOWN.search(option)
    if match:
        return float(match.group(1)) * POTENTIAL.cooldown_per_second, f"쿨감 초당 {POTENTIAL.cooldown_per_second:g}"

    return None


def classify_unrecognized(options: list[str], recognized: list[dict], main_label: str) -> tuple[list[dict], list[str]]:
    """점수에 반영되지 않은 옵션을 나눕니다.

    - ignored: 점수에 넣지 않기로 한 옵션 (scoring.toml potential.ignored_keywords)
    - other_stat: 다른 직업의 스탯을 올리는 옵션 (relevant_keywords 에 없는 스탯)
    - unsupported: 어디에도 속하지 않는, 엔진이 모르는 옵션. 로그 집계 대상은 이것뿐입니다.
    """
    config = SCORING.potential
    recognized_text = {entry["option"] for entry in recognized}
    relevant = config.relevant_keywords.raw.get(main_label, [])
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


def score_detail(score: float, recognized: list[dict], options: list[str], main_label: str) -> dict:
    ignored, unsupported = classify_unrecognized(options, recognized, main_label)
    return {"score": round(score, 2), "recognized": recognized, "ignored": ignored, "unsupported": unsupported}


def analyze_lines(options: list[str], context: StatContext, char_level: int) -> dict:
    """잠재 줄 목록을 유효 스탯% 로 합산하고 인식·제외·미지원으로 나눕니다."""
    recognized, total = [], 0.0
    for option in options:
        converted = convert_line(option, context, char_level) if option else None
        if converted is None:
            continue
        value, rule = converted
        total += value
        recognized.append({"option": option, "value": round(value, 2), "rule": rule})
    return score_detail(total, recognized, options, context.main_label)


def best_armor_total(kind: str, part: str, context: StatContext, char_level: int, item_level: int = 0) -> float:
    """방어구·장신구의 캐릭터별 기준점: potential.toml [armor.best_lines] 에서 아이템 레벨 표를 골라 후보 중 가장 좋은 줄 × count.
    부위 표에 <종류>_lines 가 있으면 그 줄 조합을 환산해 합산합니다 (장갑·모자)."""
    config = POTENTIAL.armor.best_lines
    tier_name = next(name for level_min, name in config.tiers if item_level >= level_min)
    table = config.raw[tier_name]
    part_table = (table.get("parts") or {}).get(part, {})
    fixed = part_table.get(f"{kind}_total", table.get(f"{kind}_total"))
    if fixed is not None:  # 고정 기준 만점 (유효 스탯%)
        return float(fixed)
    main = "최대 HP" if context.main_stat == "hp" else ("STR" if context.main_stat == "all_stat" else context.main_label)
    attack = "마력" if context.main_stat == "int" else "공격력"
    lines = part_table.get(f"{kind}_lines")
    if lines:
        total = 0.0
        for text in lines:
            converted = convert_line(text.replace("주스탯", main), context, char_level)
            total += converted[0] if converted else 0.0
        return round(total, 2)
    candidates = part_table.get(kind) or table[kind]
    values = []
    for text in candidates:
        if text.startswith(("공격력", "마력")) and not text.startswith(attack):
            continue  # 공격력 줄은 물리 직업, 마력 줄은 INT 직업만 후보
        converted = convert_line(text.replace("주스탯", main), context, char_level)
        values.append(converted[0] if converted else 0.0)
    return round(max(values) * config.raw["count"][kind], 2)
