# 🩺 MECULATOR - 장비 딸깍 진단서

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
* **동시 진단 제한**: 동시에 처리하는 진단을 5건으로 제한(asyncio Semaphore)하고, 초과 요청은 대기열에서 순서를 기다리게 하여 API 키 차단을 방지합니다.
* **진행 상태 조회**: `/request-status/{request_id}`로 대기 순번과 처리 단계(캐릭터 확인 → 장비 조회 → 결과 생성)를 폴링하여 화면에 표시합니다.
* **오류 매핑**: 넥슨 API 오류를 404 / 429 / 503 / 504 등 HTTP 상태와 사용자용 메시지로 변환하고, 화면은 그 메시지를 그대로 보여줍니다.

---

## 🛠 기술 스택 (Tech Stack)

### Backend
- **Framework**: FastAPI (Python)
- **HTTP Client**: httpx (AsyncClient, 연결 풀 공유)
- **Concurrency**: Asyncio (Semaphore control)
- **Template Engine**: Jinja2

### Frontend
- **CSS Framework**: Tailwind CSS (Medical Grid UI)
- **JS Framework**: Alpine.js (Reactive Data Binding)
- **Design Concept**: Clean White & Blue (Medical Report Style)

### API
- **Nexon Open API**: 캐릭터 정보 및 장비 데이터 수집

---

## 📁 프로젝트 구조 (Structure)

```text
app/
├── main.py                 # FastAPI 서버, 엔드포인트(/, /check-items, /request-status, robots.txt, sitemap.xml)
├── scraper.py              # Nexon Open API 클라이언트 및 오류 변환
├── analyzer.py             # 장비 평가, 가이드 문구, 총평, 최적 프리셋 선택
├── calculator.py           # 점수 공식 (추가옵션, 잠재능력, 스타포스, 무기)
├── request_tracker.py      # 동시 진단 제한 및 요청 진행 상태 추적
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
tests/                      # unittest 기반 테스트
```

---

## ⚙️ 실행 방법

1. **환경 변수 설정**: `.env` 파일을 생성하고 넥슨 오픈 API 키를 등록합니다.
   ```text
   NEXON_API_KEY=your_api_key_here
   # 선택: robots.txt / sitemap.xml에 쓰는 공개 주소 (기본값 https://meculator.onrender.com)
   SITE_URL=https://your-domain.example
   ```
2. **패키지 설치**:
   ```bash
   pip install -r requirements.txt
   ```
3. **서버 실행**:
   ```bash
   uvicorn app.main:app --reload
   ```
4. **테스트 실행**:
   ```bash
   python -m unittest discover -s tests -t .
   ```

---
