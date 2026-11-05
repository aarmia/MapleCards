"""collect.mjs 로 모은 공식 확률 표(잠재능력 재설정·에디셔널, 레전드리)를 app/data/potential_odds.json 으로 정리합니다.

사용: python tools/potential_odds/build.py <black.json> <addi.json> <수집일 YYYY-MM-DD>
각 줄의 확률 합이 100% 가 아니면 멈춥니다. 표가 빈 부위(그 레벨 구간에 장비가 없음)는 넣지 않습니다.
"""

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "app" / "data" / "potential_odds.json"
LINES = {"첫 번째 옵션": 0, "두 번째 옵션": 1, "세 번째 옵션": 2}
PART_NAMES = {"기계심장": "기계 심장"}  # 공식 표 → API item_equipment_part


def parse(path: str) -> dict:
    tables: dict = {}
    for row in json.loads(Path(path).read_text(encoding="utf-8")):
        band = re.search(r"장비 레벨\s*([0-9~ ]+)\n", row["text"])
        lines, current = [[], [], []], None
        for text in row["text"].split("\n"):
            head = text.split("\t")[0].strip()
            if head in LINES:
                current = LINES[head]
                continue
            match = re.match(r"(.+)\t([0-9.]+)%$", text.strip())
            if match and current is not None:
                lines[current].append([match.group(1).strip(), float(match.group(2)) / 100])
        if not band or not all(lines):
            continue
        for index, line in enumerate(lines):
            total = sum(p for _, p in line)
            if abs(total - 1) > 1e-4:
                raise SystemExit(f"{row['part']} {row['level']} {index + 1}번째 줄 확률 합 {total:.5f}")
        floor = band.group(1).split("~")[0].strip()
        tables.setdefault(floor, {})[PART_NAMES.get(row["part"], row["part"])] = lines
    return tables


def main() -> None:
    black, addi, date = sys.argv[1:4]
    data = {
        "source": "maplestory.nexon.com/Guide/OtherProbability/cube/black · cube/addi (레전드리 등급)",
        "collected": date,
        "note": "레벨 구간 하한(문자열) → 부위 → [첫·두·세 번째 줄] → [옵션, 확률]. 두·세 번째 줄에는 유니크 옵션이 섞여 있습니다.",
        "potential": parse(black),
        "additional": parse(addi),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(OUT, {kind: {band: len(parts) for band, parts in data[kind].items()} for kind in ("potential", "additional")})


if __name__ == "__main__":
    main()
