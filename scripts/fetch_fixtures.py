"""실제 Nexon Open API 응답을 받아 골든 테스트용 픽스처를 만듭니다.

사용 예:
    python scripts/fetch_fixtures.py                        # 기본 직업 세트 + 중/하위권 표본
    python scripts/fetch_fixtures.py --date 2026-11-01      # 랭킹 기준일(KST) 지정
    python scripts/fetch_fixtures.py --skip-defaults --names 내캐릭 --labels 내캐릭-테스트

- NEXON_API_KEY 는 프로젝트 루트의 .env 또는 환경 변수에서 읽습니다.
- 캐릭터 이름, 길드, 월드, 캐릭터 이미지는 저장하지 않고 라벨로 대체합니다.
- 결과는 tests/fixtures/characters/<라벨>.json 에 저장되며, 스냅샷은
  `UPDATE_GOLDEN=1 python -m unittest tests.test_golden` 으로 갱신합니다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "characters"
BASE_URL = "https://open.api.nexon.com/maplestory/v1"
KST = dt.timezone(dt.timedelta(hours=9))

# (라벨, 랭킹 class 필터). 점수 로직의 주스탯 분기(STR/DEX/INT/LUK, 데몬어벤져 HP, 제논 올스탯)를 하나씩 덮습니다.
DEFAULT_CLASS_TARGETS = [
    ("히어로", "전사-히어로"),
    ("보우마스터", "궁수-보우마스터"),
    ("비숍", "마법사-비숍"),
    ("섀도어", "도적-섀도어"),
    ("데몬어벤져", "레지스탕스-데몬어벤져"),
    ("제논", "레지스탕스-제논"),
]
# 상위권만 모으면 낮은 점수대 가이드 분기가 비므로 종합 랭킹의 깊은 페이지에서 표본을 더 뽑습니다.
DEFAULT_SAMPLE_PAGES = [("중위권", 300), ("하위권", 2000)]
REQUEST_INTERVAL_SEC = 0.25


class NexonClient:
    def __init__(self, api_key: str):
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"x-nxopen-api-key": api_key},
            timeout=20.0,
        )

    def get(self, path: str, **params) -> dict:
        time.sleep(REQUEST_INTERVAL_SEC)
        response = self._client.get(path, params=params)
        if response.status_code != 200:
            raise RuntimeError(f"{path} 실패: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    def ranking_rows(self, date: str, page: int, class_filter: str | None = None, limit: int = 3) -> list[dict]:
        params: dict[str, str | int] = {"date": date, "page": page}
        if class_filter:
            params["class"] = class_filter
        rows = self.get("/ranking/overall", **params).get("ranking", [])
        return rows[:limit]

    def character_bundle(self, name: str) -> tuple[dict, dict, dict]:
        ocid = self.get("/id", character_name=name)["ocid"]
        basic = self.get("/character/basic", ocid=ocid)
        items = self.get("/character/item-equipment", ocid=ocid)
        stat = self.get("/character/stat", ocid=ocid)
        return basic, items, stat


def anonymize_basic(basic: dict, label: str) -> dict:
    """진단에 필요한 직업·레벨만 남기고 식별 정보는 라벨로 대체합니다."""
    return {
        "character_name": f"테스트-{label}",
        "character_class": basic.get("character_class"),
        "character_level": basic.get("character_level"),
        "character_image": "",
    }


def safe_filename(label: str) -> str:
    return re.sub(r"[^\w가-힣-]+", "_", label).strip("_") or "fixture"


def save_fixture(out_dir: Path, label: str, source: str, basic: dict, items: dict, stat: dict, ranking_date: str) -> Path:
    fixture = {
        "label": label,
        "source": source,
        "ranking_date": ranking_date,
        "captured_at": dt.datetime.now(KST).date().isoformat(),
        "basic": anonymize_basic(basic, label),
        "stat": stat,
        "item_equipment": items,
    }
    path = out_dir / f"{safe_filename(label)}.json"
    # 응답 원본을 그대로 담아 파일이 크므로(개당 수백 KB) 들여쓰기 없이 저장합니다. 읽을 때는 python -m json.tool 을 쓰세요.
    path.write_text(
        json.dumps(fixture, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def equipped_count(items: dict) -> int:
    preset = items.get("preset_no") or 1
    equipped = items.get(f"item_equipment_preset_{preset}") or items.get("item_equipment") or []
    return len(equipped)


def kst_yesterday() -> str:
    return (dt.datetime.now(KST).date() - dt.timedelta(days=1)).isoformat()


def build_plan(client: NexonClient, args: argparse.Namespace) -> list[tuple[str | None, list[str], str]]:
    """(라벨, 후보 캐릭터명 목록, 출처) 목록을 만듭니다. 캐릭터명은 저장하지 않고 조회에만 씁니다."""
    plan: list[tuple[str | None, list[str], str]] = []

    if not args.skip_defaults:
        for label, class_filter in DEFAULT_CLASS_TARGETS:
            rows = client.ranking_rows(args.date, 1, class_filter, args.candidates)
            if not rows:
                print(f"[skip] {class_filter}: 랭킹 데이터가 없습니다.")
                continue
            plan.append((label, [row["character_name"] for row in rows], f"ranking:{class_filter}:p1"))

        for label, page in DEFAULT_SAMPLE_PAGES:
            for candidate_page in (page, page // 2, page // 4):
                rows = client.ranking_rows(args.date, candidate_page, None, args.candidates)
                if rows:
                    plan.append((label, [row["character_name"] for row in rows], f"ranking:overall:p{candidate_page}"))
                    break
            else:
                print(f"[skip] {label}: {page}페이지 근처에 랭킹 데이터가 없습니다.")

    for index, name in enumerate(args.names):
        label = args.labels[index] if index < len(args.labels) else None
        plan.append((label, [name], "manual"))

    return plan


def fetch_first_usable(client: NexonClient, label: str | None, names: list[str], min_items: int) -> tuple[dict, dict, dict] | None:
    """후보를 차례로 조회해 착용 장비가 충분한 첫 캐릭터의 응답을 돌려줍니다.

    랭킹 상위권에도 장비를 거의 벗어 둔 캐릭터가 있어, 장비 수가 적으면 다음 후보로 넘어갑니다.
    """
    for name in names:
        try:
            bundle = client.character_bundle(name)
        except (RuntimeError, httpx.HTTPError, KeyError) as exc:
            print(f"[warn] {label}: 후보 조회 실패, 다음 후보로 넘어갑니다. ({exc})")
            continue
        count = equipped_count(bundle[1])
        if count < min_items:
            print(f"[warn] {label}: 착용 장비 {count}개로 기준({min_items}개) 미만, 다음 후보로 넘어갑니다.")
            continue
        return bundle
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", default=kst_yesterday(), help="랭킹 기준일(KST, YYYY-MM-DD). 기본값은 어제")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="픽스처 저장 디렉터리")
    parser.add_argument("--names", nargs="*", default=[], help="랭킹 대신 직접 지정할 캐릭터 닉네임")
    parser.add_argument("--labels", nargs="*", default=[], help="--names 각각에 붙일 라벨 (생략 시 '<직업>-manual')")
    parser.add_argument("--skip-defaults", action="store_true", help="기본 직업 세트와 중/하위권 표본을 건너뜁니다")
    parser.add_argument("--min-items", type=int, default=15, help="착용 장비가 이 수보다 적은 후보는 건너뜁니다 (기본 15)")
    parser.add_argument("--candidates", type=int, default=3, help="랭킹에서 라벨당 시도할 후보 수 (기본 3)")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("NEXON_API_KEY", "")
    if not api_key:
        print("NEXON_API_KEY 가 없습니다. .env 또는 환경 변수에 설정하세요.", file=sys.stderr)
        return 1

    client = NexonClient(api_key)
    args.out.mkdir(parents=True, exist_ok=True)

    failures = 0
    for label, names, source in build_plan(client, args):
        # 직접 지정한 캐릭터는 장비 수와 관계없이 그대로 저장합니다.
        min_items = 0 if source == "manual" else args.min_items
        bundle = fetch_first_usable(client, label or source, names, min_items)
        if bundle is None:
            failures += 1
            print(f"[fail] {label or source}: 사용할 수 있는 후보가 없습니다.")
            continue

        basic, items, stat = bundle
        label = label or f"{basic.get('character_class', 'unknown')}-manual"
        path = save_fixture(args.out, label, source, basic, items, stat, args.date)
        print(
            f"[ok] {label}: {basic.get('character_class')} Lv.{basic.get('character_level')} / "
            f"착용 장비 {equipped_count(items)}개 -> {path.relative_to(ROOT)}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
