# CLAUDE.md — Project Context for Claude Code

> 이 파일은 Claude Code가 프로젝트에 진입할 때 자동으로 읽는 컨텍스트 파일이다.
> 중요한 운영 규칙과 진행 방법을 여기에 기록한다.

---

## Project Identity

- **Project**: MS BED Pilot 200 Nm³/h
- **Type**: Engineering project (Python simulation + design documents)
- **User**: 파도라미 (REX 채널 운영자, 파이프라인 엔지니어)
- **Working language**: Korean for discussion, English for code & docs

---

## Mandatory Reading Order (첫 진입 시)

순서대로 읽으면서 컨텍스트를 흡수할 것:

1. **`README.md`** — 프로젝트 전체 개요 + 현재 상태
2. **`docs/DBD_v1.0.md`** — 설계기준서 (LOCKED, SSOT)
3. **`docs/PHASE2_SPEC.md`** — 현재 진행할 Phase 2의 상세 사양
4. **`docs/design_decisions.md`** — 지금까지의 의사결정 이력
5. **`config/dbd_locked.yaml`** — 코드가 import할 SSOT
6. **`config/adsorbent_properties.yaml`** — 흡착제 등온식 파라미터

위 6개 파일을 모두 읽기 전에 코드 작성 금지.

---

## Operational Rules

### Rule 1 — SSOT 원칙
- `config/dbd_locked.yaml` 만이 설계 파라미터의 진실 공급원이다
- 모든 Python 모듈은 이 파일을 import한다
- 하드코딩된 매직 넘버 금지

### Rule 2 — 의사결정 기록
- 새로운 설계 의사결정이 발생하면 **반드시** `docs/design_decisions.md`에 DD-XXX 형식으로 추가
- 사용자에게 의사결정을 요청할 때는 항상 대안 2~3개를 제시

### Rule 3 — 단위 테스트 우선
- 새 모듈 작성 시 `tests/test_<module>.py`를 함께 만든다
- 단위 테스트 PASS 후에 다음 모듈로 진행
- REX 프로젝트의 단계별 검증 패턴 적용

### Rule 4 — Phase 1 일관성
- Phase 2 시뮬레이션 결과는 **Phase 1 엑셀 결과와 일치**해야 한다
- 일치하지 않으면 모델 또는 가정 재검토 (Phase 1 엑셀이 검증된 기준)
- 일관성 검증 통과 전에 Phase 3 진입 금지

### Rule 5 — 사용자 작업 흐름 존중
- 사용자는 REX 프로젝트(별도)와 본 프로젝트를 병행한다
- 한 번에 하나의 작업 결정만 확인 후 진행 (REX와 동일 패턴)
- 큰 변경사항은 사용자 승인 후 진행

---

## Current Phase: Phase 2 — 1D Simulation

### 진행 순서
1. `config/` 파일 검토 + 누락된 파라미터 식별
2. `apps/phase2_simulation/isotherms.py` + `tests/test_isotherms.py`
3. `apps/phase2_simulation/ldf_kinetics.py` + tests
4. `apps/phase2_simulation/adsorption_1d.py` (PDE solver) + tests
5. `apps/phase2_simulation/run_breakthrough.py` (단일 사이클)
6. **Phase 1 일관성 검증** (필수 게이트)
7. `apps/phase2_simulation/run_cycle.py` (TSA 사이클)
8. `apps/phase2_simulation/run_sensitivity.py` (27 case)
9. `docs/PHASE2_REPORT_v1.0.md` 작성

### 진입점 명령
사용자가 "Phase 2 시작해줘" 또는 비슷한 요청을 하면:

1. README, DBD, PHASE2_SPEC를 차례로 읽었음을 확인
2. 등온식 파라미터의 TODO 항목 (`adsorbent_properties.yaml`의 `todo` 섹션) 확인
3. 사용자에게 첫 작업 항목 확인:
   - "isotherms.py부터 시작할까요? Toth(Alumina)와 Langmuir(13X) 두 모델을 구현하겠습니다."
4. 승인 후 작업 시작

---

## Tech Stack

- Python 3.11
- numpy, scipy (PDE solver: scipy.integrate.solve_ivp BDF)
- pandas (data handling)
- matplotlib (visualization)
- pyyaml (config loading)
- pydantic (config validation)
- openpyxl (Phase 1 엑셀 입력 + 결과 엑셀 출력)
- pytest (testing)
- ruff (linting + formatting)

---

## File Naming Conventions

- Python files: `snake_case.py`
- Test files: `test_<module>.py`
- Config files: `<purpose>.yaml`
- Documents: `<TYPE>_v<version>.md` (예: `DBD_v1.0.md`)
- Output files: 결과별 폴더 + descriptive name (예: `outputs/phase2/breakthrough_curves/design_case.csv`)

---

## Communication Style

- 한국어로 사용자와 대화
- 코드 주석, docstring, 변수명은 영어
- 보고서/문서: 핵심 결과는 한국어, 기술 표준 용어는 영어 병기
- 기술적 의사결정은 항상 대안과 근거 제시 (REX의 의사결정 패턴)

---

## Reference Project: REX

사용자는 REX(YouTube 콘텐츠 자동화) 프로젝트를 운영 중이며, 본 프로젝트의 모듈러 구조 / 단계별 검증 / GOLDEN 파일 / 의사결정 이력 패턴은 모두 REX 모노레포에서 가져온 것이다. 사용자가 "REX처럼 해줘"라고 하면 본 패턴을 의미한다.

---

## Out of Scope (Claude Code에서 하지 않을 것)

- 본 시험장치의 실제 구매·발주
- 실제 기기 외주 의뢰 (사용자가 직접 수행)
- HAZOP 분석 (Phase 4에서 별도 회의)
- 실험 데이터 측정 (Phase 6에서 현장 수행)

Claude Code는 **설계 검토, 시뮬레이션, 문서 작성, 데이터 분석**까지가 책임 범위이다.

---

*Last updated: 2026-05-06*
