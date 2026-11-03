# 🩺 MECULATOR - 장비 딸깍 진단서

[![CI](https://github.com/aarmia/MapleCards/actions/workflows/ci.yml/badge.svg)](https://github.com/aarmia/MapleCards/actions/workflows/ci.yml)

**MECULATOR**는 메이플스토리 유저의 아이템 상태를 정밀 분석하여, 어떤 부위를 우선적으로 강화하거나 교체해야 하는지 정해진 기준을 통해 점수로 환산하여 가이드를 제공하는 **Precision Diagnosis System**입니다.

---

## 🚀 핵심 기능 (Key Features)

### 1. 캐릭터 장비 정밀 스캔
* **닉네임 기반 조회**: 넥슨 오픈 API를 연동하여 캐릭터의 현재 착용 장비 정보를 실시간으로 불러옵니다.
* **최적 프리셋 자동 선택**: 유저가 설정한 장비 프리셋 3개 중 잠재능력·에디셔널 점수 합이 가장 높은 프리셋을 자동으로 골라 분석합니다.

### 2. 다차원 점수 산출 로직 (Scoring System)
단순한 수치 합산이 아닌, 아이템의 레벨과 부위별 특성을 고려한 **상대 점수제**를 채택했습니다.
* **추가옵션(Add)**: 아이템 레벨별(130~250제) 목표 급수를 설정하고, 도달률에 따라 점수를 부여합니다.
* **잠재능력(Pot)**: 윗잠재와 에디셔널을 구분하여, 캐릭터 주스탯 및 공격력/마력 효율을 계산하여 점수화합니다.
* **스타포스(Star)**: 22성 전후의 효율 차이와 아이템 레벨에 따른 가중치를 적용합니다.
* **무기 · 보조무기 · 엠블렘(WSE)**: 공격력/마력%, 보스 데미지, 방어율 무시 중심의 별도 공식으로 평가합니다.
* **특수 장비**: 훈장 · 뱃지 · 포켓 아이템 · 칭호는 아이템 고유 기준으로 평가합니다.

### 3. 지능형 진단 가이드 (Smart Guide)
* **취약 부위 우선 순위**: 전체 장비 중 점수가 낮은 순서대로 'Rank'를 매겨, 가장 시급한 부위 5종을 상단에 노출합니다.
* **맞춤형 조언**: 점수 밸런스를 분석하여 "스타포스 권장", "에디셔널 보완", "완제품 교체 시급" 등 구체적인 행동 지침을 제공합니다. 응답에는 태그(`guide_tag`)와 본문(`guide_message`)이 분리되어 내려갑니다.
* **점수 근거 공개**: 아이템 상세 모달에서 원본 옵션과 함께 인식된 옵션별 환산값·가중치(`scoring_basis`)를 확인할 수 있습니다.
* **특수 등급 애니메이션**: 일정 점수(극 엔드급) 이상을 달성한 종결급 아이템에는 무지개 빛 애니메이션 효과가 적용됩니다.

### 4. 서버 안정성 및 배포 최적화
* **동시 진단 제한과 대기열**: 동시에 처리하는 진단 수를 제한(asyncio Semaphore)하고, 초과 요청은 대기열에서 순서를 기다립니다. 대기열 상한과 대기 타임아웃을 넘으면 503과 Retry-After로 안내하고 화면이 한 번 자동으로 다시 시도합니다. 대기 중 탭을 닫은 요청은 Nexon API를 호출하지 않습니다.
* **진행 상태 조회**: `/request-status/{request_id}`로 대기 순번과 처리 단계(캐릭터 확인 → 장비 조회 → 결과 생성)를 폴링하여 화면에 표시합니다.
* **오류 매핑**: 넥슨 API 오류 코드(OPENAPI000xx)를 본문에서 읽어 없는 캐릭터 / 키 오류 / 점검 / 호출량 초과를 구분하고, `{"error", "code", "retry_after"}` JSON과 HTTP 상태로 변환합니다. 화면은 메시지와 코드별 힌트를 보여줍니다.

---

## 🛠 기술 스택 (Tech Stack)

### Backend
- **Framework**: FastAPI (Python 3.12+)
- **HTTP Client**: httpx (AsyncClient, 연결 풀 공유)
- **Concurrency**: Asyncio (Semaphore control)
- **Template Engine**: Jinja2

### Frontend
- **CSS Framework**: Tailwind CSS (Medical Grid UI)
- **JS Framework**: Alpine.js (Reactive Data Binding)
- **Design Concept**: Clean White & Blue (Medical Report Style)

### API
- **Nexon Open API**: 캐릭터 정보 및 장비 데이터 수집

### Tooling
- **의존성 관리**: uv (`pyproject.toml` + `uv.lock`, 배포용 `requirements.txt`는 lock에서 내보냄)
- **린트**: ruff
- **테스트**: unittest + 실제 API 응답 픽스처 기반 골든 테스트
- **CI**: GitHub Actions (Python 3.12 / 3.13 매트릭스)

---

## 📁 프로젝트 구조 (Structure)

```text
app/
├── main.py                 # FastAPI 서버, 엔드포인트(/, /check-items, /request-status, robots.txt, sitemap.xml)
├── scraper.py              # Nexon Open API 클라이언트, 오류 코드(OPENAPI000xx) 변환
├── errors.py               # 사용자 응답용 예외 AppError (상태 · 메시지 · 코드 · Retry-After)
├── settings.py             # 환경 변수 운영 설정
├── analyzer.py             # 장비 평가, 가이드 문구, 총평, 최적 프리셋 선택, 진단 결과 조립(build_report)
├── calculator.py           # 점수 공식 (추가옵션, 잠재능력, 스타포스, 무기)
├── request_tracker.py      # 동시 진단 제한, 대기열 상한 · 타임아웃, 진행 상태 추적
├── static/
│   ├── images/             # logo.png, favicon.ico
│   └── js/app.js           # Alpine.js 화면 로직
└── templates/
    ├── base.html           # 공통 레이아웃, 스타일, 스크립트 로드
    ├── index.html          # 메인 진단 페이지
    └── components/         # 구성 요소
        ├── _footer.html
        ├── _header.html        # 검색창, 로딩 단계, 오류 메시지
        ├── _item_list.html     # 취약 / 전체 아이템 목록
        ├── _modals.html        # 아이템 상세, 도움말 모달
        ├── _overall_review.html
        ├── _summary.html
        └── _wse_grid.html      # 무기 · 보조무기 · 엠블렘, 특수 장비 카드
scripts/
└── fetch_fixtures.py       # 실제 Nexon 응답을 익명화해 테스트 픽스처로 저장
tests/
├── fixtures/
│   ├── characters/         # 직업별 · 스펙대별 실제 응답 픽스처 (식별 정보 제거)
│   └── golden/             # 픽스처별 진단 결과 스냅샷
├── test_calculator.py      # 점수 공식 · 가이드 분리 단위 테스트
├── test_golden.py          # 픽스처 → 진단 결과가 스냅샷과 같은지 검증
├── test_main.py            # 엔드포인트 · 오류 매핑 · 픽스처 통합 테스트
├── test_request_tracker.py
└── test_scraper.py
.github/workflows/ci.yml    # ruff + unittest + requirements.txt 동기화 검사
pyproject.toml              # 의존성 · ruff 설정
uv.lock                     # 잠금 파일 (모든 플랫폼 공용)
```

---

## ⚙️ 실행 방법

1. **환경 변수 설정**: `.env` 파일을 생성하고 넥슨 오픈 API 키를 등록합니다.
   ```text
   NEXON_API_KEY=your_api_key_here
   # 선택: robots.txt / sitemap.xml에 쓰는 공개 주소 (기본값 https://meculator.onrender.com)
   SITE_URL=https://your-domain.example
   ```
2. **패키지 설치**: 둘 중 하나를 선택합니다.
   ```bash
   uv sync                           # 권장: .venv 에 lock 그대로 설치 (개발 도구 포함)
   pip install -r requirements.txt   # 또는 배포와 같은 방식
   ```
3. **서버 실행**:
   ```bash
   uv run uvicorn app.main:app --reload
   # pip 로 설치했다면: uvicorn app.main:app --reload
   ```

---

## 🛡 운영 설정

워커 프로세스 **1개**를 전제로 합니다. 동시 진단 제한, 대기열, 진행 상태가 모두 프로세스 메모리에 있어 워커가 여러 개면 상태 조회가 어긋나고 제한이 워커 수만큼 늘어납니다. Render Start Command 권장값:
```bash
gunicorn -w 1 -k uvicorn.workers.UvicornWorker app.main:app --timeout 120 --graceful-timeout 30 --max-requests 20000 --max-requests-jitter 2000
```
진행 상태 폴링 1건도 요청 1건으로 세므로 `--max-requests`는 넉넉히 둡니다. 환경 변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `NEXON_API_KEY` | 없음 | Nexon Open API 키 (필수) |
| `SITE_URL` | `https://meculator.onrender.com` | robots.txt / sitemap.xml 에 쓰는 공개 주소 |
| `DIAGNOSIS_CONCURRENCY` | `5` | 동시에 Nexon API를 호출하는 진단 수 |
| `QUEUE_MAX_WAITING` | `40` | 대기열 상한. 넘치면 503 `QUEUE_FULL` |
| `QUEUE_WAIT_TIMEOUT` | `8` | 대기열 최대 대기 초. 넘으면 503 `QUEUE_TIMEOUT` |
| `NEXON_CONNECT_TIMEOUT` / `NEXON_READ_TIMEOUT` | `3` / `6` | Nexon API 연결 · 읽기 타임아웃(초) |

### 오류 응답
사용자 오류는 모두 `{"error": 메시지, "code": 코드, "retry_after": 초}` 형태이며 `retry_after`가 있으면 `Retry-After` 헤더도 함께 갑니다.

| 코드 | 상태 | 뜻 |
|---|---|---|
| `CHARACTER_NOT_FOUND` | 404 | 없는 닉네임 (Nexon OPENAPI00004) |
| `OPENAPI00005` / `OPENAPI00002` / `OPENAPI00006` | 503 / 503 / 502 | 잘못된 키 · 권한 없음 · 잘못된 경로. 운영자 확인 필요, CRITICAL 로그 |
| `OPENAPI00007` | 429 | Nexon 호출량 초과 |
| `OPENAPI00009` / `OPENAPI00010` / `OPENAPI00011` | 503 | 데이터 준비 중 · 게임 점검 · API 점검 |
| `NEXON_TIMEOUT` / `NEXON_UNREACHABLE` | 504 / 502 | Nexon 응답 지연 · 연결 실패 |
| `QUEUE_FULL` / `QUEUE_TIMEOUT` | 503 | 대기열 초과 · 대기 시간 초과. 화면이 Retry-After 뒤 1회 자동 재시도 |

---

## 🧪 개발 · 테스트

```bash
uv run ruff check .                                   # 린트
uv run python -m unittest discover -s tests -t .      # 전체 테스트
```

### 골든 테스트
`tests/test_golden.py`는 `tests/fixtures/characters/`의 실제 응답 픽스처로 진단 결과를 만들어 `tests/fixtures/golden/`의 스냅샷과 비교합니다. 점수 공식이나 가이드 문구를 **의도적으로** 바꿨다면 스냅샷을 갱신하고 diff를 확인한 뒤 함께 커밋합니다.
```bash
UPDATE_GOLDEN=1 uv run python -m unittest tests.test_golden
```

### 픽스처 재수집
직업 세트(히어로 · 보우마스터 · 비숍 · 섀도어 · 데몬어벤져 · 제논)와 중/하위권 표본을 넥슨 랭킹에서 골라 저장합니다. 캐릭터 이름 · 길드 · 월드 · 이미지는 저장하지 않습니다.
```bash
uv run python scripts/fetch_fixtures.py                 # 기본 세트 (NEXON_API_KEY 필요)
uv run python scripts/fetch_fixtures.py --skip-defaults --names 내캐릭 --labels 내캐릭-테스트
```

### 의존성 변경
`pyproject.toml`을 고친 뒤 lock과 배포용 requirements를 함께 갱신합니다. CI가 둘의 동기화 여부를 검사합니다.
```bash
uv lock
uv export --format requirements-txt --no-dev --no-hashes --quiet > requirements.txt
```

---
