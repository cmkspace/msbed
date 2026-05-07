# Design Basis Document (DBD) — MS BED Pilot 200 Nm³/h

> **Status: LOCKED v1.0 (2026-05-06)**
> 
> 이 문서는 본 시험장치 설계의 단일 진실 공급원(SSOT)이다. 모든 후속 Phase는 이 문서의 값을 참조한다. 변경이 필요한 경우 v1.1, v1.2... 로 버전을 올리고 변경 이력을 본 문서 하단에 기록한다.

---

## 1. Purpose & Scope

### 1.1 Purpose
- 200 Nm³/h 규모 압축공기로부터 H₂O와 CO₂를 ASU-grade 사양으로 제거하는 **Twin-bed Layered TSA** 시험장치의 설계기준 확정
- 흡착제·컬럼 설계 변화에 따른 효율 측정 가능한 **모듈러·계측 집약형** 구조 채택

### 1.2 Scope
- 200 Nm³/h 처리유량 (운전조건 ≈ 35.6 m³/h @ 5 bar(g), 15°C)
- Activated Alumina (하부) + Zeolite 13X (상부) layered single column × 2기
- 흡착 4h / 재생 4h (heat 2h + cool 1.5h + buffer 0.5h)
- 옥내 설치, 압축기 외부구매

### 1.3 Out of Scope
- ASU 본체 (Cold Box, 분리탑) — 본 시험장치는 전처리만 다룸
- 재생가스 dry air의 외부 공급 인프라 (별도 설계)
- 안전 분석 HAZOP (Phase 4에서 별도 수행)

---

## 2. Process Conditions (LOCKED)

| 항목 | 값 | 단위 | 비고 |
|---|---|---|---|
| 처리유량 | 200 | Nm³/h | 0°C, 1 atm 기준 |
| 운전압력 | 5.0 | bar(g) | 압축기 토출, 컬럼 입구 |
| 입구온도 | 15 | °C | 수세냉각탑 출구 |
| 입구 RH | 100 | % | 5 bar·15°C 포화 (보수적) |
| 입구 H₂O | 2,823 | ppm(v) | Antoine 정확계산 |
| 입구 CO₂ | 400 | ppm(v) | 대기 평균 |
| **출구 CO₂ 목표** | **< 0.1** | **ppm(v)** | **ASU-grade** |
| **출구 H₂O 목표** | **dewpoint -76°C 이하** | | **ASU-grade** |
| 외부 환경 | 옥내 | | 일반구역, 380V 3φ 가용 |

---

## 3. Cycle Configuration (LOCKED)

| 항목 | 값 | 단위 |
|---|---|---|
| 운전 방식 | Twin-bed alternating TSA | |
| 컬럼 수 | 2 | 기 |
| 흡착 시간 | 4.0 | h |
| 재생 시간 (총) | 4.0 | h |
| └ 가열 (Heating) | 2.0 | h |
| └ 냉각 (Cooling) | 1.5 | h |
| └ 버퍼 (Depressurize/Repressurize 등) | 0.5 | h |
| 재생온도 (peak) | 200 | °C |
| 재생가스 종류 | Dry air | |
| 재생가스 유량비 | 0.30 | (= 60 Nm³/h) |
| 재생가스 흐름 방향 | 역방향 (counter-current) | |

---

## 4. Adsorbent Specifications (LOCKED, Conservative)

### 4.1 Activated Alumina (하부 층 — H₂O 제거)
| 항목 | 값 | 단위 | 비고 |
|---|---|---|---|
| 동적용량 | **0.06** | kg-H₂O / kg-AA | **6 wt%** (보수, 일반 8~10%) |
| 충진량 | 36.3 | kg/column | SF 1.20 적용 |
| 층고 | 0.925 | m | |
| 입자 직경 | 3 | mm | 구형 비드 |
| 벌크밀도 | 800 | kg/m³ | |
| 흡착열 | ~3,000 | kJ/kg-H₂O | |

### 4.2 Zeolite 13X (상부 층 — CO₂ 제거)
| 항목 | 값 | 단위 | 비고 |
|---|---|---|---|
| 동적용량 | **0.03** | kg-CO₂ / kg-13X | **3 wt%** (보수, 일반 4~5%) |
| 충진량 | 25.1 | kg/column | SF 1.20 적용 |
| 층고 | 0.776 | m | |
| 입자 직경 | 1.6 | mm | 구형 비드 |
| 벌크밀도 | 660 | kg/m³ | |
| 흡착열 | ~700 | kJ/kg-CO₂ | |

### 4.3 보수적 가정 정당성
- 시험장치는 **흡착제·운전조건 변화에 따른 효율 측정**이 목적이므로 컬럼이 작아 시험을 못 돌리는 상황을 방지해야 함
- 동적용량 6%/3%는 **일반 운전조건 기준의 약 75% 수준**으로 oversize됨
- 시험을 통해 실제 동적용량이 측정되면 v2.0에서 갱신

---

## 5. Column Specifications (LOCKED)

| 항목 | 값 | 단위 |
|---|---|---|
| 직경 (Internal) | 250 | mm |
| 전체 길이 | 2,100 | mm |
| 베드 높이 (AA + 13X) | 1,700 | mm |
| Free space (top + bottom) | 400 | mm |
| 표면속도 | 0.201 | m/s |
| L/D 비 | 8.40 | - |
| 베드 ΔP | 0.062 | bar |
| 설계압력 | 7.5 | bar(g) |
| 재질 | STS304 | |
| 코드 | KGS AC111 / ASME Sec VIII Div 1 | |

