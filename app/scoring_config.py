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
    "scroll": {"weapon": dict, "default": dict, "parts": dict},
    "add_option": {
        "grade": {k: NUMBER for k in ("all_stat_pct_default", "all_stat_pct_all_stat", "attack_default", "attack_all_stat", "attack_hp", "magic_int", "sub_stat_divisor")},
        "flame": {"max_tier": NUMBER, "bands": list, "other_per_tier": NUMBER},
    },
    "potential": {"excluded_slots": list, "ignored_keywords": list, "stat_keywords": list, "relevant_keywords": dict},
    "starforce": {
        "max_star": {"bands": list, "default": NUMBER, "superior": NUMBER, "names": dict},
        "benchmark": {"benchmark_star": NUMBER, "bands": list, "glove_attack_bonus": NUMBER},
        "noljang": {"levels": list, "stat": list, "attack": list},
        "gain": {k: list for k in ("stat_1_15", "glove_attack_stars", "levels", "stat_16", "attack_16")},
        "weapon": {"low_divisor": NUMBER, "weapon_like_min_attack": NUMBER, "attack_16": list},
    },
    "noljang": {k: NUMBER for k in ("star_min", "star_max", "max_level", "etc_stat_strong", "etc_attack_strong", "etc_stat_weak", "etc_attack_weak")},
    "special_items": {"wse_slots": list, "special_parts": list, "secondary_slots": list, "superior_keyword": str,
                      "no_add_parts": list, "limited_names": list},
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
    scroll = data["scroll"]
    for name, rule in [("weapon", scroll["weapon"]), ("default", scroll["default"]), *scroll["parts"].items()]:
        if not isinstance(rule, dict) or not isinstance(rule.get("attack"), NUMBER) or \
                not any(isinstance(rule.get(key), NUMBER) for key in ("main_stat", "all_stat")):
            raise ConfigError(f"{path.name}: scroll.{name} 에는 attack 과 main_stat 또는 all_stat 숫자가 필요합니다")
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
    for band in data["starforce"]["benchmark"]["bands"]:
        if not (isinstance(band, list) and len(band) == 3 and all(isinstance(v, NUMBER) for v in band)):
            raise ConfigError(f"{path.name}: starforce.benchmark.bands 항목은 [레벨 하한, 스탯, 공/마] 여야 합니다")
        if previous is not None and band[0] > previous:
            raise ConfigError(f"{path.name}: starforce.benchmark.bands 는 레벨 하한 내림차순이어야 합니다")
        previous = band[0]
    gain = data["starforce"]["gain"]
    if not (len(gain["stat_1_15"]) == 15 and len(gain["levels"]) == len(gain["stat_16"]) == len(gain["attack_16"])
            and gain["levels"] == sorted(gain["levels"], reverse=True)
            and all(len(stat) <= 7 and len(stat) <= len(attack) for stat, attack in zip(gain["stat_16"], gain["attack_16"]))):
        raise ConfigError(f"{path.name}: starforce.gain 은 1~15성 15개, 레벨 내림차순, 레벨마다 stat_16(최대 7개) ≤ attack_16 길이여야 합니다")
    for level, stat, attack in zip(gain["levels"], gain["stat_16"], gain["attack_16"]):
        band = next((b for b in data["starforce"]["benchmark"]["bands"] if b[0] == level), None)
        if band and len(attack) >= 7 and [sum(gain["stat_1_15"]) + sum(stat), sum(attack[:7])] != band[1:]:
            raise ConfigError(f"{path.name}: starforce.gain {level}제 22성 합계가 starforce.benchmark.bands 와 다릅니다")
    weapon = data["starforce"]["weapon"]
    if len(weapon["attack_16"]) != len(gain["levels"]) or any(
            len(w) != len(st) for w, st in zip(weapon["attack_16"], gain["stat_16"])):
        raise ConfigError(f"{path.name}: starforce.weapon.attack_16 은 starforce.gain 과 레벨 수, 레벨별 16~22성 수(stat_16)가 같아야 합니다")
    noljang = data["starforce"]["noljang"]
    if not (len(noljang["levels"]) == len(noljang["stat"]) == len(noljang["attack"])
            and all(len(row) == 5 for row in noljang["stat"]) and all(len(row) == 10 for row in noljang["attack"])):
        raise ConfigError(f"{path.name}: starforce.noljang 은 레벨마다 스탯 5개(1~5성)·공/마 10개(6~15성)가 필요합니다")
    previous = None
    for band in data["starforce"]["max_star"]["bands"]:
        if not (isinstance(band, list) and len(band) == 2 and all(isinstance(v, NUMBER) for v in band)):
            raise ConfigError(f"{path.name}: starforce.max_star.bands 항목은 [레벨 미만, 최대 스타포스] 여야 합니다")
        if previous is not None and band[0] < previous:
            raise ConfigError(f"{path.name}: starforce.max_star.bands 는 레벨 오름차순이어야 합니다")
        previous = band[0]
    return Section(data, path.name)


