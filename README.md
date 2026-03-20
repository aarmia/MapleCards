# 🩺 MECULATOR - 장비 딸깍 진단서

**MECULATOR**는 메이플스토리 유저의 아이템 상태를 정밀 분석하여, 어떤 부위를 우선적으로 강화하거나 교체해야 하는지 정해진 기준을 통해 점수로 환산하여 가이드를 제공하는 **Precision Diagnosis System**입니다.

---

## 🚀 핵심 기능 (Key Features)

### 1. 캐릭터 장비 정밀 스캔
* **닉네임 기반 조회**: 넥슨 오픈 API를 연동하여 캐릭터의 현재 착용 장비 정보를 실시간으로 불러옵니다.
* **최적 프리셋 자동 선택**: 유저가 설정한 여러 개의 장비 프리셋 중 가장 전투력이 높거나 완성도가 높은 프리셋을 자동으로 판별하여 분석합니다.

### 2. 다차원 점수 산출 로직 (Scoring System)
단순한 수치 합산이 아닌, 아이템의 레벨과 부위별 특성을 고려한 **상대 점수제**를 채택했습니다.
* **추가옵션(Add)**: 아이템 레벨별(130~250제) 목표 급수를 설정하고, 도달률에 따라 점수를 부여합니다.
* **잠재능력(Pot)**: 윗잠재와 에디셔널을 구분하여, 캐릭터 주스탯 및 공격력/마력 효율을 계산하여 점수화합니다.
* **스타포스(Star)**: 22성 전후의 효율 차이와 아이템 레벨에 따른 가중치를 적용합니다.

### 3. 지능형 진단 가이드 (Smart Guide)
* **취약 부위 우선 순위**: 전체 장비 중 점수가 낮은 순서대로 'Rank'를 매겨, 가장 시급한 부위 5종을 상단에 노출합니다.
* **맞춤형 조언**: 점수 밸런스를 분석하여 "스타포스 권장", "에디셔널 보완", "완제품 교체 시급" 등 구체적인 행동 지침을 텍스트로 제공합니다.
* **특수 등급 애니메이션**: 일정 점수(극 엔드급) 이상을 달성한 종결급 아이템에는 무지개 빛 애니메이션 효과가 적용됩니다.

### 4. 서버 안정성 및 배포 최적화
* **서버 세마포어(Semaphore)**: 동시 접속자가 몰릴 경우 API 호출을 순차적으로 처리(동시 10명 제한)하여 API 키 차단을 방지합니다.

---

## 🛠 기술 스택 (Tech Stack)

### Backend
- **Framework**: FastAPI (Python)
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
├── main.py          # FastAPI 서버 로직 및 API 엔드포인트
├── scraper.py       # Nexon API 연동 및 데이터 가공
├── image_gen.py     # (옵션) 카드 이미지 생성 로직
├── static/          # 정적 파일 (로고, 파비콘, CSS)
│   └── images/      # logo.jpg, favicon.ico
└── templates/       # HTML 템플릿
    └── index.html   # 메인 진단 페이지
```

---

## ⚙️ 실행 방법

1. **환경 변수 설정**: `.env` 파일을 생성하고 파일에 넥슨 오픈 API 키를 등록합니다.
   ```text
   NEXON_API_KEY=your_api_key_here
   ```
2. **패키지 설치**:
   ```bash
   pip install -r requirements.txt
   ```
3. **서버 실행**:
   ```bash
   uvicorn app.main:app --reload
   ```

---