### 5.1 시험장치 특화 요구사항 (Phase 5에서 상세화)
- 흡착제 충진/배출 플랜지 (상부 맨홀 + 하부 드레인)
- Alumina/13X 층 사이 천공판 + 시료 채취구
- 컬럼 측면 다단 열전대 포트 (8~12점, 축방향 분산)
- 컬럼 측면 가스 샘플링 포트 (3~5점, 축방향 농도 프로파일)

---

## 6. Equipment Sizing Summary (LOCKED)

| 기기 | 사양 | 비고 |
|---|---|---|
| 압축기 | 27.5 kW Oil-free screw, 200 Nm³/h, P_ratio=6.92 | After-cooler 내장, 토출온도 < 100°C |
| 전기히터 | 5 kW SUS 저항식 | Watt density < 5 W/cm² |
| 자동밸브 | 8~10 set, 공압 ball valve | 솔레노이드 + 액추에이터 |
| 수세냉각탑 | After-cooler + chiller 통합 | 출구 15°C 보장 |
| 컬럼 | 2 set (D250 × L2100, STS304) | 위 5절 참조 |

---

## 7. Instrumentation (Phase 5에서 상세 사양 확정)

### 7.1 주요 분석기 (시험장치의 핵심)
| 측정 | 모델 (저예산형) | 위치 |
|---|---|---|
| 출구 CO₂ (저농도) | Siemens Ultramat 23 + low-range cell | 컬럼 출구 |
| 입구 CO₂ | Siemens Ultramat 6E | 압축기 후단 |
| Dewpoint | Vaisala DMT143 | 컬럼 출구 |
| 외부 분석 의뢰 | (Picarro CRDS 외부 분석실) | ppb급 정확 검증용 |

### 7.2 공정 계측
- 컬럼 축방향 RTD Pt100 × 8~12점
- 차압 / 압력계 × 6점
- Coriolis 유량계 × 1 (입구 본관)
- 재생가스 가열 후 온도 계측

---

## 8. Engineering Safety Factors (LOCKED)

| 항목 | 값 | 적용 |
|---|---|---|
| 흡착제 over-design | 1.20 | 충진량 산정 시 |
| 압축기 모터 마진 | 1.15 | 모터 정격 |
| 히터 정격 마진 | 1.20 | 히터 사이즈 |
| 설계압력 계수 | 1.50 | 1.5 × P_op (KGS AC111) |
| 압축기 등엔트로피 효율 | 0.70 | Oil-free screw 평균 |
| 재생 열손실 보정 | 1.15 | 단열 손실 등 |

---

## 9. Test Matrix (Phase 6에서 실행)

| 변수 | Low | Mid (Design) | High |
|---|---|---|---|
| GHSV | 0.5× (= 100 Nm³/h) | 1.0× (= 200 Nm³/h) | 1.5× (= 300 Nm³/h) |
| 재생온도 | 150°C | 180°C | 200°C |
| 사이클시간 | 3 h | 4 h | 5 h |

→ **27 case** 자동 실행 + 데이터 로깅

---

## 10. Budget Constraint (LOCKED)

- **저예산형 ~3.3억 원**
- 자체 엔지니어링 90% (Python 시뮬레이션 + P&ID + 제어로직 자체수행)
- CO₂ 분석기는 Siemens Ultramat 23 + 외부 분석 의뢰 병행 (Picarro 외부 검증)
- PLC: LS XGT (국산)
- 자동밸브: 국산 공압 ball valve
- 압축기: 국산 또는 중급 수입

---

## 11. References & Standards

| 항목 | 표준/문헌 |
|---|---|
| 압력용기 | KGS AC111, ASME Sec VIII Div 1 |
| 안전 | KOSHA |
| 전기 | IEC, KEC |
| 흡착이론 | Ruthven (1984), Yang (1987) |
| 13X CO₂ 등온식 | Cavenati, Grande, Rodrigues (2004) |
| Alumina H₂O 등온식 | Serbezov & Sotirchos (1998), Desai et al. (1992) |
| LDF Kinetics | Glueckauf (1955) |
| Axial Dispersion | Wakao & Funazkri (1978) |

---

## 12. Open Items (다음 Phase에서 결정)

| 항목 | Phase | 비고 |
|---|---|---|
| 등온식 파라미터 정확값 추출 | Phase 2 (Week 1) | 문헌 데이터 fitting |
| LDF 계수 검증 | Phase 2 | Glueckauf 또는 dual-resistance |
| HAZOP 분석 | Phase 4 | 인터록 도출 |
| 흡착탑 internals 상세설계 | Phase 5 | 디스트리뷰터, 그리드 |
| 시험데이터 분석 자동화 | Phase 6 | Python 분석 스크립트 |

---

## Version History

| 버전 | 일자 | 변경 내용 |
|---|---|---|
| **v1.0** | 2026-05-06 | 초기 LOCK. 채팅 → Claude Code 인계 직전 확정. |

---

*이 문서가 변경되면 모든 후속 Phase의 산출물을 재검토해야 한다.*
