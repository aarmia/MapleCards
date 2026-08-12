import unittest

from app.analyzer import evaluate_equipment, get_dynamic_guide
from app.calculator import (
    calculate_potential_score,
    get_main_stat,
    get_potential_options,
)


class PotentialOptionTests(unittest.TestCase):
    def setUp(self):
        self.challenger_top = {
            "item_equipment_part": "상의",
            "item_equipment_slot": "상의",
            "item_name": "도전자의 상의",
            "potential_option_grade": "유니크",
            "potential_option_1": "INT +9%",
            "potential_option_2": "INT +6%",
            "potential_option_3": None,
            "additional_potential_option_grade": "에픽",
            "additional_potential_option_1": "INT +4%",
            "additional_potential_option_2": "마력 +10",
            "additional_potential_option_3": None,
            "starforce": "22",
            "item_base_option": {"base_equipment_level": 200},
            "item_add_option": {},
            "item_etc_option": {},
            "item_starforce_option": {},
        }

    def test_lethe_uses_int_as_main_stat(self):
        self.assertEqual(get_main_stat("레테"), "int")

    def test_extracts_two_line_options_without_empty_third_line(self):
        self.assertEqual(
            get_potential_options(self.challenger_top, "potential"),
            ["INT +9%", "INT +6%"],
        )
        self.assertEqual(
            get_potential_options(self.challenger_top, "additional_potential"),
            ["INT +4%", "마력 +10"],
        )

    def test_two_line_potential_is_scored(self):
        self.assertEqual(
            calculate_potential_score(
                self.challenger_top,
                "potential",
                "레테",
                285,
            ),
            15.0,
        )
        self.assertEqual(
            calculate_potential_score(
                self.challenger_top,
                "additional_potential",
                "레테",
                285,
            ),
            7.0,
        )

    def test_analyzer_keeps_two_line_scores_and_raw_options(self):
        result = evaluate_equipment(
            [self.challenger_top],
            "레테",
            285,
        )[0]

        self.assertEqual(result["detail"]["pot"], 49.5)
        self.assertEqual(result["detail"]["pot_additional"], 17.5)
        self.assertEqual(
            result["raw_options"]["potential_options"],
            ["INT +9%", "INT +6%"],
        )
        self.assertEqual(result["scoring_basis"]["main_stat"], "INT")
        self.assertEqual(
            [entry["option"] for entry in result["scoring_basis"]["recognized_options"]],
            ["INT +9%", "INT +6%", "INT +4%", "마력 +10"],
        )
        self.assertEqual(
            [entry["weighted_value"] for entry in result["scoring_basis"]["recognized_options"]],
            [29.7, 19.8, 10.0, 7.5],
        )
        self.assertEqual(
            [entry["weighted_score"] for entry in result["scoring_basis"]["breakdown"]],
            [0, 49.5, 17.5, 100.0],
        )
        self.assertEqual(result["scoring_basis"]["star_score"], 100.0)

    def test_ring_does_not_recommend_unavailable_add_option(self):
        guide = get_dynamic_guide(
            [100.0, 108.9, 45.0, 100.0],
            22,
            "반지",
            353.9,
            "마이스터링",
            140,
        )

        self.assertNotIn("추가옵션", guide)
        self.assertIn("종결", guide)

    def test_unsupported_option_is_collected_in_server_log(self):
        item = dict(self.challenger_top)
        item["potential_option_2"] = "보스 몬스터 공격 시 데미지 +30%"

        with self.assertLogs("app.analyzer", level="WARNING") as captured:
            evaluate_equipment([item], "레테", 285)

        log_text = "\n".join(captured.output)
        self.assertIn("Unsupported equipment option", log_text)
        self.assertIn("potential", log_text)


if __name__ == "__main__":
    unittest.main()
