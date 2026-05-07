# Design Decisions Log

> 본 프로젝트의 모든 설계 의사결정을 시간순으로 기록한다.
> 각 결정에는 **컨텍스트, 대안, 선택, 근거, 영향**을 명시한다.
> REX 프로젝트의 Phase 1 Audit 사례에서 검증된 패턴.

---

## DD-001: 처리유량 단위 — Nm³/h vs 운전조건 m³/h

- **Date**: 2026-05-05
- **Phase**: 0 (DBD)
- **Decision**: 처리유량을 **200 Nm³/h** (표준상태 기준)로 확정
- **Context**: 초기 도면에는 "200 m³/h"로만 표기되어 있어 단위 모호성 존재. 운전조건(5 bar) 기준 vs 표준상태 기준에 따라 컬럼 크기가 약 5~6배 차이.
- **Alternatives**: 
  - (a) 운전조건 200 m³/h → 1,200 Nm³/h 환산 → 컬럼 D ≈ 500 mm
  - (b) 표준상태 200 Nm³/h → 운전조건 35.6 m³/h → 컬럼 D ≈ 250 mm
- **Choice**: (b) 표준상태 200 Nm³/h
- **Rationale**: 시험장치 규모로 적정. 옥내 설치 조건과 합치.
- **Impact**: 모든 후속 사이징의 기준값.

---

## DD-002: 출구 CO₂ 목표 — 0.1 ppm

- **Date**: 2026-05-05
- **Phase**: 0 (DBD)
- **Decision**: **CO₂ < 0.1 ppm** 목표 (ASU-grade)
- **Context**: 시험장치이지만 ASU 전처리 시뮬레이션이 목적이므로 산업 사양 그대로 적용.
- **Alternatives**: 
  - (a) 0.1 ppm — ASU 표준
  - (b) 1 ppm — 일반 dryer 수준
- **Choice**: (a) 0.1 ppm
- **Rationale**: Cold Box freezing 방지 사양과 일치. 측정 가능한 가장 엄격한 목표.
- **Impact**: 
  - 재생온도 180~200°C 필수 (120°C로는 13X 완전 탈착 불가)
  - CO₂ 분석기 ppb급 측정 능력 필요 (저예산형은 외부 분석 병행)

---

## DD-003: 재생가스 — Dry air

- **Date**: 2026-05-05
- **Phase**: 0 (DBD)
- **Decision**: 재생가스로 **Dry air** 사용
- **Context**: 일반적으로 ASU 전처리는 제품 가스 일부(질소 또는 dry air)를 recycle하지만, 시험장치는 외부 공급도 가능.
- **Alternatives**: (a) Dry air, (b) N₂, (c) 제품가스 recycle
- **Choice**: (a) Dry air
- **Rationale**: 사용자 명시적 선택. 외부 공급 인프라 단순.
- **Impact**: 재생가스 dewpoint < -40°C 사양의 외부 dry air 공급 필요.

---

## DD-004: 컬럼 구성 — Layered Single Column

- **Date**: 2026-05-05
- **Phase**: 0 (DBD)
- **Decision**: **Activated Alumina (하부) + Zeolite 13X (상부)** layered single column 채택
- **Alternatives**:
  - (a) Layered single column
  - (b) 2-stage separated columns (AA 컬럼 + 13X 컬럼)
- **Choice**: (a) Layered single column
- **Rationale**: 표준 ASU 전처리 구성. 하나의 컬럼에서 H₂O와 CO₂ 동시 제거. 재생도 한 번에 가능.
- **Impact**: 컬럼 internals 설계 시 층 사이 시료 채취구 필요 (Phase 5).

---

## DD-005: 동적용량 가정 — 보수적 (6%/3%)

- **Date**: 2026-05-06
- **Phase**: 1 (Mass Balance)
- **Decision**: Activated Alumina **6 wt%**, Zeolite 13X **3 wt%** 보수적 가정
- **Context**: 일반 산업 설계는 8~10% (AA), 4~5% (13X) 사용. 시험장치에서 컬럼이 작아 실험 못 돌리는 위험 회피 필요.
- **Alternatives**: (a) 표준값, (b) 보수값
- **Choice**: (b) 보수값
- **Rationale**: 시험장치 목적(흡착제·운전 변화에 따른 효율 측정)에 부합. 측정 후 v2.0에서 갱신.
- **Impact**: 충진량이 ~25% 더 큼 → 컬럼 길이 ~25% 증가 → 비용 다소 증가.

