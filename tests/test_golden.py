"""실제 Nexon 응답 픽스처로 진단 결과 전체를 고정(golden)하는 회귀 테스트.

- 픽스처: tests/fixtures/characters/*.json (scripts/fetch_fixtures.py 로 수집, 식별 정보 제거됨)
- 스냅샷: tests/fixtures/golden/*.json (같은 이름)

점수 공식이나 가이드 문구를 의도적으로 바꿨다면 스냅샷을 갱신합니다:
    UPDATE_GOLDEN=1 python -m unittest tests.test_golden
"""

import json
import logging
import os
import unittest
from pathlib import Path

from app.analyzer import build_report

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "characters"
GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
UPDATE_GOLDEN = os.getenv("UPDATE_GOLDEN") == "1"

# 스냅샷에 고정하는 아이템 필드. raw_options 같은 원본 응답은 픽스처에 이미 있으므로 제외합니다.
ITEM_KEYS = (
    "name",
    "part",
    "slot",
    "star",
    "level",
    "is_wse",
    "is_special",
    "is_noljang",
    "total_score",
    "detail",
    "guide_tag",
    "guide_message",
    "guide",
)


def fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def report_from_fixture(fixture: dict) -> dict:
    basic = fixture["basic"]
    return build_report(basic["character_name"], basic, fixture["item_equipment"], fixture["stat"])


def snapshot_from_report(report: dict) -> dict:
    snapshot = {
        "class": report["class"],
        "level": report["level"],
        "combat_power": report["combat_power"],
        "best_preset": report["best_preset"],
        "overall": report["overall"],
        "items": [
            {
                **{key: item[key] for key in ITEM_KEYS},
                "breakdown": item["scoring_basis"]["breakdown"],
            }
            for item in report["results"]
        ],
    }
    # JSON 왕복으로 타입을 정규화해 파일에서 읽은 스냅샷과 같은 모양으로 비교합니다.
    return json.loads(json.dumps(snapshot, ensure_ascii=False))


class GoldenReportTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        # 실제 장비에는 점수에 반영하지 않는 옵션이 많아 경고 로그가 수백 줄 찍히므로 테스트 중에는 잠시 올립니다.
        cls._analyzer_logger = logging.getLogger("app.analyzer")
        cls._previous_level = cls._analyzer_logger.level
        cls._analyzer_logger.setLevel(logging.ERROR)

    @classmethod
    def tearDownClass(cls):
        cls._analyzer_logger.setLevel(cls._previous_level)

    def test_fixtures_exist(self):
        self.assertTrue(
            fixture_paths(),
            f"픽스처가 없습니다: {FIXTURE_DIR}. scripts/fetch_fixtures.py 로 생성하세요.",
        )

    def test_reports_match_golden_snapshots(self):
        for fixture_path in fixture_paths():
            with self.subTest(fixture=fixture_path.stem):
                actual = snapshot_from_report(report_from_fixture(load_json(fixture_path)))
                golden_path = GOLDEN_DIR / fixture_path.name

                if UPDATE_GOLDEN:
                    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
                    golden_path.write_text(
                        json.dumps(actual, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    continue

                self.assertTrue(
                    golden_path.exists(),
                    f"스냅샷이 없습니다: {golden_path}. UPDATE_GOLDEN=1 로 생성하세요.",
                )
                self.assertEqual(
                    actual,
                    load_json(golden_path),
                    f"{fixture_path.stem} 결과가 골든 스냅샷과 다릅니다. 의도한 변경이면 UPDATE_GOLDEN=1 로 갱신하세요.",
                )

    def test_every_evaluated_item_has_consistent_shape(self):
        for fixture_path in fixture_paths():
            report = report_from_fixture(load_json(fixture_path))
            self.assertIn(report["best_preset"], (1, 2, 3))
            self.assertIn(report["overall"]["rank"], {"ETERNAL", "DESTINY", "ASTRA", "GENESIS", "EPIC"})
            self.assertTrue(report["results"], f"{fixture_path.stem}: 평가된 장비가 없습니다.")

            for item in report["results"]:
                with self.subTest(fixture=fixture_path.stem, item=item["name"]):
                    self.assertTrue(item["guide_tag"])
                    self.assertNotIn("[", item["guide_message"])
                    self.assertGreaterEqual(item["total_score"], 0)
                    self.assertEqual(
                        {entry["key"] for entry in item["scoring_basis"]["breakdown"]},
                        {"add", "pot", "pot_additional", "star"},
                    )
                    if not item["is_special"]:
                        weighted_total = sum(entry["weighted_score"] for entry in item["scoring_basis"]["breakdown"])
                        self.assertAlmostEqual(weighted_total, item["total_score"], delta=0.05)


if __name__ == "__main__":
    unittest.main()
