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
        elif not isinstance(value, expected) or isinstance(value, bool):
            raise ConfigError(f"{source}.{key}: 타입이 잘못되었습니다 ({type(value).__name__})")


def _int_keyed(table: dict, source: str) -> dict:
    try:
        return {int(key): value for key, value in table.items()}
    except ValueError as exc:
        raise ConfigError(f"{source}: 키는 정수(아이템 레벨)여야 합니다") from exc


_FALLBACK = {"min_level": NUMBER, "slope": NUMBER, "offset": NUMBER}
_CLASSES_SPEC = {"main_stat": dict, "special": dict, "switchable": dict, "default": str}
_SCORING_SPEC = {
    "add_option": {
        "grade": {k: NUMBER for k in ("all_stat_pct_default", "all_stat_pct_all_stat", "attack_default", "attack_all_stat", "attack_hp", "magic_int")},
        "targets": {
            "default": dict, "default_fallback": _FALLBACK,
            "xenon": dict, "xenon_fallback": _FALLBACK,
            "demon_avenger": dict, "demon_avenger_fallback": _FALLBACK,
        },
        "no_add_parts": list,
        "curve": {"chunk": NUMBER, "above": {"mult": NUMBER, "decay": NUMBER}, "below": {"mult": NUMBER, "decay": NUMBER, "cap": NUMBER}},
    },
    "potential": {
        "excluded_slots": list, "cooldown_per_second": NUMBER, "all_stat_pct": NUMBER, "all_stat_pct_boost": NUMBER,
        "all_stat_pct_boost_classes": list, "level_stat": NUMBER, "attack_flat": NUMBER, "stat_flat": NUMBER,
        "demon_avenger_additional": NUMBER,
        "crit_damage": {"single": NUMBER, "multi": NUMBER, "additional": NUMBER},
        "xenon": {k: NUMBER for k in ("level_stat_divisor", "stat_pct_divisor", "attack_flat", "stat_flat", "multiplier")},
    },
    "weapon": {
        "add": {k: NUMBER for k in ("boss_damage", "damage", "all_stat", "main_stat")},
        "potential": {k: NUMBER for k in ("damage", "ignore_defense", "all_stat_pct", "main_stat_pct")},
    },
    "starforce": {k: NUMBER for k in ("reference_level", "level_weight_divisor", "base_star", "over_star_log_weight")},
    "weights": {k: NUMBER for k in ("potential", "additional", "superior_star", "weapon_add", "fixed_secondary_emblem")},
    "noljang": {k: NUMBER for k in ("star_min", "star_max", "max_level", "etc_stat_strong", "etc_attack_strong", "etc_stat_weak", "etc_attack_weak")},
    "special_items": {
        "wse_slots": list, "special_parts": list, "secondary_slots": list, "excluded_rings": list, "superior_keyword": str,
        "pocket_demon_avenger_divisor": NUMBER, "fixed": list, "default": NUMBER,
    },
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
    return Section(data, path.name)


def load_scoring(path: Path = DATA_DIR / "scoring.toml") -> Section:
    data = load_toml(path)
    validate(data, _SCORING_SPEC, path.name)
    targets = data["add_option"]["targets"]
    for name in ("default", "xenon", "demon_avenger"):
        targets[name] = _int_keyed(targets[name], f"{path.name}: add_option.targets.{name}")
    for entry in data["special_items"]["fixed"]:
        if not (isinstance(entry, list) and len(entry) == 2 and isinstance(entry[0], str) and isinstance(entry[1], NUMBER)):
            raise ConfigError(f"{path.name}: special_items.fixed 는 [이름, 점수] 쌍의 목록이어야 합니다")
    return Section(data, path.name)


CLASSES = load_classes()
SCORING = load_scoring()
