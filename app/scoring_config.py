"""점수 엔진의 상수와 규칙을 app/data/*.toml 에서 읽습니다.

import 시 한 번 읽고 필수 키와 타입을 검증하므로, 파일이 잘못되면 서버가 뜨지 않아 배포 전에 드러납니다.
"""

import tomllib
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
NUMBER = (int, float)


class ConfigError(ValueError):
    pass


class Section:
    """TOML 테이블을 속성으로 읽는 읽기 전용 래퍼. 없는 키는 어디가 빠졌는지 알려주는 ConfigError 가 됩니다."""

    __slots__ = ("_data", "_source")

    def __init__(self, data: dict, source: str):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_source", source)

    def __getattr__(self, name: str):
        if name not in self._data:
            raise ConfigError(f"{self._source}: '{name}' 항목이 없습니다")
        value = self._data[name]
        return Section(value, f"{self._source}.{name}") if isinstance(value, dict) else value

    def __setattr__(self, name, value):
        raise AttributeError("설정은 읽기 전용입니다")

    # 레벨별 목표 표처럼 키가 데이터인 테이블은 dict 처럼 조회합니다.
    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    @property
    def raw(self) -> dict:
        return self._data


def load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} 가 없습니다") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def validate(data: dict, spec: dict, source: str) -> None:
    """spec 은 {키: 타입 | 하위 spec} 형태입니다. 빠진 키와 타입 불일치를 경로와 함께 알립니다."""
    for key, expected in spec.items():
        if key not in data:
            raise ConfigError(f"{source}: '{key}' 항목이 없습니다")
        value = data[key]
        if isinstance(expected, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{source}.{key}: 테이블이어야 합니다")
            validate(value, expected, f"{source}.{key}")
        elif expected is bool:
            if not isinstance(value, bool):
                raise ConfigError(f"{source}.{key}: true/false 여야 합니다")
        elif not isinstance(value, expected) or isinstance(value, bool):
            raise ConfigError(f"{source}.{key}: 타입이 잘못되었습니다 ({type(value).__name__})")


def _int_keyed(table: dict, source: str) -> dict:
    try:
        return {int(key): value for key, value in table.items()}
    except ValueError as exc:
        raise ConfigError(f"{source}: 키는 정수(아이템 레벨)여야 합니다") from exc


_CLASSES_SPEC = {
    "main_stat": dict, "special": dict, "switchable": dict, "default": str,
    "secondary_stat": dict, "secondary_stat_overrides": dict, "archer_classes": list, "stat_weight": dict,
}
_SCORING_SPEC = {
    "add_option": {
        "grade": {k: NUMBER for k in ("all_stat_pct_default", "all_stat_pct_all_stat", "attack_default", "attack_all_stat", "attack_hp", "magic_int", "sub_stat_divisor")},
        "flame": {"max_tier": NUMBER, "bands": list, "other_per_tier": NUMBER},
    },
    "potential": {"excluded_slots": list, "ignored_keywords": list, "stat_keywords": list, "relevant_keywords": dict},
    "starforce": {
        **{k: NUMBER for k in ("reference_level", "level_weight_divisor", "base_star", "over_star_log_weight")},
        "max_star": {"bands": list, "default": NUMBER, "superior": NUMBER, "names": dict},
    },
    "weights": {k: NUMBER for k in ("add", "superior_star")},
    "noljang": {k: NUMBER for k in ("star_min", "star_max", "max_level", "etc_stat_strong", "etc_attack_strong", "etc_stat_weak", "etc_attack_weak")},
    "special_items": {"wse_slots": list, "special_parts": list, "secondary_slots": list, "superior_keyword": str},
}


def load_classes(path: Path = DATA_DIR / "classes.toml") -> Section:
    data = load_toml(path)
    validate(data, _CLASSES_SPEC, path.name)
    for stat, classes in data["main_stat"].items():
        if not isinstance(classes, list) or not all(isinstance(name, str) for name in classes):
            raise ConfigError(f"{path.name}: main_stat.{stat} 는 직업명 목록이어야 합니다")
    for name, rule in data["switchable"].items():
        if not isinstance(rule, dict) or not {"base", "alternative"} <= set(rule):
            raise ConfigError(f"{path.name}: switchable.{name} 에는 base 와 alternative 가 필요합니다")
    if not isinstance(data["stat_weight"].get("default"), NUMBER):
        raise ConfigError(f"{path.name}: stat_weight.default 가 필요합니다")
    return Section(data, path.name)


def load_scoring(path: Path = DATA_DIR / "scoring.toml") -> Section:
    data = load_toml(path)
    validate(data, _SCORING_SPEC, path.name)
    previous = None
    for band in data["add_option"]["flame"]["bands"]:
        if not (isinstance(band, list) and len(band) == 4 and all(isinstance(v, NUMBER) for v in band)):
            raise ConfigError(f"{path.name}: add_option.flame.bands 항목은 [레벨 하한, 단일 추옵, 이중 추옵, 최대 HP] 여야 합니다")
        if previous is not None and band[0] > previous:
            raise ConfigError(f"{path.name}: add_option.flame.bands 는 레벨 하한 내림차순이어야 합니다")
        previous = band[0]
    if data["add_option"]["flame"]["bands"][-1][0] != 0:
        raise ConfigError(f"{path.name}: add_option.flame.bands 의 마지막 항목은 레벨 하한 0 이어야 합니다")
    previous = None
    for band in data["starforce"]["max_star"]["bands"]:
        if not (isinstance(band, list) and len(band) == 2 and all(isinstance(v, NUMBER) for v in band)):
            raise ConfigError(f"{path.name}: starforce.max_star.bands 항목은 [레벨 미만, 최대 스타포스] 여야 합니다")
        if previous is not None and band[0] < previous:
            raise ConfigError(f"{path.name}: starforce.max_star.bands 는 레벨 오름차순이어야 합니다")
        previous = band[0]
    return Section(data, path.name)


_GRADES_SPEC = {
    "item_grade": list, "item_grade_default": str,
    "card_highlight": list, "card_highlight_default": str,
    "score_color": list, "score_color_default": str,
    "stat_bar": {"pot": list, "pot_additional": list, "star": list, "add": list, "default": str},
    "stat_bar_max": {"add": NUMBER, "pot": NUMBER, "pot_additional": NUMBER, "star": NUMBER},
    "overall": list,
}


def check_bands(bands, source: str, width: int) -> None:
    """[기준, 티어(, 초과 여부)] 목록이 내림차순인지 확인합니다. 첫 일치 규칙이라 순서가 틀리면 조용히 잘못 매칭됩니다."""
    previous = None
    for band in bands:
        if not (isinstance(band, list) and len(band) == width and isinstance(band[0], NUMBER) and isinstance(band[1], str)):
            raise ConfigError(f"{source}: 각 항목은 [기준, 티어{', 초과 여부' if width == 3 else ''}] 이어야 합니다")
        if width == 3 and not isinstance(band[2], bool):
            raise ConfigError(f"{source}: 세 번째 값은 true/false 여야 합니다")
        if previous is not None and band[0] > previous:
            raise ConfigError(f"{source}: 기준값은 내림차순이어야 합니다")
        previous = band[0]


def load_grades(path: Path = DATA_DIR / "grades.toml") -> Section:
    data = load_toml(path)
    validate(data, _GRADES_SPEC, path.name)
    for name in ("item_grade", "card_highlight", "score_color"):
        check_bands(data[name], f"{path.name}: {name}", 2)
    for name in ("pot", "pot_additional", "star", "add"):
        check_bands(data["stat_bar"][name], f"{path.name}: stat_bar.{name}", 3)
    previous = None
    for entry in data["overall"]:
        if not isinstance(entry, dict) or not {"min", "rank", "comment"} <= set(entry):
            raise ConfigError(f"{path.name}: overall 항목에는 min, rank, comment 가 필요합니다")
        if ("weapon_keyword" in entry) != ("with_weapon" in entry):
            raise ConfigError(f"{path.name}: overall 의 weapon_keyword 와 with_weapon 은 함께 있어야 합니다")
        if previous is not None and entry["min"] > previous:
            raise ConfigError(f"{path.name}: overall 의 min 은 내림차순이어야 합니다")
        previous = entry["min"]
    return Section(data, path.name)


RULE_OPERATORS = ("<", "<=", ">", ">=", "==", "!=")
_GUIDES_SPEC = {
    "axes": {"labels": list, "benchmarks": list, "no_add_parts": list, "no_star_parts": list},
    "targets": {k: NUMBER for k in ("star_330_level_split", "star_330_low", "star_330_high", "star_mid", "star_step")},
    "names": {k: list for k in ("upgrade_hearts", "target_hearts", "black_hearts", "superior", "limited")},
    "special_rule": list,
    "rule": list,
}


def check_rules(rules: list, names: dict, fields: set, source: str) -> None:
    """규칙 표의 구조를 검증합니다. 모르는 필드·비교 연산자·names 참조는 기동 시점에 걸립니다."""
    if not rules:
        raise ConfigError(f"{source}: 규칙이 하나도 없습니다")
    for index, rule in enumerate(rules):
        where = f"{source}[{index}]"
        if not isinstance(rule, dict) or not isinstance(rule.get("text"), str):
            raise ConfigError(f"{where}: text 가 필요합니다")
        name_any = rule.get("name_any")
        if isinstance(name_any, str) and name_any not in names:
            raise ConfigError(f"{where}: names 표에 '{name_any}' 가 없습니다")
        if name_any is not None and not isinstance(name_any, (str, list)):
            raise ConfigError(f"{where}: name_any 는 names 표의 이름이나 목록이어야 합니다")
        for condition in rule.get("when", []):
            if not (isinstance(condition, list) and len(condition) == 3):
                raise ConfigError(f"{where}: when 항목은 [필드, 비교, 값] 이어야 합니다")
            field, op, value = condition
            if field not in fields or op not in RULE_OPERATORS:
                raise ConfigError(f"{where}: 알 수 없는 필드나 비교 ({field!r} {op!r})")
            if isinstance(value, str) and value not in fields:
                raise ConfigError(f"{where}: 값 자리의 '{value}' 는 필드 이름이 아닙니다")
    last = rules[-1]
    if any(key in last for key in ("when", "name_any", "part_any")):
        raise ConfigError(f"{source}: 마지막 규칙은 조건 없이 모든 경우를 받아야 합니다")


def load_guides(path: Path = DATA_DIR / "guides.toml", rule_fields: set | None = None, special_fields: set | None = None) -> Section:
    data = load_toml(path)
    validate(data, _GUIDES_SPEC, path.name)
    if len(data["axes"]["labels"]) != 4 or len(data["axes"]["benchmarks"]) != 4:
        raise ConfigError(f"{path.name}: axes.labels 와 axes.benchmarks 는 4개여야 합니다")
    if rule_fields is not None:
        check_rules(data["rule"], data["names"], rule_fields, f"{path.name}: rule")
    if special_fields is not None:
        check_rules(data["special_rule"], data["names"], special_fields, f"{path.name}: special_rule")
    return Section(data, path.name)


_POTENTIAL_SPEC = {
    "sub_stat_ratio": NUMBER, "cooldown_per_second": NUMBER, "crit_base": NUMBER,
    "buff": {k: NUMBER for k in ("main_stat_flat", "attack_flat", "attack_pct", "ap_stat_pct", "crit_damage_pct")},
    "ap": {"base": NUMBER, "per_level": NUMBER},
    "bounds": {"pct_per_flat": list, "pct_per_attack": list, "pct_per_crit_damage": list},
    "reference": {k: NUMBER for k in ("level", "base_stat", "stat_pct", "attack", "crit_damage", "crit_rate")},
    "exempt_estimate": {"bands": list},
    "hyper_stat": {"main_stat_per_level": NUMBER},
    "crit_rate": {"zero_threshold": NUMBER, "reinforce_ratio": NUMBER, "reinforce_uptime": NUMBER},
    "weapon": {
        "boss_defense": NUMBER, "secondary_star_level": NUMBER,
        "buff": {k: NUMBER for k in ("attack_pct_extra", "boss_damage_extra", "ied_extra_pct")},
        "add_best": {"attack_ratio": list, "boss_damage_per_tier": NUMBER, "damage_per_tier": NUMBER, "all_stat_per_tier": NUMBER, "lines": NUMBER},
        "best_lines": {
            "count": dict, "soul_total": NUMBER, "destiny_keyword": str,
            "destiny": {"potential": list, "additional_potential": list},
            "default": {"potential": list, "additional_potential": list},
        },
    },
    "special": {
        "ignored_keys": list, "add_option_parts": list, "ring_part": str, "no_grade_parts": list, "no_score_parts": list,
        "best": dict, "ring": {"max_level": NUMBER, "weights": dict, "default": NUMBER},
    },
    "exceptional": {"parts": list, "points_per_upgrade": NUMBER},
    "armor": {"best_lines": {"count": dict, "tiers": list}},
}


def load_potential(path: Path = DATA_DIR / "potential.toml") -> Section:
    data = load_toml(path)
    validate(data, _POTENTIAL_SPEC, path.name)
    for name, bounds in data["bounds"].items():
        if not (isinstance(bounds, list) and len(bounds) == 2 and all(isinstance(v, NUMBER) for v in bounds) and bounds[0] <= bounds[1]):
            raise ConfigError(f"{path.name}: bounds.{name} 은 [최소, 최대] 여야 합니다")
    previous = None
    for band in data["exempt_estimate"]["bands"]:
        if not (isinstance(band, list) and len(band) == 2 and all(isinstance(v, NUMBER) for v in band)):
            raise ConfigError(f"{path.name}: exempt_estimate.bands 항목은 [레벨 이하, 추정치] 여야 합니다")
        if previous is not None and band[0] < previous:
            raise ConfigError(f"{path.name}: exempt_estimate.bands 는 레벨 오름차순이어야 합니다")
        previous = band[0]
    best_lines = data["armor"]["best_lines"]
    tiers = best_lines["tiers"]
    if not tiers or tiers[-1][0] != 0:
        raise ConfigError(f"{path.name}: armor.best_lines.tiers 의 마지막 항목은 레벨 하한 0 이어야 합니다")
    for tier in tiers:
        if not (isinstance(tier, list) and len(tier) == 2 and isinstance(tier[0], NUMBER) and isinstance(tier[1], str)):
            raise ConfigError(f"{path.name}: armor.best_lines.tiers 항목은 [레벨 하한, 표 이름] 이어야 합니다")
        table = best_lines.get(tier[1])
        for kind in ("potential", "additional_potential"):
            has_lines = isinstance(table, dict) and isinstance(table.get(kind), list)
            has_total = isinstance(table, dict) and isinstance(table.get(f"{kind}_total"), NUMBER)
            if not (has_lines or has_total):
                raise ConfigError(f"{path.name}: armor.best_lines.{tier[1]} 에는 {kind} 목록이나 {kind}_total 이 필요합니다")
    return Section(data, path.name)


CLASSES = load_classes()
SCORING = load_scoring()
POTENTIAL = load_potential()
GRADES = load_grades()
