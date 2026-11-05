# 테스트 운영

테스트 숫자를 줄이기 위해 서로 다른 장애 검사를 삭제하지 않는다.
같은 동작은 한 곳에서 검증하고, 과거 실측값 대조는 자동 회귀와 분리한다.

## 스펙업 기능 수정 중

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_spec_up tests.test_starforce_targets tests.test_damage_index
node tests/spec_up_ui.cjs
```

- `test_spec_up`: 옵션 확률·추천 목표·쿨감/시드링·제외 대상
- `test_starforce_targets`: 상한·단계 연결·연속 추천 통합
- `test_damage_index`: 교체 손실·곱연산·확률 가중 데미지
- `spec_up_ui.cjs`: 실제 JS 정렬·시세 변경·추천 통합

## 커밋/배포 전 전체 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
node tests/spec_up_ui.cjs
```

HTTP 입력 검증·페이지 렌더링은 `test_main`에서 담당한다. 같은 API를 직접 호출하는
중복 테스트나 문구·HTML 소스 표현에만 의존하는 테스트는 별도로 추가하지 않는다.
API 오류·요청 제한·로그·세트/프리셋 계산은 독립적인 회귀 위험이 있어 유지한다.
Python 전체 자동 검사는 로컬에서 약 1초 이내로 완료된다(환경에 따라 달라짐).

## 필요할 때만 실행하는 수치 감사

```powershell
# 네트워크 없음: 과거 세 캐릭터 컨텍스트의 54개 환산값 대조
.\.venv\Scripts\python.exe -m scripts.check_efficiency_snapshots
# 네트워크 사용: 최신 API와 과거 참조표 비교
.\.venv\Scripts\python.exe -X utf8 -m scripts.audit_efficiency
# 네트워크 사용: 실제 캐릭터 추천 응답 검증
.\.venv\Scripts\python.exe -X utf8 -m scripts.smoke_damage_recommendations
```

동결 수치 검사는 당시 버프 설정에 의존한다. 설정 변경 때 결과 차이를 조사하는 도구이지
항상 통과해야 하는 제품 규칙이 아니므로 CI 자동 검색 대상에서 분리했다.
