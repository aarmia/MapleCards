import math
import unittest
from collections import Counter
from dataclasses import replace

from app.analyzer import (
    _preset_main_flat_delta,
    build_report,
    evaluate_equipment,
    unsupported_registry,
)
from app.calculator import (
    add_option_entries,
    calculate_item_score,
    calculate_potential_score,
    get_main_stat,
    get_potential_options,
    starforce_benchmark_option,
    starforce_option,
)
from app.character_profile import (
    _gear_attack_pct,
    _gear_attack_pct_sources,
    _hexa_main_stat,
    build_character_profile,
    build_stat_context,
)
from app.potential import convert_line, reference_context
from app.scoring_config import CLASSES, POTENTIAL


class PotentialOptionTests(unittest.TestCase):
    def test_preset_flat_delta_handles_missing_and_special_stats(self):
        self.assertEqual(_preset_main_flat_delta([{}], [{"item_total_option": {"luk": "100"}}], "luk"), 0)
        self.assertEqual(_preset_main_flat_delta([], [{"item_total_option": {"luk": "100"}}], "luk"), 0)
        self.assertEqual(_preset_main_flat_delta([{"item_total_option": {"hp": "100"}}],
                                                [{"item_total_option": {"hp": "200"}}], "hp"), 0)
        self.assertEqual(_preset_main_flat_delta([{"item_total_option": {"luk": "200"}}],
                                                [{"item_total_option": {"luk": "100"}}], "luk"), -100)

    def test_preset_flat_main_stat_corrects_level_option_overestimate(self):
        from unittest.mock import patch
        equipped = {"item_total_option": {"luk": "4311"}, "potential_option_1": "LUK +557%"}
        selected = {"item_total_option": {"luk": "5570"}, "potential_option_1": "LUK +838%"}
        stats = {"final_stat": [{"stat_name": "LUK", "stat_value": "77589"},
                                 {"stat_name": "DEX", "stat_value": "7862"},
                                 {"stat_name": "공격력", "stat_value": "9032"}]}
        extras = {"symbol": {"symbol": [{"symbol_luk": "29990"}]}}
        current = build_stat_context("나이트워커", "luk", 298, [equipped], stats, extras)
        old = replace(current, stat_pct=9.38)
        with patch("app.analyzer.evaluate_equipment", return_value=[]), patch("app.analyzer.get_best_preset", return_value=1):
            report = build_report("테스트", {"character_class": "나이트워커", "character_level": 298},
                {"item_equipment": [equipped], "item_equipment_preset_1": [selected]}, stats, extras=extras)
        context = report["profile"]["context"]
        self.assertEqual(context["sources"]["preset_main_flat_delta"], 1259)
        self.assertEqual(context["base_stat"], round(current.base_stat + 1259))
        new = replace(current, base_stat=current.base_stat + 1259, stat_pct=9.38)
        option = "캐릭터 기준 9레벨 당 LUK +2"
        # 66 고정 × 100 × b ÷ (a + 도핑 주스탯 30)
        self.assertAlmostEqual(convert_line(option, new, 298)[0], 66 * 100 * 9.38 / new.base_stat_effective, places=2)
        self.assertLess(convert_line(option, new, 298)[0], convert_line(option, old, 298)[0])
        self.assertEqual(convert_line(option, new, 298)[0], convert_line("LUK +66", new, 298)[0])

    def test_common_formula_runs_for_all_configured_classes(self):
        classes = [name for names in CLASSES.main_stat.raw.values() for name in names]
        classes.extend(CLASSES.special.raw)
        stats = {"final_stat": [{"stat_name": name, "stat_value": str(value)}
                 for name, value in {"STR": 40000, "DEX": 40000, "INT": 40000,
                                     "LUK": 40000, "HP": 500000, "공격력": 5000,
                                     "마력": 5000, "크리티컬 데미지": 100,
                                     "크리티컬 확률": 100}.items()]}
        for char_class in classes:
            with self.subTest(char_class=char_class):
                main = get_main_stat(char_class, stats)
                context = build_stat_context(char_class, main, 290, [], stats)
                attack = "마력" if main == "int" else "공격력"
                for option in (f"{attack} +14", "크리티컬 데미지 +1%", "올스탯 +6%"):
                    value, _ = convert_line(option, context, 290)
                    self.assertTrue(math.isfinite(value))
                    self.assertGreaterEqual(value, 0)
                self.assertTrue(any("공통 수식 기반 추정" in note for note in context.notes()))

    def test_soul_attack_percent_counts_new_and_legacy_options(self):
        items = [{"potential_option_1": "공격력 +185%",
                  "soul_option": "공격력 +3%", "soul_pad": "20",
                  "soul_potential_amplified_grade": 4,
                  "soul_potential_option_1": "공격력 +8%",
                  "soul_potential_option_2": "공격력 +6%",
                  "soul_potential_option_3": "공격력 +6%"}]
        self.assertEqual(_gear_attack_pct(items, "luk"), 208)
        breakdown = _gear_attack_pct_sources(items, "luk")
        self.assertEqual(breakdown["soul_potential"], 20)
        self.assertEqual(breakdown["soul_option"], 3)
        self.assertEqual(_gear_attack_pct(items, "int"), 0)

    def test_soul_magic_and_missing_options(self):
        items = [{"soul_option": "마력 +3%", "soul_mad": "20",
                  "soul_potential_option_1": "마력 +8%",
                  "soul_potential_option_2": None}, {}]
        self.assertEqual(_gear_attack_pct(items, "int"), 11)
        self.assertEqual(_gear_attack_pct(items, "luk"), 0)
        self.assertEqual(_gear_attack_pct(None, "int"), 0)

    def test_hexa_counts_active_cores_only(self):
        data = {
            "character_hexa_stat_core": [{"sub_stat_name_2": "주력 스탯 증가", "sub_stat_level_2": 5}],
            "character_hexa_stat_core_2": [{"sub_stat_name_2": "주력 스탯 증가", "sub_stat_level_2": 3}],
            "character_hexa_stat_core_3": [{"main_stat_name": "주력 스탯 증가", "main_stat_level": 10}],
            "preset_hexa_stat_core": [{"main_stat_name": "주력 스탯 증가", "main_stat_level": 10}],
        }
        self.assertEqual(_hexa_main_stat(data, "luk"), 2800)
        self.assertEqual(_hexa_main_stat({}, "luk"), 0)

    def test_sub_stat_removes_exempt_and_includes_link_percent(self):
        stats = {"final_stat": [{"stat_name": "LUK", "stat_value": "50000"},
                                 {"stat_name": "DEX", "stat_value": "16185"},
                                 {"stat_name": "공격력", "stat_value": "5000"}]}
        extras = {"symbol": {"symbol": []},
                  "hyper_stat": {"hyper_stat_preset_1": [{"stat_level": 3, "stat_increase": "DEX 90 증가"}]},
                  "union_raider": {"union_raider_stat": ["DEX 530 증가"]},
                  "link_skill": {"character_link_skill": [{"skill_effect": "모든 능력치 15% 증가"}]}}
        context = build_stat_context("칼리", "luk", 299,
            [{"potential_option_1": "DEX +176%"}], stats, extras)
        self.assertEqual(context.sources["sub_exempt"], 620)
        self.assertAlmostEqual(context.sources["sub_multiplier"], 2.91)
        self.assertEqual(round(context.sources["sub_base_estimate"]), 5349)

    def test_attack_percent_amplifies_flat_attack_conversion(self):
        context = reference_context("히어로", "str")
        boosted = replace(context, attack_multiplier=1.65)
        # 고정 공격력은 (배율 + 버프 공격력 %) 만큼 커지고, 기준 공격력도 같은 배율로 커진 것으로 봅니다
        expected = (boosted.attack_multiplier_effective * context.attack_effective) / (context.attack_multiplier_effective * boosted.attack_effective)
        self.assertAlmostEqual(convert_line("공격력 +14", boosted, 285)[0], convert_line("공격력 +14", context, 285)[0] * expected)
        self.assertGreater(expected, 1.5)

    def test_flame_all_stat_increases_flat_and_level_stat_efficiency(self):
        stats = {"final_stat": [{"stat_name": "STR", "stat_value": "40000"},
                                 {"stat_name": "공격력", "stat_value": "3000"}]}
        plain = build_stat_context("히어로", "str", 280, [], stats)
        flame = build_stat_context("히어로", "str", 280,
                                   [{"item_add_option": {"all_stat": "6"}}], stats)
        self.assertAlmostEqual(flame.stat_pct - plain.stat_pct, 0.06)
        for option in ("STR +32", "캐릭터 기준 9레벨 당 STR +2"):
            self.assertGreater(convert_line(option, flame, 280)[0], convert_line(option, plain, 280)[0])

    def test_report_uses_selected_preset_for_conversion(self):
        equipped = {"potential_option_1": "INT +6%"}
        selected = {"potential_option_1": "INT +12%"}
        stats = {"final_stat": [
            {"stat_name": "INT", "stat_value": "30000"},
            {"stat_name": "마력", "stat_value": "2000"},
        ]}
        from unittest.mock import patch
        with patch("app.analyzer.evaluate_equipment", return_value=[]):
            result = build_report("test", {"character_class": "비숍", "character_level": 280},
                {"item_equipment": [equipped], "item_equipment_preset_1": [equipped],
                 "item_equipment_preset_2": [selected]}, stats)
        self.assertEqual(result["best_preset"], 2)
        self.assertEqual(result["profile"]["conversion_preset"], 2)
        self.assertEqual(result["profile"]["context"]["sources"]["gear_pct"], 12)
        expected = build_stat_context("비숍", "int", 280, [equipped], stats)
        self.assertEqual(result["profile"]["context"]["base_stat"], round(expected.base_stat))
        self.assertEqual(result["profile"]["context"]["final_main"],
                         round(expected.base_stat * 1.12 + expected.exempt_stat))

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
            "item_starforce_option": {"int": "145", "luk": "145", "magic_power": "106"},  # 200제 22성(만점) 합계 → 스타포스 축 100
            "item_base_option": {"base_equipment_level": 200},
            "item_add_option": {},
            "item_etc_option": {},
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
        # 스탯 응답이 없으면 기준 캐릭터(potential.toml reference)로 환산합니다. 도핑 가정([buff])까지 더해 마력 1 = 0.1186%
        self.assertEqual(calculate_potential_score(self.challenger_top, "potential", "레테", 285), 15.0)
        magic = reference_context("레테", "int").pct_per_attack  # 도핑 가정([buff])에 따라 달라지므로 기준 캐릭터에서 읽습니다
        self.assertEqual(calculate_potential_score(self.challenger_top, "additional_potential", "레테", 285), round(4 + 10 * magic, 2))

    def test_analyzer_keeps_two_line_scores_and_raw_options(self):
        result = evaluate_equipment(
            [self.challenger_top],
            "레테",
            285,
        )[0]

        self.assertEqual(result["detail"]["pot"], 15.0)  # 곱상수 없이 유효 스탯% 합
        magic = reference_context("레테", "int").pct_per_attack
        eddy_raw = round(4 + 10 * magic, 2)
        self.assertEqual(result["detail"]["pot_additional"], round(eddy_raw, 1))
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
            [entry["value"] for entry in result["scoring_basis"]["recognized_options"]],
            [9.0, 6.0, 4.0, round(10 * magic, 2)],
        )
        rows = {entry["key"]: entry for entry in result["scoring_basis"]["breakdown"]}
        # 점수 = 축별 주스탯 %p: 잠재는 유효 스탯% 그대로, 22성 스타포스는 만점과 같음
        self.assertEqual([rows[key]["value"] for key in ("add", "pot", "pot_additional")], [0, 15.0, round(eddy_raw, 1)])
        self.assertEqual(rows["star"]["value"], rows["star"]["max"])
        self.assertAlmostEqual(result["total_score"], sum(row["value"] for row in rows.values()), delta=0.3)

    def test_scroll_axis_is_against_best_scrolls_for_the_slot_count(self):
        # 주문서 만점 = 횟수(성공 + 남은 + 복구 가능) × 최상 1작(그 외: 올스탯 +6 · 공/마 +6). 기본 옵션은 만점 = 현재 값
        item = {**self.challenger_top, "scroll_upgrade": "6", "scroll_upgradeable_count": "1", "scroll_resilience_count": "1",
                "item_etc_option": {"int": "36", "luk": "36", "magic_power": "36"}}
        row = evaluate_equipment([item], "레테", 285)[0]
        scroll = next(entry for entry in row["scoring_basis"]["breakdown"] if entry["key"] == "scroll")
        self.assertEqual(scroll["raw"], "6/8")
        self.assertAlmostEqual(scroll["value"] / scroll["max"], 6 / 8, delta=0.02)  # 6장을 최상 주문서로 바른 상태


    def test_starforce_option_per_star_table(self):
        # 22성 합계는 만점 표와 같고, 23성부터는 공/마만 오르며, 장갑은 15성까지 공/마 +1 이 7번 붙습니다.
        self.assertEqual(starforce_option(250, 22, "luk", ("DEX",)), starforce_benchmark_option(250, "luk", ("DEX",)))
        self.assertEqual(starforce_option(250, 23, "luk", ("DEX",)), {"luk": 159, "dex": 159, "attack_power": 143})
        self.assertEqual(starforce_option(200, 15, "str", ("DEX",), "장갑"), {"str": 40, "dex": 40, "attack_power": 7})
        self.assertIsNone(starforce_option(130, 21, "str"))  # 130제는 20성까지

    def test_starforce_axis_is_real_stats_against_22_stars(self):
        over = {**self.challenger_top, "starforce": "25", "item_starforce_option": {"int": "145", "luk": "145", "magic_power": "175"}}
        low = {**self.challenger_top, "starforce": "18", "item_starforce_option": {"int": "85", "luk": "85", "magic_power": "30"}}
        def star(item, job="레테"):
            row = evaluate_equipment([item], job, 285)[0]
            return row["detail"].get("star"), row["bar_max"].get("star")

        value, best = star(over)
        self.assertGreater(value, best)  # 23성 이상은 실제 값이 22성 만점을 넘습니다
        value, best = star(low)
        self.assertLess(value, best / 2)  # 18성은 22성 스탯 환산의 절반 미만
        # 데몬어벤져: 주스탯 자리를 최대 HP 로 둔 같은 상승표로 스타포스 축이 들어갑니다
        demon = {**self.challenger_top, "starforce": "22", "item_starforce_option": {"max_hp": "145", "str": "145", "attack_power": "106"}}
        value, best = star(demon, "데몬어벤져")
        self.assertIsNotNone(best)
        self.assertGreater(value, 0)

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
        item["potential_option_2"] = "몬스터 방어율 무시 +30%"          # 엔진이 모르는 옵션
        unsupported_registry.reset()
        collector = Counter()

        with self.assertLogs("app.analyzer", level="WARNING") as captured:
            result = evaluate_equipment([item], "레테", 285, unsupported=collector)[0]

        self.assertEqual(
            result["scoring_basis"]["unrecognized_options"],
            [
                {"category": "윗잠재", "option": "DEX +9%", "reason": "other_stat"},
                {"category": "윗잠재", "option": "몬스터 방어율 무시 +30%", "reason": "unsupported"},
                {"category": "에디셔널", "option": "방어력 +7%", "reason": "ignored"},
            ],
        )
        # 로그와 집계에는 엔진이 모르는 옵션만 올라갑니다.
        self.assertEqual(len(captured.records), 1)
        self.assertEqual(list(collector), [("potential", "몬스터 방어율 무시 +30%")])

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

    def test_stat_context_derives_base_stat_from_extras(self):
        stats = {
            "final_stat": [
                {"stat_name": "INT", "stat_value": "40000"},
                {"stat_name": "LUK", "stat_value": "4000"},
                {"stat_name": "마력", "stat_value": "2000"},
                {"stat_name": "크리티컬 데미지", "stat_value": "85.00"},
                {"stat_name": "크리티컬 확률", "stat_value": "100.00"},
                {"stat_name": "전투력", "stat_value": "50000000"},
            ]
        }
        item = dict(self.challenger_top)
        item["potential_option_3"] = "올스탯 +9%"  # 장비 주스탯% = 9 + 6 + 9 + 에디 4 = 28
        extras = {
            "symbol": {"symbol": [{"symbol_int": "2200", "symbol_str": "0"}, {"symbol_int": "1800"}]},
            "hyper_stat": {
                "use_preset_no": "2",
                "hyper_stat_preset_2": [
                    {"stat_type": "INT", "stat_level": 10, "stat_increase": "지력 300 증가"},
                    {"stat_type": "HP", "stat_level": 3, "stat_increase": "최대 HP 6% 증가"},
                ],
            },
            "union_raider": {"union_raider_stat": ["INT 80 증가", "STR, DEX, LUK 40 증가"], "union_occupied_stat": ["INT 25 증가", "최대 HP 500 증가"]},
            "union_artifact": {"union_artifact_effect": [{"name": "올스탯 150 증가", "level": 10}, {"name": "최대 HP 3750, 최대 MP 3750 증가", "level": 5}]},
            "ability": {"ability_info": [{"ability_value": "INT 20 증가"}]},
            "hexa_stat": {"character_hexa_stat_core": []},
            "link_skill": {"character_link_skill": [{"skill_effect": "모든 능력치 10% 증가"}, {"skill_effect": "HP 15% 증가"}]},
        }

        profile = build_character_profile("레테", "int", 285, [item], stats, extras)
        context = profile["context"]

        self.assertIn("combat_power", profile)
        self.assertEqual((context.basis, context.exempt_stat), ("stat", 4425.0))  # 어빌리티 INT 20 도 미적용  # 아티팩트·어빌리티 고정 스탯은 % 적용
        self.assertAlmostEqual(context.stat_pct, 1.38, places=9)  # b = 1 + (장비 28 + 링크 10) ÷ 100
        self.assertAlmostEqual(context.base_stat, (40000 - 4425) / 1.38, places=6)
        # 효율은 도핑 가정(주스탯·올스탯·공격력·공격력 %)을 더한 전투 중 상태 기준입니다
        buff = POTENTIAL.buff
        a = context.base_stat + buff.main_stat_flat + buff.all_stat_flat + (4 + 5 * 284) * buff.ap_stat_pct / 100  # 도핑 + 메이플 용사류 AP 16%
        self.assertAlmostEqual(context.base_stat_effective, a, places=6)
        stat_attack = 4 * (40000 + (a - context.base_stat) * 1.38) + 4000 + buff.all_stat_flat  # 부스탯에도 올스탯 도핑
        attack = (2000 + buff.attack_flat) * (1 + buff.attack_pct / 100)
        self.assertAlmostEqual(context.pct_per_flat, 100 * 1.38 / a, places=9)
        self.assertAlmostEqual(context.pct_per_attack, 100 * stat_attack * (1 + buff.attack_pct / 100) / (4 * a * attack), places=9)
        self.assertAlmostEqual(context.pct_per_crit_damage, stat_attack / ((1.35 + 0.85 + buff.crit_damage_pct / 100) * 4 * a), places=9)

        estimated = build_stat_context("레테", "int", 285, [item], stats, {"hyper_stat": extras["hyper_stat"]})
        self.assertEqual((estimated.basis, estimated.exempt_stat), ("estimated", 24000.0))  # 심볼이 없으면 레벨로 추정 (potential.toml exempt_estimate)
        self.assertEqual(reference_context("레테", "int").basis, "reference")

    def test_lines_convert_to_effective_stat_pct(self):
        # 기준 캐릭터: a = 12000, b = 3 → 고정 스탯 1 = 0.025%, 공/마 1 = 0.12%, 크뎀 1% = A ÷ ((1.35 + 0.9 + 버프) × w × a')
        reference = reference_context("히어로", "str")  # 공/마·고정 스탯 1 의 가치는 [buff] 도핑 가정에 따라 달라집니다
        attack12, flat1 = round(12 * reference.pct_per_attack, 2), reference.pct_per_flat
        xenon, avenger = reference_context("제논", "all_stat"), reference_context("데몬어벤져", "hp")  # 올스탯 도핑 몫이 달라 효율도 다름
        cases = [
            ("히어로", "str", "STR +12%", 12.0),
            ("히어로", "str", "올스탯 +9%", 9.9),  # 주스탯 1 + 부스탯 1 × 0.1
            ("듀얼블레이더", "luk", "올스탯 +9%", 10.8),  # 부스탯 STR·DEX
            ("히어로", "str", "DEX +9%", 0.9),
            ("히어로", "str", "STR +40", round(40 * flat1, 2)),
            ("히어로", "str", "DEX +40", round(4 * flat1, 2)),
            ("히어로", "str", "공격력 +12", attack12),
            ("히어로", "str", "마력 +12", None),
            ("히어로", "str", "크리티컬 데미지 +8%", round(8 * reference.pct_per_crit_damage, 2)),
            ("히어로", "str", "모든 스킬의 재사용 대기시간 -2초(10초 이하는 10%감소, 5초 미만으로 감소 불가)", 22.0),
            ("제논", "all_stat", "STR +9%", 3.0),
            ("제논", "all_stat", "INT +9%", None),
            ("제논", "all_stat", "올스탯 +9%", 9.0),
            ("제논", "all_stat", "LUK +40", round(40 * xenon.pct_per_flat, 2)),
            ("데몬어벤져", "hp", "최대 HP +12%", 12.0),
            ("데몬어벤져", "hp", "STR +12%", 1.2),
            ("데몬어벤져", "hp", "올스탯 +9%", 0.9),
            ("데몬어벤져", "hp", "공격력 +12", round(12 * avenger.pct_per_attack, 2)),
        ]
        for class_name, main_stat, option, expected in cases:
            with self.subTest(option=f"{class_name} {option}"):
                converted = convert_line(option, reference_context(class_name, main_stat), 285)
                self.assertEqual(None if converted is None else round(converted[0], 2), expected)

    def test_crit_rate_lines_follow_current_crit_rate_and_archer_bonus(self):
        def points(class_name, crit_rate):
            context = replace(reference_context(class_name, "dex"), crit_rate=crit_rate)
            return convert_line("크리티컬 확률 +9%", context, 285)[0], context

        self.assertEqual(points("캡틴", 95.0)[0], 0.0)  # 90% 이상이면 0 이지만 미지원으로 남지 않음
        below, context = points("캡틴", 80.0)  # 100% 까지 20% 여유 → 9% 전부 유효
        self.assertAlmostEqual(below, 9 * context.pct_per_crit_rate, places=9)
        # A × (0.35 + 크뎀 + 버프) ÷ (w × a' × (1 + 크확 × (0.35 + 크뎀 + 버프)))
        a = context.base_stat_effective  # 도핑·메이플 용사류 가정 포함
        bonus = 0.35 + 0.9 + POTENTIAL.buff.crit_damage_pct / 100
        self.assertAlmostEqual(context.pct_per_crit_rate, context.stat_attack * bonus / (4 * a * (1 + 0.8 * bonus)), places=9)
        archer, context = points("보우마스터", 100.0)  # 궁수는 초과분 9% 가 크리티컬 리인포스 가치
        self.assertAlmostEqual(archer, 9 * 0.5 * 0.25 * context.pct_per_crit_damage, places=9)
        self.assertEqual(points("캡틴", 100.0)[0], 0.0)

    def test_level_stat_line_scales_with_character_level(self):
        item = dict(self.challenger_top)
        item["potential_option_2"] = "캐릭터 기준 9레벨 당 INT +2"

        flat1 = reference_context("레테", "int").pct_per_flat
        by_level = calculate_potential_score(item, "potential", "레테", 285)  # INT 9% + 2 × 31 × 고정 1
        unknown_level = calculate_potential_score(item, "potential", "레테", 0)  # 레벨을 모르면 기준 레벨 275

        self.assertAlmostEqual(by_level, 9 + 62 * flat1, places=2)
        self.assertAlmostEqual(unknown_level, 9 + 60 * flat1, places=2)

    def test_line_values_sum_to_add_option_score_for_every_stat_kind(self):
        option = {"str": "40", "dex": "42", "int": "30", "luk": "168", "max_hp": "2100",
                  "attack_power": "3", "magic_power": "4", "all_stat": "6"}
        for char_class, main in (("섀도어", "luk"), ("나이트워커", "luk"), ("비숍", "int"),
                                 ("제논", "all_stat"), ("데몬어벤져", "hp"), ("히어로", "str")):
            entries = add_option_entries(option, char_class, main_stat=main)
            self.assertAlmostEqual(sum(e["value"] for e in entries), calculate_item_score(option, char_class, main_stat=main), places=6, msg=char_class)
        rows = {e["option"]: (e["value"], e["rule"]) for e in add_option_entries(option, "섀도어", main_stat="luk")}
        self.assertEqual(rows["LUK +168"], (168, "주스탯"))
        self.assertEqual(rows["올스탯 +6%"], (60, "올스탯% × 10"))
        self.assertEqual(rows["DEX +42"], (4.2, "부스탯 ÷ 10"))
        self.assertEqual(add_option_entries({}, "섀도어", main_stat="luk"), [])


    def test_add_option_grade_uses_character_efficiency_when_context_given(self):
        context = reference_context("섀도어", "luk")
        option = {"luk": "120", "dex": "40", "all_stat": "6", "attack_power": "10"}
        entries = add_option_entries(option, "섀도어", main_stat="luk", context=context)
        grades = {entry["option"]: entry["value"] for entry in entries}
        self.assertEqual(grades["LUK +120"], 120)  # 주스탯은 1당 1급
        self.assertAlmostEqual(grades["공격력 +10"], round(10 * context.pct_per_attack / context.pct_per_flat, 2))
        self.assertAlmostEqual(sum(grades.values()), calculate_item_score(option, "섀도어", "luk", context), places=1)