---

## DD-006: 시뮬레이션 도구 — Python 자체개발

- **Date**: 2026-05-05
- **Phase**: 2 (Simulation)
- **Decision**: **Python 1D Axial Dispersion + LDF 자체개발**
- **Alternatives**: (a) Aspen Adsorption, (b) gPROMS, (c) Python 자체
- **Choice**: (c) Python 자체
- **Rationale**: 
  - 저예산형 (~3.3억) 한도 내에서 라이선스 비용 회피
  - 사용자(파도라미)의 Python/REX 자체개발 역량 활용
  - 27 case 자동화 및 결과 분석 통합 용이
- **Impact**: Phase 2에 3주 추가 개발 시간 필요. Aspen 대비 정밀도는 다소 낮으나 시험장치 검토 목적에는 충분.

---

## DD-007: 작업 환경 — Claude Code (Antigravity IDE)

- **Date**: 2026-05-06
- **Phase**: 2 (Simulation 진입)
- **Decision**: 채팅 인터페이스에서 **Claude Code (Antigravity IDE)로 이동**
- **Context**: Phase 2~6 전체 코드베이스가 ~2,500~3,000 LOC 규모로 채팅 관리 한계 도달.
- **Choice**: Claude Code (REX와 동일 환경)
- **Rationale**: 
  - 다파일 일관성 관리 필요 (DBD 변경 시 영향 추적)
  - Git 통합으로 의사결정 이력 추적
  - 단위 테스트 병행
  - 사용자 작업 환경(REX 모노레포)과 일관성
- **Impact**: 본 인계 패키지 (README + PHASE2_SPEC + config) 전달 후 Claude Code에서 작업 재개.

---

## DD-008: 예산 — 저예산형 (~3.3억)

- **Date**: 2026-05-06
- **Phase**: 0 (DBD)
- **Decision**: **저예산형 ~3.3억 원**으로 lock
- **Choice details**:
  - CO₂ 분석기: Siemens Ultramat 23 + low-range (Picarro CRDS는 외부 분석 의뢰 병행)
  - PLC: LS XGT (국산)
  - 자동밸브: 국산 공압 ball valve
  - 수세냉각탑: After-cooler + chiller 통합
  - 자체 엔지니어링 90%
- **Impact**: 
  - 출구 CO₂ 0.1 ppm 검증은 외부 분석실(Picarro/LGR) 의뢰 필요
  - PLC 프로그래밍은 자체 수행 또는 국산 SI 외주

---

## DD-009: 등온식 파라미터 provisional 보정 + Van't Hoff 부호규약 정정

- **Date**: 2026-05-07
- **Phase**: 2 (Simulation — Week 1)
- **Decision**: Toth(AA-H₂O) 및 Langmuir(13X-CO₂) 등온식 파라미터를 DBD/문헌 기준점에 맞춰 provisional 보정. Van't Hoff 부호규약을 양수 ΔH magnitude 컨벤션으로 통일.

### Issue
초기 `config/adsorbent_properties.yaml`의 placeholder 값들이 **물리적으로 비합리적인 결과**를 산출:

| 등온식 | 기존 b0 | 설계조건 q | 기대값 | 괴리 |
|---|---|---|---|---|
| Toth (AA-H₂O) | 1.0e-9 Pa⁻¹ | 0.286 wt% @ 1697 Pa, 298 K | DBD 6 wt% | ~21× 미달 |
| Langmuir (13X-CO₂) | 2.4e-7 Pa⁻¹ | 5.45 mol/kg @ 240 Pa, 298 K | Cavenati ~2-3 mol/kg | ~2× 과다 |

또한 `PHASE2_SPEC.md §2.2.2`의 Langmuir 식 `b = b0·exp(−ΔH/(R·T))`은 ΔH가 양수(=흡착열 magnitude)로 저장될 때 **온도 방향이 반대**가 됨 (cold→b 작아짐, exothermic과 모순).

### Alternatives
- **(A) 보정 진행 + 부호규약 정정** (제안값: Toth b0=1.0e-3, Langmuir b0=4.0e-9, 부호 `+ΔH/RT`)
- **(B) Toth만 보정, Langmuir는 YAML 그대로 + saturation 동작 수용**
- **(C) 두 b0 모두 placeholder 유지 + 테스트 정성검증으로 완화**

### Choice
**(A)** — 두 등온식 모두 보정, 부호규약 정정.

