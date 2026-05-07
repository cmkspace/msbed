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

### Rule 6 — 파라미터 검증 게이트 (개정, DD-010, DD-012)
새로운 흡착·동역학 파라미터를 도입하거나 변경할 때:

1. **등온식**: design point에서 ±50% 일관성 (문헌 기준점은 ±20%)
   - 게이트: `apps/phase2_simulation/isotherms.py::sanity_check_at_design_point()`
   - 자동검증: `tests/test_isotherms.py::test_phase1_consistency`
2. **LDF**: dual-resistance 정통 형식 (`1/k = 1/k_macro + 1/k_internal`) + design point에서 사용자 정의 범위 일관성 + **MTZ 폭이 grid에서 5+ cells 차지**
   - 게이트: `apps/phase2_simulation/ldf_kinetics.py::sanity_check_at_design_point()`
   - 자동검증: `tests/test_ldf_kinetics.py::test_phase2_consistency`
3. 새 파라미터 도입 시 **mechanistic vs empirical 구분 명시** docstring 필수
4. **Empirical 파라미터**는 YAML에 `*_provenance` + `*_validity` 메타데이터 필수
5. 통과 못 하면 코드 작성 중단, 사용자에게 보고
6. **사전 임계값(threshold)의 근거 명시 (Rule 6.6, DD-012)**:
   1. 문헌 또는 측정 데이터 기반 근거 명시 (없으면 `"TBD — first measurement will set baseline"`)
   2. 첫 측정 후 임계값과 측정값이 1자릿수 이상 차이나면 임계값 재검토
   3. 임계값 변경 시 이유 + 측정값 + 문헌 근거를 DD에 기록
   4. 보수적 임계값(stop early)이 잘못되면 false positive로 작업 지연 발생 — 문헌 데이터가 있을 때는 그 범위의 상한을 임계값으로 사용
7. 의사결정 이력은 DD-XXX 형식으로 `docs/design_decisions.md`에 기록 (Issue / Decision / Status / Validation / Known Limitations / Lessons Learned)
8. PDE 격자 N의 변경은 항상 `check_grid_resolution()`을 27 case 극단(GHSV 1.5×)에서 PASS하는지 함께 검증

### Rule 6.7 — Mode Validation: Multi-Dimensional Stability (DD-017, 강화 DD-021)

ODE/PDE 모드별 안정성 검증 시 다음 모든 차원에서 측정:

1. **Time scale**: 충분히 긴 sim duration (full mode duration, e.g. 2 h heating).
2. **State context**: 실제 cycle 진입 시 state (이전 단계 결과, not 합성 uniform).
3. **Hypothesis vs measurement**: 물리적 가설은 측정으로 검증. 가설과 측정이 모순되면 가설 즉시 폐기, 측정 기반 재구성.
4. **Solver call pattern (강화 DD-021)**: **Chunked restart는 모든 integrating phase의 DEFAULT**. Single-call은 측정으로 정당화될 때만 사용 (faster + stable across full operating envelope, not just design point).

   Default 적용 사례:
   - Adsorption: chunked (DD-014 + DD-021 confirmation)
   - Heating: chunked (DD-017)
   - Cooling: chunked (DD-017)

   Single-call exception 조건 (모두 만족해야 함):
   1. Operating envelope 4코너 case 모두 stable 측정
   2. Wall time 30%+ 단축 측정값 확보
   3. Regression test가 envelope 모두 커버

BDF는 stepper history-dependent이므로 short test가 long test를 보장하지 않는다. Phase 5B 결정 등 critical path에서는 위 4 차원 모두에서 측정 데이터 확보 필수. Step 5.4.0b (preflight, uniform state, 1.5 h)는 PASS였지만 Step 5.4.1 (cycle-realistic, 2 h, single-call)은 FAIL, Step 5.5 (adsorption single-call at GHSV=0.5×)도 FAIL → Rule 6.7 강화의 두 motivating example.

### Rule 6.10 — Operating Envelope Validation (DD-021)

Design point에서 PASS는 모든 operating point PASS를 보장하지 않는다. 새 모듈/전략 도입 시 다음 단계 의무:

1. **Design point 검증** (basic correctness).
2. **Operating envelope 검증 (코너 case)**:
   - Min/max GHSV (예: 0.5×, 1.5×)
   - Min/max temperature (예: 150 °C, 200 °C regen)
   - Min/max cycle time (예: 3 h, 5 h)
   - 최소 4개 corner case (factorial corner 또는 worst-case combination)
3. **Envelope 검증 PASS 후에만 production 적용**.

Phase 2 누적 사례:
- Step 5.4.0d: Heating preflight design state PASS, cycle reality state FAIL.
- Step 5.5: Adsorption single-call design GHSV PASS, GHSV=0.5× FAIL.

둘 다 envelope 검증 누락이 원인. 향후 Phase 6 데이터 분석 모듈, Phase 5B (도입 시) analytical Jacobian 검증, Phase 4 HAZOP 분석 등에도 동일 적용.

### Rule 6.9 — Metric Design: Multi-Convention + Noise Floor (DD-018)

Closure / balance metric 설계 시:

1. **Noise floor 명시**: Numerical zero 영역에서 percentage metric은 의미 없다. Absolute threshold(예: 1×10⁻⁶ mol)를 두고, scale이 이 미만이면 'PASS with degenerate flag'로 처리. False fail 방지.
2. **Multi-convention support**: Engineering convention (실험 측정과 동일) + Numerical convention (model 내부 일관성) 둘 다 측정. 단일 metric은 false positive/negative 위험.
3. **Gate threshold는 convention-specific**: 같은 5 % gate가 conservative form과 primitive form에서 의미 다름. Spec 작성 시 convention 명시 필수.
4. **실험 비교 가능 convention 보존**: Phase 6 실험 데이터와 비교 가능한 convention을 반드시 측정. Numerical-only는 비교 불가.

Phase 2 사례 (DD-018):
- Cooling mass closure 89 % false fail → noise floor 누락
- Heating energy closure 11.6 % → conservative-vs-primitive form mismatch
- 둘 다 metric design 문제, 모델 자체 문제 아님.

### Rule 6.8 — Hypothesis Falsification Priority (DD-017)

복잡한 PDE/ODE 시스템에서 직관적 물리 가설은 자주 틀린다. Phase 2 누적 사례 4건 모두 가설이 부정확:

- DD-009: Toth `b₀` 부호규약
- DD-012: stiffness ratio 1.27e8 (사전 추정 1e6, 100× 어긋남)
- DD-013: sparsity nnz 추정 3,900 vs 실측 3,094 (Rule 6.6 calibration)
- DD-017: heating crash 원인 (가설=물리 stiffness, 실제=BDF stepper history)

따라서:

1. 가설은 진단의 **출발점이지 결론이 아님**.
2. 측정 데이터가 가설과 모순되면 가설 **즉시 폐기**, 측정 기반 재구성.
3. 진단 작업은 **측정 인프라 구축**이 우선 (예: Step 5.4.0d의 stiffness time profile).
4. 가설 기반 quick fix를 측정 검증보다 우선하지 않음.

이 Rule은 Phase 5B 도입 결정, 27-case 디버깅, Phase 6 실험 데이터 해석 등 모든 후속 단계에 적용.

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