class OverallRankTests(unittest.TestCase):
    def rank(self, achievement, weapon_name=None, scrolls=0):
        from app.analyzer import generate_overall_review
        row = {"total_score": achievement, "benchmark_total": 100, "score_ratio": achievement, "part": "모자", "slot": "모자",
               "graded": True, "scored": True, "is_special": False}
        weapon = {"item_name": weapon_name, "scroll_upgrade": str(scrolls)} if weapon_name else None
        return generate_overall_review([row], weapon)[0]["rank"]

    def test_weapon_ranks_and_achievement_cuts(self):
        self.assertEqual(self.rank(98, "데스티니 대거", 9), "ABSOLUTE")          # 97 이상은 무기와 관계없이
        self.assertEqual(self.rank(60, "데스티니 대거", 9), "DESTINY+")          # 데스티니 9작
        self.assertEqual(self.rank(90, "데스티니 대거", 9), "DESTINY+")          # 9작은 ELDER 보다 위
        self.assertEqual(self.rank(84, "데스티니 대거", 8), "ELDER")             # 83 이상이면 DESTINY 보다 ELDER
        self.assertEqual(self.rank(60, "데스티니 대거", 8), "DESTINY")           # 데스티니 (9작 미만)
        self.assertEqual(self.rank(84, "제네시스 대거"), "ELDER")                 # 83 이상
        self.assertEqual(self.rank(78.5, "제네시스 대거"), "DESTINY CHALLENGER")  # 78 초과 + 제네시스
        self.assertEqual(self.rank(78, "제네시스 대거"), "ASTRA")                 # 78 은 넘기지 않음, 65 이상
        self.assertEqual(self.rank(70, "아케인셰이드 대거"), "ASTRA")
        self.assertEqual(self.rank(50, "제네시스 대거"), "GENESIS")               # 45 이상 + 제네시스
        self.assertEqual(self.rank(50, "아케인셰이드 대거"), "GENESIS CHALLENGER")  # 45 이상, 제네시스 아님
        self.assertEqual(self.rank(50), "GENESIS CHALLENGER")
        self.assertEqual(self.rank(44, "제네시스 대거"), "EPIC")



if __name__ == "__main__":
    unittest.main()
