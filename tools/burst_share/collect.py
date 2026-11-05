"""직업별 랭커의 연무장 리플레이를 모읍니다 (analyze.py 의 입력). 캐릭터 이름은 저장·출력하지 않고 '직업-순번' 라벨만 씁니다.

사용: python tools/burst_share/collect.py <출력 폴더> <기간 번호,…> [--per-class N] [--max-scan M] [--classes 렌,히어로]
  --per-class  직업마다 모을 기록 수 (기본 8). 채우면 그 직업의 조회를 멈춥니다.
  --max-scan   직업마다 최대로 확인할 랭커 수 (기본 400). 200명 = 랭킹 한 페이지.
API 키는 .env 의 NEXON_API_KEY 를 쓰며 출력하지 않습니다. 호출 사이 0.1초 쉬고 429 는 기다렸다 다시 시도합니다.
랭커 1명당 2회(ocid · 리플레이 목록), 찾은 기록 1개당 3회(결과 · 입장 정보 · 타임라인) 호출합니다.
"""

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

CLASSES = [
    "히어로", "팔라딘", "다크나이트", "아크메이지(불,독)", "아크메이지(썬,콜)", "비숍", "보우마스터", "신궁", "패스파인더",
    "나이트로드", "섀도어", "듀얼블레이더", "바이퍼", "캡틴", "캐논마스터", "소울마스터", "플레임위자드", "윈드브레이커",
    "나이트워커", "스트라이커", "미하일", "아란", "에반", "루미너스", "메르세데스", "팬텀", "은월", "블래스터", "배틀메이지",
    "와일드헌터", "메카닉", "제논", "데몬슬레이어", "데몬어벤져", "카이저", "카인", "카데나", "엔젤릭버스터", "아델", "일리움",
    "칼리", "아크", "라라", "호영", "제로", "키네시스", "렌", "레테",
]


def strip_names(node):
    """저장 전 캐릭터·길드 이름 필드를 지웁니다."""
    if isinstance(node, dict):
        return {k: ("" if k in ("character_name", "guild_name", "character_guild_name") else strip_names(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [strip_names(v) for v in node]
    return node


def option(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    periods = {int(p) for p in sys.argv[2].split(",")}
    per_class, max_scan = int(option("--per-class", "8")), int(option("--max-scan", "400"))
    classes = option("--classes", ",".join(CLASSES)).split(",")
    client = httpx.Client(base_url="https://open.api.nexon.com/maplestory/v1",
                          headers={"x-nxopen-api-key": os.environ["NEXON_API_KEY"]}, timeout=20)
    date = (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date() - dt.timedelta(days=1)).isoformat()
    calls = 0

    def get(path, **params):
        nonlocal calls
        for attempt in range(4):
            time.sleep(0.1)
            calls += 1
            response = client.get(path, params=params)
            if response.status_code == 429:
                time.sleep(2 + attempt * 3)
                continue
            return response.json() if response.status_code == 200 else None
        return None

    for class_name in classes:
        saved = len(list(out.glob(f"{class_name}-*.json")))
        scanned, page = 0, 1
        while saved < per_class and scanned < max_scan:
            rows = (get("/ranking/overall", date=date, page=page, **{"class": f"{class_name}-전체 전직"}) or {}).get("ranking") or []
            if not rows:
                break
            for row in rows:
                if saved >= per_class or scanned >= max_scan:
                    break
                scanned += 1
                ocid = (get("/id", character_name=row["character_name"]) or {}).get("ocid")
                replays = [r for r in ((get("/battle-practice/replay-id", ocid=ocid) or {}).get("replay_list") or [])
                           if r.get("period_no") in periods] if ocid else []
                if not replays:
                    continue
                latest = max(replays, key=lambda r: (r.get("period_no") or 0, r.get("register_date") or ""))
                rid = latest["replay_id"]
                result = get("/battle-practice/result", replay_id=rid)
                info = get("/battle-practice/character-info", replay_id=rid)
                timeline = get("/battle-practice/skill-timeline", replay_id=rid)
                if not (result and info and timeline):
                    continue
                if (timeline.get("total_page_no") or 1) > 1:  # 여러 페이지면 이어 붙입니다
                    for extra in range(2, timeline["total_page_no"] + 1):
                        more = get("/battle-practice/skill-timeline", replay_id=rid, page_no=extra) or {}
                        timeline["skill_timeline"] += more.get("skill_timeline") or []
                record = strip_names({"class": class_name, "label": f"{class_name}-{scanned:03d}", "replay": latest,
                                      "result": result, "info": info, "timeline": timeline})
                (out / f"{class_name}-{scanned:03d}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                saved += 1
            page += 1
        print(f"{class_name}: 기록 {saved}개 (랭커 {scanned}명 확인, 누적 호출 {calls}회)", flush=True)


if __name__ == "__main__":
    main()
