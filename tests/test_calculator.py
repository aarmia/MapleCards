import unittest
from collections import Counter

from app.analyzer import (
    build_character_profile,
    build_report,
    evaluate_equipment,
    get_dynamic_guide,
    select_guide,
    split_guide,
    unsupported_registry,
)
from app.calculator import (
    analyze_potential_score,
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

    def test_unsupported_option_is_warned_once_and_counted(self):
        item = dict(self.challenger_top)
        item["potential_option_2"] = "보스 몬스터 공격 시 데미지 +30%"
        unsupported_registry.reset()
        collector = Counter()

        with self.assertLogs("app.analyzer", level="WARNING") as captured:
            evaluate_equipment([item], "레테", 285, unsupported=collector)
            evaluate_equipment([item], "레테", 285, unsupported=collector)

        # 처음 본 옵션만 경고 1줄, 두 번째 진단부터는 집계만 됩니다.
        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.getMessage(), "unsupported_option_first_seen")
        self.assertEqual((record.potential_type, record.option), ("potential", "보스 몬스터 공격 시 데미지 +30%"))
        self.assertEqual(collector[("potential", "보스 몬스터 공격 시 데미지 +30%")], 2)
    def test_unrecognized_options_are_classified_and_exposed(self):
        item = dict(self.challenger_top)
        item["potential_option_3"] = "DEX +9%"                       # 레테(INT)에게는 다른 직업 스탯
        item["additional_potential_option_3"] = "방어력 +7%"          # 점수에 넣지 않기로 한 옵션
        item["potential_option_2"] = "크리티컬 확률 +9%"               # 엔진이 모르는 옵션
        unsupported_registry.reset()
        collector = Counter()

        with self.assertLogs("app.analyzer", level="WARNING") as captured:
            result = evaluate_equipment([item], "레테", 285, unsupported=collector)[0]

        self.assertEqual(
            result["scoring_basis"]["unrecognized_options"],
            [
                {"category": "윗잠재", "option": "DEX +9%", "reason": "other_stat"},
                {"category": "윗잠재", "option": "크리티컬 확률 +9%", "reason": "unsupported"},
                {"category": "에디셔널", "option": "방어력 +7%", "reason": "ignored"},
            ],
        )
        # 로그와 집계에는 엔진이 모르는 옵션만 올라갑니다.
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(list(collector), [("potential", "크리티컬 확률 +9%")])

    def test_switchable_classes_follow_the_higher_stat(self):
        stats = {
            "final_stat": [
                {"stat_name": "STR", "stat_value": "7901"},
                {"stat_name": "DEX", "stat_value": "64324"},
                {"stat_name": "보스 몬스터 데미지", "stat_value": "462.00"},
                {"stat_name": "MP", "stat_value": None},
            ]
        }

        self.assertEqual(get_main_stat("바이퍼", stats), "dex")  # 기본 STR 이지만 DEX 가 더 높음
        self.assertEqual(get_main_stat("캡틴", stats), "dex")  # 기본 DEX 유지
        self.assertEqual(get_main_stat("히어로", stats), "str")  # 전환 대상이 아닌 직업은 영향 없음

        basic = {"character_class": "바이퍼", "character_level": 285}
        report = build_report("x", basic, {"item_equipment": [self.challenger_top]}, stats)
        self.assertEqual(report["results"][0]["scoring_basis"]["main_stat"], "DEX")

    def test_character_profile_sets_tier_and_conversion(self):
        stats = {
            "final_stat": [
                {"stat_name": "INT", "stat_value": "20000"},
                {"stat_name": "LUK", "stat_value": "4000"},
                {"stat_name": "마력", "stat_value": "1200"},
                {"stat_name": "크리티컬 데미지", "stat_value": "85.00"},
                {"stat_name": "전투력", "stat_value": "50000000"},
            ]
        }
        item = dict(self.challenger_top)
        item["potential_option_3"] = "올스탯 +9%"  # INT 9% + 6% + 올스탯 9% + 에디 INT 4% = 28%

        profile = build_character_profile("레테", "int", [item], stats)

        self.assertEqual((profile["tier"], profile["add_target_multiplier"]), ("중자본", 0.87))
        self.assertEqual(profile["stat_pct_total"], 28)
        # b = 1.48 → 고정 스탯 1 = 100 × 1.48² ÷ 20000, 마력 1 = 25 × 1.48 × (80000 + 4000) ÷ (20000 × 1200)
        self.assertAlmostEqual(profile["conversion"].stat_flat, 3.0 * 0.01095, places=3)  # × stat_flat_scale
        self.assertAlmostEqual(profile["conversion"].attack_flat, 2.0 * 0.1295, places=3)  # × attack_flat_scale
        self.assertAlmostEqual(profile["conversion"].crit_damage, 1.48 / 2.20, places=3)  # b ÷ (1.35 + 0.85)
        self.assertIsNone(build_character_profile("제논", "all_stat", [item], stats)["conversion"])

    def test_crit_rate_lines_follow_current_crit_rate_and_archer_bonus(self):
        def stats(crit_rate):
            return {
                "final_stat": [
                    {"stat_name": "DEX", "stat_value": "20000"},
                    {"stat_name": "STR", "stat_value": "4000"},
                    {"stat_name": "공격력", "stat_value": "1200"},
                    {"stat_name": "크리티컬 데미지", "stat_value": "85.00"},
                    {"stat_name": "크리티컬 확률", "stat_value": crit_rate},
                    {"stat_name": "전투력", "stat_value": "50000000"},
                ]
            }

        item = dict(self.challenger_top)
        item["potential_option_1"] = "크리티컬 확률 +9%"
        item["potential_option_2"] = None

        def points(class_name, crit_rate):
            profile = build_character_profile(class_name, "dex", [item], stats(crit_rate))
            analysis = analyze_potential_score(item, "potential", class_name, 285, main_stat="dex", conversion=profile["conversion"])
            return next(entry["value"] for entry in analysis["recognized"] if "크리티컬 확률" in entry["option"]), profile

        capped, _ = points("캡틴", "95.00")  # 90% 이상이면 0 이지만 미지원으로 남지 않음
        self.assertEqual(capped, 0)
        below, profile = points("캡틴", "80.00")  # 100% 까지 20% 여유 → 9% 전부 유효
        self.assertAlmostEqual(below, round(9 * profile["conversion"].crit_rate_value, 2), places=2)
        archer, profile = points("보우마스터", "100.00")  # 궁수는 초과분 9% 가 크리티컬 리인포스 가치
        self.assertAlmostEqual(archer, round(9 * 0.5 * 0.25 * profile["conversion"].crit_damage, 2), places=2)
        self.assertEqual(points("캡틴", "100.00")[1]["conversion"].crit_excess_value, 0.0)

    def test_level_stat_line_scales_with_character_level(self):
        item = dict(self.challenger_top)
        item["potential_option_2"] = "캐릭터 기준 9레벨 당 INT +2"

        by_level = calculate_potential_score(item, "potential", "레테", 285)  # INT 9% + 2 × 31 × 0.09
        unknown_level = calculate_potential_score(item, "potential", "레테", 0)  # 레벨을 모르면 기본값 3.5

        self.assertAlmostEqual(by_level, 9 + 62 * 0.09, places=2)
        self.assertAlmostEqual(unknown_level, 9 + 2 * 3.5, places=2)

    def test_guide_rules_match_in_order_and_fill_fields(self):
        rules = [
            {"name_any": "hearts", "when": [["total", ">=", 100]], "text": "하트 {star_next}성"},
            {"when": [["star", "<", "max_star"], ["limited", "==", False]], "text": "강화 '{worst}'"},
            {"text": "기본"},
        ]
        names = {"hearts": ["리튬 하트"]}
        context = {"total": 120, "star": 17, "star_next": 18, "max_star": 22, "limited": False, "worst": "윗잠재"}

        self.assertEqual(select_guide(rules, context, names, "리튬 하트"), "하트 18성")
        self.assertEqual(select_guide(rules, context, names, "앱솔랩스 슈즈"), "강화 '윗잠재'")
        self.assertEqual(select_guide(rules, {**context, "star": 22}, names, "앱솔랩스 슈즈"), "기본")

    def test_split_guide_separates_tag_and_message(self):
        self.assertEqual(
            split_guide("👑 [종결] 완벽한 장비입니다. 23성 도전 외엔 투자가 무의미합니다."),
            ("종결", "👑 완벽한 장비입니다. 23성 도전 외엔 투자가 무의미합니다."),
        )
        self.assertEqual(
            split_guide("✅ 해당 부위는 표준 성능을 보여주고 있습니다."),
            ("INFO", "✅ 해당 부위는 표준 성능을 보여주고 있습니다."),
        )
        self.assertEqual(split_guide(""), ("INFO", ""))

    def test_evaluated_items_expose_guide_tag_and_message(self):
        result = evaluate_equipment([self.challenger_top], "레테", 285)[0]

        self.assertEqual(result["guide_tag"], "교체 시급")
        self.assertNotIn("[", result["guide_message"])
        self.assertIn(f"[{result['guide_tag']}]", result["guide"])

    def test_black_heart_guide_has_a_tag(self):
        black_heart = dict(self.challenger_top)
        black_heart.update({
            "item_equipment_part": "기계 심장",
            "item_equipment_slot": "기계 심장",
            "item_name": "블랙 하트",
        })

        result = evaluate_equipment([black_heart], "레테", 285)[0]

        self.assertEqual(result["guide_tag"], "미지원")
        self.assertIn("블랙 하트", result["guide_message"])


if __name__ == "__main__":
    unittest.main()
