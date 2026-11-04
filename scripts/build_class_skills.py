"""직업별 스킬 크리티컬 데미지 표(app/data/class_skills.toml)를 넥슨 API 스킬 응답에서 만듭니다.

전체 랭킹을 훑어 직업마다 상위 캐릭터 1명(레벨 285 이상)의 스킬 효과 문구를 읽고, 크리티컬 데미지를 세 갈래로 나눕니다.
- passive: 지속시간이 없는 효과와 "[패시브 효과 : …]" 안의 효과 (항상 적용)
- boss_buff: 120초 이상 지속되는 버프 (보스 세팅에서 켜 두는 버프, 예: 샤프 아이즈·소울 게이즈)
- 제외: 60초 이하의 극딜 버프, 조건부 효과(공격 시·착용 시·중첩·알파의 …), 공용 스킬(쓸만한 샤프 아이즈·고급 무기 제련)

패치로 스킬 수치가 바뀌면 다시 실행합니다. 캐릭터 이름은 출력하거나 저장하지 않습니다.
사용:
    NEXON_API_KEY=... python scripts/build_class_skills.py            # API 에서 가져와 표를 씁니다
    python scripts/build_class_skills.py --cache <dir>               # <dir>/<직업>.json 응답 캐시를 쓰고, 없는 직업만 가져옵니다
생성된 표는 커밋 전에 사람이 검토해야 합니다.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "data" / "class_skills.toml"
CLASSES = (
    "히어로", "팔라딘", "다크나이트", "아크메이지(불,독)", "아크메이지(썬,콜)", "비숍", "보우마스터", "신궁", "패스파인더",
    "나이트로드", "섀도어", "듀얼블레이더", "바이퍼", "캡틴", "캐논마스터", "소울마스터", "플레임위자드", "윈드브레이커",
    "나이트워커", "스트라이커", "미하일", "아란", "에반", "루미너스", "메르세데스", "팬텀", "은월", "블래스터", "배틀메이지",
    "와일드헌터", "메카닉", "데몬슬레이어", "데몬어벤져", "제논", "카이저", "카인", "카데나", "엔젤릭버스터", "아델", "일리움",
    "칼리", "아크", "라라", "호영", "렌", "제로", "키네시스", "레테",
)
GRADES = ("1", "1.5", "2", "2.5", "3", "4", "hyperpassive", "hyperactive", "5", "6")  # 0차(소모품·공용)는 보지 않습니다
COMMON_SKILLS = {"쓸만한 샤프 아이즈", "고급 무기 제련"}  # 모든 직업 공용: crit_damage.py 가 따로 더하거나 뺍니다
CONDITIONAL = ("공격 시", "공격하면", "상태의 적", "대상은", "있을 경우", "있을 때", "착용 시", "중첩", "알파의", "적이 받는", "확률의", "지속시간 동안")
# 조건 단어가 들어 있지만 스탯 이름인 것. 조건 판정 전에 지웁니다.
STAT_NAMES = ("보스 몬스터 공격 시 데미지", "일반 몬스터 공격 시 데미지")
MIN_BUFF_SECONDS = 120
CRIT = re.compile(r"크리티컬 데미지\s*(\d+(?:\.\d+)?)\s*%")
DURATION = re.compile(r"(\d+)\s*(초|분)\s*동안")
PASSIVE_BLOCK = re.compile(r"\[패시브 효과\s*:\s*([^\]]*)\]")
# 규칙으로 잡히지 않는 효과. 수치는 검토한 스킬 설명 기준입니다.
MANUAL = {"팔라딘": ("passive", 5.0, "팔라딘 엑스퍼트 무기별 추가 크뎀 (한손검 6 · 그 외 5)")}


def classify(effect: str) -> tuple[float, float, list[str]]:
    """스킬 효과 문구 → (passive, boss_buff, 근거)."""
    passive, buff, notes = 0.0, 0.0, []
    blocks = PASSIVE_BLOCK.findall(effect)
    if blocks:
        for block in blocks:
            value = sum(float(v) for v in CRIT.findall(block))
            passive += value
        return passive, buff, notes
    for line in effect.split("\n"):
        for name in STAT_NAMES:
            line = line.replace(name, "스탯")
        duration = DURATION.search(line)
        seconds = int(duration[1]) * (60 if duration[2] == "분" else 1) if duration else None
        for match in CRIT.finditer(line):
            if any(word in line[: match.start()] for word in CONDITIONAL):
                continue  # 크뎀 앞에 조건이 붙은 효과
            if seconds is None:
                passive += float(match[1])
            elif seconds >= MIN_BUFF_SECONDS:
                buff += float(match[1])
    return passive, buff, notes


def summarize(skills_by_grade: dict) -> dict:
    passive, buff, sources, sharp_eyes = 0.0, 0.0, [], False
    for grade in GRADES:
        for skill in skills_by_grade.get(grade, []):
            name, effect = skill.get("skill_name", ""), skill.get("skill_effect") or ""
            if name in COMMON_SKILLS or "크리티컬 데미지" not in effect:
                continue
            p, b, _ = classify(effect)
            if p:
                passive += p
                sources.append(f"{name} {p:g}")
            if b:
                buff += b
                sources.append(f"{name} {b:g} (버프)")
                sharp_eyes = sharp_eyes or name == "샤프 아이즈"
    return {"passive": passive, "boss_buff": buff, "sharp_eyes": sharp_eyes, "sources": sources}


def fetch(cache: Path | None) -> dict:
    """직업 → 스킬 응답(grade → 목록). 캐시에 있으면 쓰고 없는 직업만 API 로 가져옵니다."""
    result, need = {}, []
    for cls in CLASSES:
        path = cache / f"{cls}.json" if cache else None
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("class") == cls:
                result[cls] = data["skills"]
                continue
        need.append(cls)
    if not need:
        return result
    import httpx

    client = httpx.Client(base_url="https://open.api.nexon.com/maplestory/v1",
                          headers={"x-nxopen-api-key": os.environ["NEXON_API_KEY"]}, timeout=20)

    def get(path, **params):
        time.sleep(0.12)
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    date = (dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date() - dt.timedelta(days=1)).isoformat()
    names = {}
    for page in range(1, 61):
        for row in get("/ranking/overall", date=date, page=page).get("ranking", []):
            cls = row.get("sub_class_name") or row.get("class_name")
            if cls in need and cls not in names and (row.get("character_level") or 0) >= 285:
                names[cls] = row["character_name"]
        if len(names) == len(need):
            break
    for cls, name in names.items():
        ocid = get("/id", character_name=name)["ocid"]
        skills = {}
        for grade in GRADES:
            try:
                skills[grade] = get("/character/skill", ocid=ocid, character_skill_grade=grade).get("character_skill", [])
            except httpx.HTTPError:
                skills[grade] = []
        result[cls] = skills
        if cache:
            cache.mkdir(parents=True, exist_ok=True)
            (cache / f"{cls}.json").write_text(json.dumps({"class": cls, "skills": skills}, ensure_ascii=False), encoding="utf-8")
    missing = sorted(set(need) - set(names))
    if missing:
        print("랭킹에서 찾지 못한 직업:", ", ".join(missing), file=sys.stderr)
    return result


def render(table: dict) -> str:
    today = dt.date.today().isoformat()
    lines = [
        f"# 직업별 스킬 크리티컬 데미지. scripts/build_class_skills.py 가 넥슨 API 스킬 응답에서 생성했습니다 ({today}). 손으로 고치지 말고 다시 생성하세요.",
        "# passive: 항상 적용되는 스킬 크뎀 / boss_buff: 120초 이상 지속 버프 크뎀 (보스 세팅 기준으로 켜 둔다고 봅니다)",
        "# sharp_eyes: 직업 샤프 아이즈가 있으면 공용 쓸만한 샤프 아이즈와 중복되지 않습니다",
        "",
        "[common]",
        "useful_sharp_eyes = 8.0   # 공용 5차 쓸만한 샤프 아이즈 크뎀 (보스 세팅 버프)",
        "",
        "[crit_damage]",
    ]
    for cls in CLASSES:
        if cls not in table:
            continue
        row = table[cls]
        sources = ", ".join(row["sources"]) or "없음"
        lines.append(f'"{cls}" = {{ passive = {row["passive"]:.1f}, boss_buff = {row["boss_buff"]:.1f}, sharp_eyes = {str(row["sharp_eyes"]).lower()} }}   # {sources}')
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path, help="직업별 스킬 응답 캐시 디렉터리")
    args = parser.parse_args()
    table = {cls: summarize(skills) for cls, skills in fetch(args.cache).items()}
    for cls, (kind, value, note) in MANUAL.items():
        if cls in table:
            table[cls][kind] += value
            table[cls]["sources"].append(f"{note} {value:g}")
    OUTPUT.write_text(render(table), encoding="utf-8")
    print(f"{OUTPUT.relative_to(ROOT)}: {len(table)}개 직업")


if __name__ == "__main__":
    main()