_GRADES_SPEC = {
    "card_highlight": list, "card_highlight_default": str,
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
    for name in ("card_highlight",):
        check_bands(data[name], f"{path.name}: {name}", 2)
    previous = None
    for entry in data["overall"]:
        if not isinstance(entry, dict) or not {"min", "rank"} <= set(entry):
            raise ConfigError(f"{path.name}: overall 항목에는 min, rank 가 필요합니다")
        if previous is not None and entry["min"] > previous:
            raise ConfigError(f"{path.name}: overall 의 min 은 내림차순이어야 합니다")
        previous = entry["min"]
    return Section(data, path.name)


_POTENTIAL_SPEC = {
    "sub_stat_ratio": NUMBER, "cooldown_per_second": NUMBER, "crit_base": NUMBER,
    "buff": {k: NUMBER for k in ("main_stat_flat", "all_stat_flat", "attack_flat", "attack_pct", "ap_stat_pct", "crit_damage_pct",
                                 "damage_pct", "boss_damage_pct")},
    "ap": {"base": NUMBER, "per_level": NUMBER},
    "bounds": {"pct_per_flat": list, "pct_per_attack": list, "pct_per_crit_damage": list},
    "reference": {k: NUMBER for k in ("level", "base_stat", "stat_pct", "attack", "crit_damage", "crit_rate")},
    "standard": {k: NUMBER for k in ("level", "base_stat", "stat_pct", "exempt_stat", "final_main", "final_sub", "attack",
                                     "attack_multiplier", "crit_damage", "crit_rate", "damage", "boss_damage", "ignore_defense")},
    "exempt_estimate": {"bands": list},
    "hyper_stat": {"main_stat_per_level": NUMBER},
    "crit_rate": {"zero_threshold": NUMBER, "reinforce_ratio": NUMBER, "reinforce_uptime": NUMBER},
    "weapon": {
        "boss_defense": NUMBER, "secondary_star_level": NUMBER,
        "buff": {k: NUMBER for k in ("attack_pct_extra", "boss_damage_extra", "ied_extra_pct")},
        "add_best": {"attack_ratio": list, "attack_tier_ratio": list, "boss_damage_per_tier": NUMBER, "damage_per_tier": NUMBER, "all_stat_per_tier": NUMBER, "lines": NUMBER},
        "best_lines": {
            "count": dict, "soul_total": NUMBER, "destiny_keyword": str,
            "destiny": {"potential": list, "additional_potential": list},
            "default": {"potential": list, "additional_potential": list},
        },
    },
    "special": {
        "ignored_keys": list, "add_option_parts": list, "ring_part": str, "no_grade_parts": list, "no_score_parts": list,
        "best": dict,
    },
    "exceptional": {"parts": list},
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


def load_starforce_cost(path: Path = DATA_DIR / "starforce_cost.toml") -> Section:
    """스타포스 기대 비용 설정: 샤타 이벤트·성별 확률과 비용 분모·파괴 방지·확정 복구·노작 시세 기본값"""
    data = load_toml(path)
    validate(data, {
        "event": {k: NUMBER for k in ("cost_multiplier", "destroy_multiplier", "destroy_max_star", "restore_multiplier")},
        "odds": dict, "cost_denominator": dict, "prevention": {"stars": list},
        "restore": {"items": dict, "meso": dict}, "base_price": {"default": list, "item": list},
    }, path.name)
    stars = [str(star) for star in range(12, 30)]
    for star in stars:
        odds = data["odds"].get(star)
        if not (isinstance(odds, list) and len(odds) == 3 and abs(sum(odds) - 1) < 1e-6 and odds[0] > 0):
            raise ConfigError(f"{path.name}: odds.{star} 는 [성공(>0), 실패, 파괴] 이고 합이 1 이어야 합니다")
        if not (isinstance(data["cost_denominator"].get(star), NUMBER) and data["cost_denominator"][star] > 0):
            raise ConfigError(f"{path.name}: cost_denominator.{star} 는 양수여야 합니다")
    for level, row in data["restore"]["meso"].items():
        if not (str(level).isdigit() and all(star in data["restore"]["items"] and isinstance(v, NUMBER) for star, v in row.items())):
            raise ConfigError(f"{path.name}: restore.meso.{level} 의 성은 restore.items 에 있어야 하고 값은 숫자여야 합니다")
    previous = None
    for band in data["base_price"]["default"]:
        if not (isinstance(band, list) and len(band) == 2 and all(isinstance(v, NUMBER) for v in band)) or (
                previous is not None and band[0] > previous):
            raise ConfigError(f"{path.name}: base_price.default 는 [레벨 하한, 억 메소] 레벨 내림차순이어야 합니다")
        previous = band[0]
    if previous != 0:
        raise ConfigError(f"{path.name}: base_price.default 의 마지막 레벨 하한은 0 이어야 합니다")
    for index, item in enumerate(data["base_price"]["item"]):
        if not (isinstance(item.get("name"), str) and isinstance(item.get("price"), NUMBER) and item["price"] >= 0
                and isinstance(item.get("parts", []), list)):
            raise ConfigError(f"{path.name}: base_price.item[{index}] 에는 name, 0 이상 price 가 필요합니다")
    return Section(data, path.name)


def load_spec_up(path: Path = DATA_DIR / "spec_up.toml") -> Section:
    """스펙업 추천 설정: 효율 단위·큐브 재설정 비용·등급업 확률·천장·추가옵션 재설정 규칙"""
    data = load_toml(path)
    cube_spec = {"cost": list, "grade_up": list, "pity": list, "min_step": NUMBER}
    validate(data, {
        "ranking": {"unit": NUMBER, "top": NUMBER},
        "cube": {"grades": list, "potential": cube_spec, "additional": cube_spec},
        "flame": {"cost": NUMBER, "first_step": NUMBER, "step": NUMBER, "tiers": list, "tier_odds": list, "boss_bonus": NUMBER,
                  "lines": NUMBER, "options": list},
        "seed_ring": {"restraint": dict, "continuous": dict, "upgrade": dict, "materials": dict, "burst_share": dict},
        "listing": {"grades": list, "radiant_grades": list, "cube_cost_share": NUMBER, "flame_cost_share": NUMBER,
                    "radiant_flame_cost_share": NUMBER, "radiant_star_cap": NUMBER, "min_index_gain_pct": NUMBER, "grade_cap": dict,
                    "eternal_bundle_sets": list, "heart_scroll": list, "heart_scroll_cost": list,
                    "final_items": list, "radiant_needs_pitched": NUMBER, "medal_add": NUMBER},
        "soul": {"min_level": NUMBER, "keyword": str, "reset_cost": list, "grade_up": list, "pity": list, "miracle": NUMBER,
                 "amplify": dict, "materials": dict},
    }, path.name)
    soul = data["soul"]
    if not len(soul["grade_up"]) == len(soul["pity"]) == len(soul["reset_cost"]) - 1:
        raise ConfigError(f"{path.name}: soul.grade_up·pity 는 등급 수 − 1 개, reset_cost 는 등급 수여야 합니다")
    for stage, step in soul["amplify"].items():
        if step.get("material") not in soul["materials"] or not all(isinstance(step.get(k), NUMBER) for k in ("chance", "step", "pity", "meso")):
            raise ConfigError(f"{path.name}: soul.amplify.{stage} 의 material 은 soul.materials 에 있어야 하고 chance·step·pity·meso 는 숫자여야 합니다")
    for index, grade in enumerate([*data["listing"]["grades"], *data["listing"]["radiant_grades"]], 1):
        pairs = ("pot", "eddy", "add")
        if not isinstance(grade.get("star"), int) or not all(isinstance(grade.get(k), list) and len(grade[k]) == 2 for k in pairs) \
                or not all(isinstance(grade.get(k), dict) for k in ("armor", "accessory", "scroll_cost")):
            raise ConfigError(f"{path.name}: listing.grades[{index}] 에는 star 와 [250제, 200제 이하] pot·eddy·add, armor·accessory 주문서, scroll_cost 가 필요합니다")
    ring = data["seed_ring"]
    if not all(isinstance(v, list) and len(v) == 2 and 0 < v[0] <= v[1] < 100 for v in ring["burst_share"].values()) \
            or "default" not in ring["burst_share"] or not 0 < ring["continuous"].get("uptime", 0) <= 1:
        raise ConfigError(f"{path.name}: seed_ring.burst_share 는 default 와 직업별 [15초 %, 20초 %] (0 < 15초 ≤ 20초 < 100) 여야 합니다")
    for level, step in ring["upgrade"].items():
        if step.get("material") not in ring["materials"] or not all(isinstance(step.get(k), NUMBER) for k in ("count", "meso")):
            raise ConfigError(f"{path.name}: seed_ring.upgrade.{level} 의 material 은 materials 에 있어야 하고 count·meso 는 숫자여야 합니다")
    grades = len(data["cube"]["grades"])
    for kind in ("potential", "additional"):
        cube = data["cube"][kind]
        if not (len(cube["grade_up"]) == len(cube["pity"]) == grades - 1
                and all(len(band) == 2 and len(band[1]) == grades for band in cube["cost"]) and cube["cost"][-1][0] == 0):
            raise ConfigError(f"{path.name}: cube.{kind} 의 grade_up·pity 는 등급 수 − 1 개, cost 는 [레벨 하한, 등급별 비용] 이고 마지막 하한은 0 이어야 합니다")
    flame = data["flame"]
    if len(flame["tiers"]) != len(flame["tier_odds"]) or abs(sum(flame["tier_odds"]) - 1) > 1e-9 or flame["lines"] > len(flame["options"]):
        raise ConfigError(f"{path.name}: flame.tiers·tier_odds 길이가 같고 확률 합이 1, lines ≤ 옵션 종류 수여야 합니다")
    return Section(data, path.name)


CLASSES = load_classes()
SCORING = load_scoring()
POTENTIAL = load_potential()
GRADES = load_grades()
STARFORCE_COST = load_starforce_cost()
SPEC_UP = load_spec_up()
