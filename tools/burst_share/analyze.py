"""연무장 리플레이로 리스트레인트 링 구간(15초·20초)의 딜 비중을 실측합니다 (스펙업 추천의 시드링 가치에 사용).

사용: python tools/burst_share/analyze.py <리플레이 JSON 폴더> [--toml]
  리플레이 JSON = collect.py 가 저장한 {class, label, replay, result, info, timeline} (캐릭터 이름 없음)

방법
- 리레 구간: 타임라인에서 '리스트레인트 링'을 쓴 시각부터 15초·20초. 실제 사용 간격(쿨감 모자·유니온 쿨감 등)이 그대로 반영됩니다.
- 타임라인 스킬(버프·쿨타임 스킬): 스킬 총딜을 타임라인 사용 횟수로 똑같이 나눠 사용 시각에 둡니다.
- 타임라인에 없는 스킬(연타·자동 발동): 시각을 알 수 없어 두 경우로 범위를 잡습니다.
    하한 = 시간에 고르게 들어감, 상한 = 타임라인 스킬과 같은 비율로 리레 구간에 몰림.
  추정값은 상한입니다. 극딜 중에는 연타도 극딜 버프를 받고, 아래 한계 때문에 구간 딜이 낮게 잡히기 때문입니다.
- 한계: 사용 1회당 딜을 똑같다고 봐서 극딜 버프로 커진 구간 딜이 빠집니다(실제 비중은 더 높을 수 있음).
- 자동 종료(end_type 1)이고 리레를 2번 이상 쓴 기록만 씁니다.
"""

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

WINDOWS = (15, 20)


def ring_level(info: dict) -> int | None:
    def walk(node):
        if isinstance(node, dict):
            if "리스트레인트" in (node.get("item_name") or ""):
                yield node.get("special_ring_level")
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)
    return next((level for level in walk(info.get("item_object")) if level), None)


def analyze(record: dict) -> dict | None:
    result = record.get("result") or {}
    timeline = ((record.get("timeline") or {}).get("skill_timeline")) or []
    if str(result.get("end_type")) != "1" or not timeline:
        return None
    total, play = result["total_damage"], result["total_play_time"] / 1000
    starts = [row["elapse_time"] / 1000 for row in timeline if "리스트레인트" in row["skill_name"]]
    if len(starts) < 2 or total <= 0:
        return None
    stats = {row["skill_name"]: row["damage"] for row in result.get("skill_statistic") or []}
    casts = Counter(row["skill_name"] for row in timeline if row["skill_name"] in stats)
    timed_total = sum(stats[name] for name in casts)
    untimed_total = total - timed_total
    out = {"class": record.get("class"), "label": record.get("label"), "period": (record.get("replay") or {}).get("period_no"),
           "ring_level": ring_level(record.get("info") or {}), "interval": round((starts[-1] - starts[0]) / (len(starts) - 1), 1),
           "timed": round(timed_total / total, 3)}
    for length in WINDOWS:
        timed_in = sum(stats[row["skill_name"]] / casts[row["skill_name"]] for row in timeline
                       if row["skill_name"] in casts and any(s <= row["elapse_time"] / 1000 < s + length for s in starts))
        window_time = sum(min(s + length, play) - s for s in starts)
        low = (timed_in + untimed_total * window_time / play) / total
        high = (timed_in + untimed_total * (timed_in / timed_total if timed_total else 0)) / total
        out[length] = {"low": round(low, 4), "high": round(high, 4), "mid": round((low + high) / 2, 4)}
    return out


def main() -> None:
    folder = Path(sys.argv[1])
    rows = [row for path in sorted(folder.glob("*.json")) if (row := analyze(json.loads(path.read_text(encoding="utf-8"))))]
    by_class = defaultdict(list)
    for row in rows:
        by_class[row["class"]].append(row)
    print(f"분석한 기록 {len(rows)}개 · 직업 {len(by_class)}개")
    summary = {}
    for name, items in sorted(by_class.items()):
        mid15 = statistics.median(row[15]["high"] for row in items)  # 추정값 = 상한
        mid20 = statistics.median(row[20]["high"] for row in items)
        summary[name] = (mid15, mid20, len(items))
        print(f"  {name}: 기록 {len(items)}개 · 15초 {mid15 * 100:.2f}% · 20초 {mid20 * 100:.2f}% "
              f"(20초 범위 {min(r[20]['low'] for r in items) * 100:.1f}~{max(r[20]['high'] for r in items) * 100:.1f}%, "
              f"리레 간격 중앙값 {statistics.median(r['interval'] for r in items):.0f}초)")
    if "--toml" in sys.argv:
        print("\n# spec_up.toml [seed_ring.burst_share] (실측 상한의 중앙값, 기록 수)")
        for name, (mid15, mid20, count) in summary.items():
            print(f'"{name}" = [{mid15 * 100:.2f}, {mid20 * 100:.2f}]  # {count}개')


if __name__ == "__main__":
    main()
