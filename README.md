# MS BED Pilot 200 Nm³/h — Engineering Project

> **Twin-bed TSA 시험장치 설계·시뮬레이션·시운전 프로젝트**
> CO₂ 0.1 ppm + H₂O dewpoint -76°C 달성을 위한 Activated Alumina + Zeolite 13X Layered Single Column 시스템

---

## 📌 Project Status (DBD Locked v1.0 — 2026-05-06)

| 항목 | 확정값 | 상태 |
|---|---|---|
| Phase 0 — DBD 작성 | ✅ Complete | `docs/DBD_v1.0.md` |
| Phase 1 — Mass & Energy Balance | ✅ Complete | `outputs/phase1/MS_BED_Phase1_Mass_Energy_Balance_v1.0.xlsx` |
| **Phase 2 — 1D Simulation** | 🟡 **Active** (Claude Code 진입점) | `apps/phase2_simulation/` |
| Phase 3 — 주요기기 사양 | ⬜ Pending | |
| Phase 4 — P&ID + 제어로직 | ⬜ Pending | |
| Phase 5 — 기계설계 | ⬜ Pending | |
| Phase 6 — 제작/시운전/시험 | ⬜ Pending | |

---

## 🎯 Project Overview

### Purpose
- **목적**: 흡착제·컬럼 설계 변화에 따른 수분/CO₂ 제거 효율 측정
- **시험 매트릭스**: GHSV 3 수준 × 재생온도 3 수준 × 사이클시간 3 수준 = **27 run**

### Design Basis (DBD v1.0 — LOCKED)

| 항목 | 값 | 비고 |
|---|---|---|
| 처리유량 | **200 Nm³/h** (0°C, 1 atm) | 운전조건 ≈ 35.6 m³/h |
| 운전압력 | **5.0 bar(g)** | 6.013 bar(a) |
| 입구온도 | **15°C** | 수세냉각탑 출구 |
| 입구 H₂O | **2,823 ppm(v)** | 5 bar·15°C 포화 (Antoine 정확계산) |
| 입구 CO₂ | **400 ppm(v)** | 대기 평균 |
| 출구 목표 | **CO₂ < 0.1 ppm, H₂O dewpoint ≈ -76°C** | ASU-grade |
| 컬럼 구성 | **Activated Alumina (하부) + Zeolite 13X (상부)** Layered Single Column | |
| 컬럼 수 | **2기 Twin-bed** | 흡착 1기 + 재생 1기 교번 |
| 사이클 | **흡착 4h / 재생 4h** | heat 2h + cool 1.5h + buffer 0.5h |
| 재생온도 | **180~200°C** (설계: 200°C) | 13X 완전 탈착 보장 |
| 재생가스 | **Dry air**, ratio 0.30 (= 60 Nm³/h) | |
| 설치환경 | **옥내**, 압축기 외부구매 | |

### Phase 1 핵심 결과 (Phase 2 입력값)

| 항목 | 값 | 단위 |
|---|---|---|
| H₂O 부하 | 0.454 | kg/h |
| CO₂ 부하 | 0.157 | kg/h |
| Activated Alumina 충진량 | **36.3** | kg/column |
| Zeolite 13X 충진량 | **25.1** | kg/column |
| 컬럼 직경 | **250** | mm |
| 컬럼 길이 (전체) | **2,100** | mm |
| Alumina 층고 | 925 | mm |
| 13X 층고 | 776 | mm |
| 표면속도 | 0.201 | m/s |
| 베드 ΔP | 0.062 | bar |
| 설계압력 | 7.5 | bar(g) |
| 컬럼 재질 | STS304 | |
| 압축기 모터 | 27.5 | kW |
| 재생 히터 | 5 | kW |

### 보수적 가정 (시험장치 oversize 정당성)

| 흡착제 | 동적용량 | 비고 |
|---|---|---|
| Activated Alumina | **6 wt%** | 일반 8~10%, 시험장치는 보수적 |
| Zeolite 13X | **3 wt%** | 일반 4~5%, 시험장치는 보수적 |
| 안전계수 (충진량 SF) | 1.20 | |

### 예산
- **저예산형 ~3.3억 원** (CO₂ 분석기는 Siemens Ultramat 23 + 외부 분석 의뢰 병행)
- 자체 엔지니어링 90% (Python 시뮬레이션 + P&ID + 제어로직 자체수행)

---

## 📁 Repository Structure

