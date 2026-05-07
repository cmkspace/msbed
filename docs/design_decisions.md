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
