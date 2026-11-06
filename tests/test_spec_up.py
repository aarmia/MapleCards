import re
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.calculator import calculate_item_score, flame_best_add_option
from app.potential import reference_context
from app.scoring_config import POTENTIAL
from app.spec_up import (
    attach_spec_up,
    best_target,
    burst_share,
    expected_resets,
    final_damage_per_pct,
    flame_distribution,
    flame_steps,
    potential_actions,
    potential_distribution,
    seed_ring_action,
)


def str_pct(text):
    match = re.match(r"^(STR|올스탯) \+(\d+)%$", text)
    return float(match.group(2)) if match else 0.0


class SpecUpTests(unittest.TestCase):
    def test_challenger_items_skip_all_upgrade_paths_and_keep_scores(self):
        context = reference_context("나이트로드", "luk")
        for name in ("도전자의 모자", "도전자의 컨티뉴어스 링", "강화된 도전자의 장갑"):
            for source in ("row", "api"):
                with self.subTest(name=name, source=source):
                    row = {"slot": "모자", "name": name if source == "row" else "일반 모자",
                           "total_score": 42, "score_ratio": 80, "spec_up": [{"stale": True}]}
                    item = {"item_equipment_slot": "모자", "item_name": name if source == "api" else "일반 모자"}
                    with patch("app.spec_up.starforce_action") as star, patch("app.spec_up.potential_actions") as potential, \
                            patch("app.spec_up.flame_distribution") as flame, patch("app.spec_up.seed_ring_action") as ring:
                        report = attach_spec_up([row], [item], context, "나이트로드", "luk", 298)
                        for action in (star, potential, flame, ring):
                            action.assert_not_called()
                    self.assertEqual(row["spec_up"], [])
                    self.assertEqual((row["total_score"], row["score_ratio"]), (42, 80))
                    self.assertEqual(report["count"], 0)

    def test_assumptions_follow_config_and_are_in_report(self):
        context = reference_context("나이트로드", "luk")
        with patch.dict(POTENTIAL.raw, {"cooldown_per_second": 7.5}):
            report = attach_spec_up([], [], context, "나이트로드", "luk", 280)
        self.assertTrue(report["estimate"])
        notes = " ".join(report["assumptions"])
        for token in ("주스탯 7.5%", "비실측", "실제 DPS", "평균"):
            self.assertIn(token, notes)

    def test_official_odds_match_mapletiptip_examples(self):
        # mapletiptip.com 200제 모자 예시: 윗잠 STR 21%↑ 2.5584% · 30%↑ 0.1426%, 에디 STR 14%↑ 0.2973% (올스탯% 포함)
        for kind, target, chance in (("potential", 21, 0.025584), ("potential", 30, 0.001426), ("additional", 14, 0.002973)):
            dist = potential_distribution(kind, "모자", 200, None, 0, value_of=str_pct)
            self.assertAlmostEqual(sum(p for v, p in dist.items() if v >= target), chance, places=6)
        # 천장 포함 평균 재설정(올림): 윗잠 6·23·56회, 에디 33·80·112회
        resets = [expected_resets(p, n) for p, n in ((0.15, 10), (0.035, 42), (0.014, 107), (0.02381, 62), (0.009804, 152), (0.007, 214))]
        self.assertEqual([int(-(-r // 1)) for r in resets], [6, 23, 56, 33, 80, 112])

    def test_hat_additional_splits_main_stat_and_cooldown_cases(self):
        context = reference_context("나이트로드", "luk")
        hat = {"additional_potential_option_grade": "레전드리", "additional_potential_option_1": "LUK +9%",
               "additional_potential_option_2": "LUK +7%", "additional_potential_option_3": "최대 HP +9%"}
        actions = potential_actions(hat, "additional", "모자", 250, context, 280, cap=23.0)
        self.assertEqual([a["variant"] for a in actions], ["main", "cooldown"])
        self.assertNotIn("재사용", actions[0]["combo"])  # 주스탯만: 대표 구성에 쿨감 없음
        self.assertIn("재사용 대기시간", actions[1]["combo"])  # 쿨감 1초 + 주스탯
        shown = float(actions[1]["label"].split("환산 ")[1].split("%")[0])
        self.assertLess(shown, 23.0 - 11.0 + 1e-9)  # 표시 목표는 쿨감 1초(11%) 몫을 뺀 주스탯%
        self.assertGreater(actions[1]["chance"], actions[0]["chance"])
        shoes = potential_actions({**hat}, "additional", "신발", 250, context, 280, cap=23.0)
        self.assertEqual([a["variant"] for a in shoes], ["all"])  # 모자 외에는 나누지 않음

    def test_final_damage_matches_stat_attack_ratio(self):
        # 유효 스탯 1% = 최종 주스탯 + a'/100 → 최종 데미지 증가 = 스탯 공격력 증가 비율
        context = reference_context("나이트로드", "luk")
        raised = replace(context, final_main=context.final_main + context.base_stat_effective / 100)
        self.assertAlmostEqual(final_damage_per_pct(context), 100 * (raised.stat_attack / context.stat_attack - 1), places=9)

    def test_seed_ring_levels_value_their_burst_window(self):
        context = reference_context("나이트로드", "luk")
        # 리레 4→5: 늘어난 5초(15→20초 비중 차이)에 공/마 68% / 5→6: 20초 구간 공/마 68→85%
        s15, s20 = burst_share("나이트로드")  # 실측 전 직업은 default
        r45 = seed_ring_action("리스트레인트 링", 4, "나이트로드", context, 0.0, 5)
        base = context.attack_multiplier_effective * 100 + 12  # 컨티 5레벨 공/마 12%
        # 지금 레벨의 전체 딜(1 + 구간 비중 × 공/마 ÷ 배율) 대비 증가
        self.assertAlmostEqual(r45["gain"] * final_damage_per_pct(context), 100 * ((1 + s20 * 68 / base) / (1 + s15 * 68 / base) - 1), places=6)
        self.assertEqual(r45["material"], {"name": "생명의 연마석", "count": 10, "meso": 50e8})  # 비용은 연마석 시세로 화면이 계산
        r56 = seed_ring_action("리스트레인트 링", 5, "나이트로드", context, 0.0, 5)
        self.assertAlmostEqual(r56["gain"] * final_damage_per_pct(context), 100 * ((1 + s20 * 85 / base) / (1 + s20 * 68 / base) - 1), places=6)
        self.assertIsNone(seed_ring_action("리스트레인트 링", 6, "나이트로드", context, 0.0, 5))  # 만렙
        c56 = seed_ring_action("컨티뉴어스 링", 5, "처음 보는 직업", context, 100.0, 5)  # 표에 없는 직업은 평균
        self.assertEqual(c56["material"], {"name": "신념의 연마석", "count": 20, "meso": 200e8})
        self.assertGreater(c56["gain"], 0)

    def test_best_target_trades_gain_against_cost(self):
        dist = {0.0: 0.5, 10.0: 0.4, 30.0: 0.1}
        best = best_target(dist, current=5.0, cost_per_try=1.0)
        # 10↑: 이득 (0.4·10 + 0.1·30)/0.5 − 5 = 9, 비용 2 → 4.5 / 30↑: 이득 25, 비용 10 → 2.5
        self.assertEqual((best["target"], best["gain"], best["cost"]), (10.0, 9.0, 2.0))
        # 등급업 직후 첫 결과도 성공일 수 있음: 추가 재설정 (1 − P) / P
        self.assertEqual(best_target(dist, 5.0, 1.0, setup_cost=3.0, first_try_free=True)["cost"], 4.0)
        # 목표 범위(현재 + 최소 상승폭 ~ 만점): 10 은 최소 상승폭 미달이라 30 만 후보, 만점이 20 이면 후보 없음
        self.assertEqual(best_target(dist, 5.0, 1.0, min_target=11.0)["target"], 30.0)
        self.assertIsNone(best_target(dist, 5.0, 1.0, min_target=11.0, max_target=20.0))

    def test_flame_steps_start_at_plus_7_5_then_every_5(self):
        dist = {100.0: 0.5, 110.0: 0.3, 120.0: 0.15, 130.0: 0.05}
        steps = flame_steps(dist, current=100.0, cost_per_try=1.0)
        self.assertEqual([step["target"] for step in steps], [107.5, 112.5, 117.5, 122.5, 127.5])
        self.assertAlmostEqual(steps[0]["chance"], 0.5)                          # 110 · 120 · 130
        self.assertAlmostEqual(steps[0]["gain"], (110 * .3 + 120 * .15 + 130 * .05) / .5 - 100)
        self.assertAlmostEqual(steps[2]["chance"], 0.2)                          # 117.5 이상 = 120 · 130
        for step in steps:
            self.assertAlmostEqual(step["tries"], 1 / step["chance"])
            self.assertAlmostEqual(step["cost"], 1.0 / step["chance"])

    def test_flame_distribution_tops_out_at_best_flame(self):
        context = reference_context("나이트로드", "luk")
        dist = flame_distribution(250, "나이트로드", "luk", context)
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=9)
        best = calculate_item_score(flame_best_add_option(250, "나이트로드", "luk", context.sub_names, context), "나이트로드", "luk", context)
        self.assertAlmostEqual(max(dist), round(best, 1), places=1)


if __name__ == "__main__":
    unittest.main()