```
MS_BED_PILOT/
├── README.md                          # ← 현재 파일 (project context)
├── docs/
│   ├── DBD_v1.0.md                    # 설계기준서 (SSOT)
│   ├── PHASE2_SPEC.md                 # Phase 2 상세 사양 (← 진입점)
│   ├── design_decisions.md            # 의사결정 이력 (점진 작성)
│   └── references/                    # 참고문헌 PDF, 도면 스캔
├── config/
│   ├── dbd_locked.yaml                # 모든 코드가 import하는 SSOT
│   └── adsorbent_properties.yaml      # Alumina, 13X 물성 DB
├── apps/
│   ├── phase1_mass_balance/           # ✅ Complete (엑셀로 대체 완료)
│   │   └── (엑셀 파일이 SSOT, 코드 불필요)
│   ├── phase2_simulation/             # 🟡 Active
│   │   ├── isotherms.py               # Langmuir / Toth 등온식
│   │   ├── ldf_kinetics.py            # Linear Driving Force
│   │   ├── adsorption_1d.py           # 1D PDE solver (메인)
│   │   ├── run_breakthrough.py        # Breakthrough 시뮬레이션 실행
│   │   ├── run_cycle.py               # TSA 사이클 시뮬레이션
│   │   ├── run_sensitivity.py         # 민감도 분석 (27 case)
│   │   ├── viz/                       # 시각화 모듈
│   │   │   ├── plot_breakthrough.py
│   │   │   ├── plot_profile.py
│   │   │   └── plot_cycle.py
│   │   └── tests/                     # 단위 테스트
│   │       ├── test_isotherms.py
│   │       ├── test_ldf.py
│   │       └── test_pde_solver.py
│   ├── phase3_equipment/              # ⬜ Pending
│   ├── phase4_pid/                    # ⬜ Pending
│   └── phase6_test_analysis/          # ⬜ Pending
├── outputs/
│   ├── phase1/
│   │   └── MS_BED_Phase1_Mass_Energy_Balance_v1.0.xlsx  # ✅ 받음
│   └── phase2/                        # 🟡 시뮬레이션 결과 출력 위치
│       ├── breakthrough_curves/       # CSV + PNG
│       ├── profiles/                  # 컬럼 내부 프로파일 애니메이션
│       └── sensitivity_summary.xlsx   # 27 case 결과 요약
├── pyproject.toml                     # Python 환경 (uv 또는 poetry)
├── .gitignore
└── .python-version                    # 3.11
```

---

## 🚀 Claude Code 진입 절차

### Step 1: 환경 확인
```bash
cd D:\MK\Engineering\MS_BED_PILOT
python --version    # 3.11 권장
git init
git add .
git commit -m "Initial handoff package from chat"
```

### Step 2: Claude Code 첫 명령
> "이 프로젝트의 README.md, docs/DBD_v1.0.md, docs/PHASE2_SPEC.md를 차례로 읽고, Phase 2 시뮬레이션 코드 구조를 잡아줘. 첫 번째로 isotherms.py와 그 단위 테스트부터 작성해줘."

### Step 3: 점진적 개발 순서 (REX 패턴 적용)
1. `config/dbd_locked.yaml` + `config/adsorbent_properties.yaml` 작성
2. `isotherms.py` + tests (Langmuir, Toth)
3. `ldf_kinetics.py` + tests
4. `adsorption_1d.py` (PDE solver 메인) + tests
5. `run_breakthrough.py` (단일 사이클 시뮬)
6. 검증: Phase 1 엑셀 충진량 vs breakthrough 시간 일치 확인
7. `run_cycle.py` (TSA 사이클 5~10 cycle)
8. `run_sensitivity.py` (27 case 자동 실행)

각 단계마다 **단위 테스트 통과 + 결과 검증** 후 다음 단계로 진행 (REX의 단계별 GOLDEN 확정 패턴).

---

## 🔑 핵심 설계 원칙 (REX 모노레포 경험 기반)

### 1) Single Source of Truth (SSOT)
- `config/dbd_locked.yaml` 만이 설계 파라미터의 진실 공급원
- 모든 코드가 이 파일을 import (REX의 `episode_data.json` 패턴)
- 값 변경 시 한 곳만 수정 → 모든 모듈 자동 반영

### 2) 단계별 검증 (Incremental Validation)
- 각 모듈은 **단위 테스트 PASS** 후 다음 단계 진행
- 통합 검증: **Phase 1 엑셀 결과와 일관성** (예: 4h 사이클에서 breakthrough 발생)
- REX의 "Phase 1 → Phase 2β" 단계별 검증 패턴 동일 적용

### 3) 결과물 격리 (Output Isolation)
- 코드는 `apps/`, 결과물은 `outputs/`
- 결과물은 Git 추적 (재실행 이력 보존), 단 대용량 데이터는 LFS 또는 sample만

### 4) 문서 우선 (Document First)
- 설계 의사결정은 `docs/design_decisions.md`에 누적 기록
- 코드 작성 전 사양 문서화 (`PHASE2_SPEC.md`가 Phase 2의 청사진)

### 5) 보수적 가정 명시
- 모든 가정은 `docs/DBD_v1.0.md`에 근거와 함께 기록
- 시험 진행 후 측정값 기반 업데이트 시 v1.1, v1.2... 버전관리

---

## 📞 Engineering Standards & References

| 항목 | 표준/참고문헌 |
|---|---|
| 압력용기 설계 | KGS AC111, ASME Sec VIII Div 1 |
| 안전 | KOSHA |
| 전기 | IEC, KEC |
| 흡착공정 이론 | Ruthven, "Principles of Adsorption and Adsorption Processes" (1984) |
| TSA 시뮬레이션 | Yang, "Gas Separation by Adsorption Processes" (1987) |
| ASU 전처리 사례 | Air Liquide, Linde 기술자료 (공개분) |

---

## 🔄 Version History

| 버전 | 일자 | 내용 |
|---|---|---|
| v1.0 | 2026-05-06 | Initial handoff from chat — Phase 1 complete, Phase 2 spec ready |

---

## 📌 Open Items (Claude Code에서 해결 예정)

1. Toth/Langmuir 등온식 파라미터 — 문헌 데이터에서 추출 필요
2. LDF 계수 (k_LDF) — Glueckauf 공식 또는 실험값 사용
3. 축방향 분산계수 — Wakao-Funazkri 상관식 적용
4. PDE 수치해법 선택 — Method of Lines (MOL) + scipy.solve_ivp(BDF)
5. 사이클 시뮬레이션의 사이클 안정화 판정 기준
6. 시뮬레이션 검증을 위한 문헌 케이스 선정
