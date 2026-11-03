import unittest

from app.scoring_config import (
    DATA_DIR,
    ConfigError,
    Section,
    check_bands,
    load_classes,
    load_grades,
    load_scoring,
    load_toml,
    validate,
)


class ScoringConfigTests(unittest.TestCase):
    def test_shipped_files_load_and_missing_key_is_reported_with_path(self):
        classes, scoring, grades = load_classes(), load_scoring(), load_grades()

        self.assertIn("히어로", classes.main_stat.str)
        self.assertEqual(scoring.add_option.targets.default[250], 186)
        self.assertEqual(scoring.weights.potential, 3.3)
        self.assertEqual(grades.item_grade[0], [360, "SSS+"])
        self.assertEqual((grades.overall[0]["rank"], grades.overall[-1]["rank"]), ("ETERNAL", "EPIC"))
        with self.assertRaises(ConfigError):
            check_bands([[300, "a"], [350, "b"]], "x", 2)  # 첫 일치 규칙이라 내림차순이 아니면 거부

        broken = load_toml(DATA_DIR / "scoring.toml")
        del broken["potential"]["crit_damage"]["multi"]
        with self.assertRaises(ConfigError) as context:
            validate(broken, {"potential": {"crit_damage": {"multi": (int, float)}}}, "scoring.toml")
        self.assertIn("scoring.toml.potential.crit_damage: 'multi'", str(context.exception))

        with self.assertRaises(ConfigError):
            _ = Section({"a": 1}, "x").missing


if __name__ == "__main__":
    unittest.main()