### Rationale
- (B), (C)는 PDE solver의 입력으로 비물리적 값을 통과시킬 위험이 있음 → Phase 1 일관성 검증(필수 게이트) 단계에서 더 큰 문제 누적 가능.
- 보정값이 Phase 1 DBD(AA 6 wt%) 및 Cavenati 2004 (13X 100 Pa→2.5 mol/kg) 두 독립 기준점과 정합 → 정량적 근거 확보.
- 부호 오류는 명백한 결함이므로 분리 처리 비효율, 한 번에 정정.

### Implementation
1. `config/adsorbent_properties.yaml`:
   - `alumina_h2o_toth.b0_Pa_inv`: **1.0e-9 → 1.0e-3 Pa⁻¹** (provisional)
   - `zeolite_13x_co2_langmuir.b0_Pa_inv`: **2.4e-7 → 4.0e-9 Pa⁻¹** (provisional)
   - 두 섹션에 `provisional_calibration` 메타데이터 블록 추가 (calibration date, design point, expected q, tolerance, status)
2. `apps/phase2_simulation/isotherms.py`:
   - Toth, Langmuir 함수 모두 양수 ΔH magnitude 컨벤션으로 구현
   - `sanity_check_at_design_point()` 함수 — 자동 검증 게이트
   - 함수 docstring에 부호규약 명시
3. `docs/PHASE2_SPEC.md §2.2.2`: `exp(−ΔH/RT)` → `exp(+ΔH/RT)` 정정 + 주석 추가
4. `apps/phase2_simulation/tests/test_isotherms.py`:
   - `test_known_data_point_cavenati`: 298 K, 100 Pa → 2.0–3.0 mol/kg
   - `test_known_data_point_aa_wt_pct`: 298 K, 1697 Pa → 3.0–9.0 wt%
   - `test_phase1_consistency`: sanity_check 자동 호출
   - `test_sanity_check_diagnoses_bad_*`: 실패 진단 메시지 동작 검증
5. `CLAUDE.md`에 Rule 6 추가 (등온식 파라미터 검증 게이트).

### Status
**PROVISIONAL — pending literature fitting in Phase 2 Week 2.**

### Validation
- 1차 검증: `test_known_data_point_cavenati` (Cavenati 2004 ±20%) + `test_phase1_consistency` (DBD 6 wt% ±50%) 통과로 확보.
- 2차 검증: Phase 2 Week 2에 Serbezov(1998) 및 Cavenati(2004) PDF에서 직접 파라미터 추출 후 v1.1로 갱신 예정.
- breakthrough 시뮬 결과가 Phase 1 엑셀 (4h 사이클, 충진량 36.3+25.1 kg)과 일치하는지가 최종 통합검증 (Phase 2 Week 2 게이트).

### Impact
- Phase 2 모든 PDE solver 입력이 물리적으로 일관된 등온식 파라미터 사용.
- 이후 등온식 파라미터 변경 시 `sanity_check_at_design_point()`이 자동 게이트로 동작 (CLAUDE.md Rule 6).
- 추후 PDF 직접 fitting 결과가 보정값과 ±10% 이내면 v1.1로 lock, 그렇지 않으면 Phase 1 일관성 재검토 필요.

### Lessons Learned
- Initial b0 placeholder (1e-5) gave physically reasonable-looking
  intermediate values (Henry's K_H ~1.3e-4 mol/kg/Pa) but was 10× off
  in final wt% due to Toth's nonlinear saturation behavior in the
  `1+(bP)^t` denominator. Linear extrapolation from `b·P` intuition
  fails for Toth — must compute the full equation at the design point.
- Sign-convention errors in Van't Hoff forms produce *plausible-looking*
  numbers at one temperature but invert the temperature direction.
  The bug only surfaces under multi-T testing (regen vs adsorption).
- All future placeholder isotherm parameters MUST be sanity-checked at
  the DBD design point before code commit (now enforced by
  `test_phase1_consistency` and `CLAUDE.md` Rule 6).

---

## Template for New Decisions

```markdown
## DD-XXX: [제목]

- **Date**: YYYY-MM-DD
- **Phase**: [0~6]
- **Decision**: [한 줄 요약]
- **Context**: [배경, 왜 이 결정이 필요한가]
- **Alternatives**: [고려한 대안들]
- **Choice**: [선택]
- **Rationale**: [선택 근거]
- **Impact**: [후속 영향]
```
