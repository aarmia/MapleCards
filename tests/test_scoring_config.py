import unittest

from app.scoring_config import (
    DATA_DIR,
    ConfigError,
    Section,
    check_bands,
    load_classes,
    load_grades,
    load_potential,
    load_scoring,
    load_toml,
    validate,
)


class ScoringConfigTests(unittest.TestCase):
    def test_shipped_files_load_and_missing_key_is_reported_with_path(self):
        classes, scoring, grades, potential = load_classes(), load_scoring(), load_grades(), load_potential()

        self.assertIn("히어로", classes.main_stat.str)
        self.assertEqual(potential.reference.base_stat * potential.bounds.pct_per_flat[0], 60)  # 범위는 [최소, 최대] 쌍으로 읽힙니다
        self.assertEqual(scoring.add_option.flame.bands[0], [250, 12, 7, 700])
        self.assertEqual(scoring.weights.add, 1.1)
        self.assertEqual(grades.item_grade[0], [90, "SSS+"])
        mins = [entry["min"] for entry in grades.overall]
        self.assertEqual(mins, sorted(mins, reverse=True))  # 첫 일치 규칙이라 내림차순
        self.assertEqual(grades.overall[-1]["min"], 0)  # 마지막 항목이 모든 평균을 받습니다
        with self.assertRaises(ConfigError):
            check_bands([[300, "a"], [350, "b"]], "x", 2)  # 첫 일치 규칙이라 내림차순이 아니면 거부

        broken = load_toml(DATA_DIR / "scoring.toml")
        del broken["add_option"]["flame"]["max_tier"]
        with self.assertRaises(ConfigError) as context:
            validate(broken, {"add_option": {"flame": {"max_tier": (int, float)}}}, "scoring.toml")
        self.assertIn("scoring.toml.add_option.flame: 'max_tier'", str(context.exception))

        with self.assertRaises(ConfigError):
            _ = Section({"a": 1}, "x").missing


if __name__ == "__main__":
    unittest.main()
