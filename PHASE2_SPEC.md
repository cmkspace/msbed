# Phase 2 Specification — 1D TSA Adsorption Simulation

> **Purpose**: Phase 1에서 산출된 컬럼 사양(D=250mm, L=2,100mm, Alumina 36.3kg + 13X 25.1kg)이 4시간 흡착 사이클에서 CO₂ < 0.1 ppm을 달성하는지 동적으로 검증하고, 운전변수(GHSV, T_regen, t_cycle)의 영향을 정량화한다.

---

## 1. Scope

### 1.1 In Scope
- **흡착 단계** 시뮬레이션: 4시간 동안 컬럼 내부 농도/온도 프로파일
- **재생 단계** 시뮬레이션: 200°C dry air 60 Nm³/h 역방향 통과
- **TSA 사이클** 시뮬레이션: 흡착→감압→가열→냉각→재가압 (5~10 cycle 안정화)
- **민감도 분석**: 27 case (GHSV ±50%, T_regen 150/180/200°C, t_cycle 3/4/5h)
- **검증**: Phase 1 엑셀 결과와의 일관성 + 문헌 케이스 비교

### 1.2 Out of Scope (later phases)
- 3D CFD (분포 효과, 채널링) — Phase 5에서 필요시
- Multi-component competitive isotherm 정밀 모델링 (IAST 적용 안 함, 1차 근사로 처리)
- 압력강하 동적 변화 (Phase 1 Ergun 정상상태 ΔP 사용)

---

## 2. Mathematical Model

### 2.1 Governing Equations

**가스상 물질수지 (성분 i ∈ {H₂O, CO₂})**
```
∂Cᵢ/∂t + u·∂Cᵢ/∂z = D_ax·∂²Cᵢ/∂z² − ((1-ε)/ε)·ρ_p·∂qᵢ/∂t
```

**고체상 흡착속도 (LDF)**
```
∂qᵢ/∂t = k_LDF,i·(qᵢ* − qᵢ)
```

**평형 등온식 (해당 흡착제별 적용)**
```
qᵢ* = f_isotherm(Pᵢ, T)
```

**에너지수지**
```
(ε·ρ_g·c_p,g + (1-ε)·ρ_p·c_p,s)·∂T/∂t 
  = −u·ρ_g·c_p,g·∂T/∂z 
    + λ_ax·∂²T/∂z² 
    + (1-ε)·ρ_p·Σᵢ(−ΔH_ads,i)·∂qᵢ/∂t 
    − (4·U/D)·(T − T_amb)
```

**Notation**
| 기호 | 의미 | 단위 |
|---|---|---|
| Cᵢ | 가스상 농도 of i | mol/m³ |
| qᵢ | 흡착량 of i | mol/kg adsorbent |
| qᵢ* | 평형 흡착량 | mol/kg |
| u | 표면속도 | m/s |
| ε | 베드 공극률 | - |
| ρ_p | 흡착제 입자 밀도 | kg/m³ |
| D_ax | 축방향 분산계수 | m²/s |
| k_LDF | LDF 흡착속도 상수 | 1/s |
| λ_ax | 축방향 열전도 | W/m·K |
| U | 외부 열전달 계수 | W/m²·K |
| ΔH_ads | 흡착열 | J/mol |

### 2.2 Isotherms

#### 2.2.1 H₂O on Activated Alumina — Toth Equation
```
q* = (q_m·b·P) / [1 + (b·P)^t]^(1/t)

q_m = q_m0 · exp[χ·(1 − T/T_ref)]
b   = b0 · exp[ΔH/(R·T_ref)·(T_ref/T − 1)]
```

**파라미터 (문헌값 추출 — TODO)**
| 파라미터 | 값 | 출처 |
|---|---|---|
| q_m0 | TBD | Serbezov & Sotirchos (1998) 또는 Desai et al. |
| b0 | TBD | |
| t (heterogeneity) | TBD | |
| ΔH (heat of ads) | ~3,000~3,500 kJ/kg ≈ 54~63 kJ/mol | |

#### 2.2.2 CO₂ on Zeolite 13X — Langmuir Equation
```
q* = q_m·b·P / (1 + b·P)
b  = b0 · exp(+ΔH/(R·T))   # ΔH stored as positive magnitude (DD-009)
```

> **Sign convention (DD-009)**: ΔH is stored as a positive magnitude in J/mol.
> For exothermic adsorption b must increase as T decreases — the `+` sign
> above produces this. The earlier `−` form in v1.0 of this spec was a
> sign error and is corrected here (2026-05-07).

**파라미터 (문헌값)**
| 파라미터 | 값 | 출처 |
|---|---|---|
| q_m | ~5.5 mol/kg | Cavenati, Grande, Rodrigues (2004) |
| b0 | ~2.4×10⁻⁷ Pa⁻¹ | |
| ΔH | ~36 kJ/mol | |

#### 2.2.3 Multi-component (1차 근사)
- Alumina 층: H₂O만 흡착 (CO₂는 무시)
- 13X 층: H₂O가 우선 흡착 (이미 Alumina가 막아주므로 13X에 도달 시 ~0)
- → 사실상 **각 층에서 단일 성분 흡착**으로 단순화

### 2.3 Mass Transfer Coefficients (LDF)

**Glueckauf 공식**
```
k_LDF = 15 · D_e / r_p²
```
- D_e: 유효 macropore diffusivity (m²/s)
- r_p: 입자 반경 (m)

**유효 확산계수**
```
D_e = ε_p · D_m / τ
```
- ε_p ≈ 0.4 (입자 공극률)
- τ ≈ 3 (tortuosity)
- D_m: 분자 확산계수 (Chapman-Enskog 또는 Fuller eq.)

### 2.4 Axial Dispersion (Wakao-Funazkri)
```
D_ax / D_m = 20 + 0.5·Sc·Re
```
- Re = ρ·u·d_p / μ
- Sc = μ / (ρ·D_m)

---

## 3. Numerical Method

### 3.1 Spatial Discretization — Method of Lines (MOL)

**격자**
- 컬럼 길이 L = 2.10 m
- 노드 수 N = 100 (Alumina 50 + 13X 50, 층 경계에서 노드 일치)
- Δz = L / (N−1)

**유한차분 (Finite Volume Upwind)**
- 1차 미분 (이송): 1차 풍상 (Upwind) — 안정성 우선
- 2차 미분 (분산/전도): 중심차분
- 입출구: Danckwerts 경계조건
  - 입구: u·(C_in − C(0)) = D_ax·∂C/∂z|₀
  - 출구: ∂C/∂z|_L = 0

### 3.2 Time Integration

**ODE 시스템**: 각 격자점에서 (C_H2O, q_H2O, C_CO2, q_CO2, T) → 총 5N = 500 ODEs

**Solver**: `scipy.integrate.solve_ivp`
- method='BDF' (Backward Differentiation, stiff equation 안정)
- rtol=1e-6, atol=1e-9
- dense_output=True (시각화용 보간)

**시간 단계**: 자동 적응 (BDF가 stiffness 따라 조정)

### 3.3 사이클 단계 전환 (TSA)

```python
# Pseudo-code
state = initial_state(C=0, q=0, T=T_in)
for cycle in range(10):
    state = simulate(t=0..14400, mode='adsorption', flow=200 Nm³/h, T_in=15°C)
    state = simulate(t=0..1800,  mode='depressurize')
    state = simulate(t=0..7200,  mode='heating',     flow=60 Nm³/h, T_in=200°C, dir='reverse')
    state = simulate(t=0..5400,  mode='cooling',     flow=60 Nm³/h, T_in=15°C,  dir='reverse')
    state = simulate(t=0..1800,  mode='repressurize')
    save_cycle_state(cycle, state)
```

**사이클 안정화 판정**
- 연속 2개 cycle의 출구 농도 적분값 차이 < 5%
- 통상 5~7 cycle 후 정상상태 도달

---

## 4. Module Structure

### 4.1 `isotherms.py`

```python
"""Isotherm models for H₂O on Alumina and CO₂ on Zeolite 13X."""

def toth_h2o_alumina(P_h2o: float, T: float) -> float:
    """H₂O equilibrium loading on Activated Alumina (Toth eq.).
    
    Args:
        P_h2o: H₂O partial pressure (Pa)
        T: Temperature (K)
    Returns:
        q_eq: Equilibrium loading (mol/kg)
    """
    ...

def langmuir_co2_13x(P_co2: float, T: float) -> float:
    """CO₂ equilibrium loading on Zeolite 13X (Langmuir eq.).
    
    Args:
        P_co2: CO₂ partial pressure (Pa)
        T: Temperature (K)
    Returns:
        q_eq: Equilibrium loading (mol/kg)
    """
    ...
```

**Tests required:**
- `test_toth_zero_pressure`: P=0 → q=0
- `test_langmuir_high_pressure`: P→∞ → q→q_m
- `test_temperature_dependence`: T 증가 → q 감소 (exothermic)
- `test_known_data_point`: 문헌 데이터 1점 일치 (예: 25°C, 400 ppm CO₂)

### 4.2 `ldf_kinetics.py`

```python
"""LDF mass transfer kinetics with Glueckauf approximation."""

def k_ldf(D_eff: float, r_p: float) -> float:
    """LDF coefficient via Glueckauf formula."""
    return 15 * D_eff / r_p**2

def molecular_diffusivity(T: float, P: float, species: str) -> float:
    """Molecular diffusivity in air via Fuller equation."""
    ...
```

**Tests required:**
- `test_glueckauf_units`: 단위 검증 (1/s)
- `test_diffusivity_temperature`: T^1.75 의존성
- `test_diffusivity_pressure`: 1/P 의존성

### 4.3 `adsorption_1d.py` (Core Solver)

```python
"""1D TSA adsorption simulation — main PDE solver."""

@dataclass
class ColumnConfig:
    L: float                    # column length (m)
    D: float                    # diameter (m)
    N: int = 100                # grid points
    layer_split: float = 0.55   # alumina fraction (z-direction)
    
@dataclass
class OperatingConditions:
    flow_nm3h: float
    P_op: float                 # bar(a)
    T_in: float                 # K
    y_h2o_in: float             # mole fraction
    y_co2_in: float             # mole fraction
    mode: str                   # 'adsorption' | 'heating' | 'cooling' | ...

class AdsorptionSolver:
    def __init__(self, col: ColumnConfig, props: AdsorbentProps):
        ...
    
    def simulate(
        self, 
        op: OperatingConditions, 
        t_span: tuple, 
        y0: np.ndarray
    ) -> SimulationResult:
        """Solve PDE system via MOL + scipy.solve_ivp."""
        ...
```

### 4.4 `run_breakthrough.py`

단일 흡착 시뮬레이션 (default 5시간) — Phase 1 일관성의 첫 검증. 5h 길이는
H₂O 5% breakthrough(~4.15h)를 포착하면서 t=4h CO₂ checkpoint도 포함하기 위함.

#### Acceptance Criteria — Three-Gate Structure (DD-014)

세 게이트는 **독립적**이며, 모두 PASS해야 PDE 모델이 Phase 1과 일관됨이
확인된다. 각 게이트는 서로 다른 가정을 검증한다.

##### Gate 1 — H₂O Breakthrough Timing (Cycle Determinant)
- **Definition**: H₂O outlet이 inlet의 5%에 도달하는 시각 t_5%
- **Criterion**: `t_5% ∈ [3.5h, 4.5h]`
- **Physical meaning**: AA 층 dynamic loading 가정(6 wt%) + LDF rate 검증.
  Layered bed의 cycle time을 결정하는 species가 H₂O이므로 본 timing이
  Phase 1 cycle = 4h와 정렬되어야 함.
- **Failure mode interpretation**:
  - `t_5% < 3.5h` → AA dynamic loading이 실제로 6 wt% 미만 OR `k_LDF`가 너무 작음 (front 너무 빨리 도달)
  - `t_5% > 4.5h` → AA dynamic loading이 6 wt% 초과 OR `k_LDF`가 너무 큼
  - `t_5% = NaN` (sim 끝까지 미도달) → sim 길이 부족 OR 흡착 모델이
    가정보다 훨씬 강함 (등온식 capacity 점검 필요)

##### Gate 2 — CO₂ Product Specification (ASU-grade)
- **Definition**: t = 4h 시점의 CO₂ outlet ppm (인렛 비율로 환산: `out_ppm = (C_out / C_in) × 400`)
- **Criterion**: `out_ppm < 0.1 ppm` (DBD §3.5 ASU-grade target)
- **Physical meaning**: 13X dynamic loading 가정 + layered bed 가정
  (H₂O가 13X 층까지 침투하지 않음) 검증.
- **Failure mode interpretation**:
  - `out_ppm ≫ 0.1 ppm` → 13X dynamic loading이 너무 작음 OR
    H₂O가 13X 영역까지 침투해서 CO₂ adsorption site 차단 (layered bed 가정 위반)
- **Note**: CO₂ **breakthrough timing**은 본 게이트에 포함되지 않는다. CO₂는
  13X의 큰 working capacity 덕분에 H₂O보다 훨씬 늦게(추정 6–8h+)
  breakthrough하며, 4h checkpoint는 **product spec 충족 여부**만 검증한다.

##### Gate 3 — Mass Balance Closure (Solver Self-Check)
- **Definition** (each species):
  ```
  cum_adsorbed = ∫(F_in − F_out) dt
  bed_inventory = Σ_cells (ε·C·V + (1−ε)·ρ_p·q·V)
  rel_err_pct = 100 × |cum_adsorbed − bed_inventory| / cum_inlet
  ```
- **Criterion**: `rel_err_pct < 10 %` (both H₂O and CO₂)
- **Physical meaning**: PDE 솔버의 **수치 정확성** 검증 (물리 모델이 아닌
  솔버 회귀 감지). Layout B + sparse-Jac BDF가 정상 동작하면
  near-machine-precision (rel < 1e-6) 수준의 closure가 기대된다.
- **Failure mode interpretation**:
  - `rel_err_pct ~ 1–10%` → 격자 정밀도 부족 (`check_grid_resolution()` 재확인)
  - `rel_err_pct > 10%` → solver/RHS 회귀, 디버깅 필요

#### Phase 1 정량 일관성 (정보 항목)
- H₂O 누적 흡착량 ≈ DBD `loads.h2o_kg_per_cycle` = 1.8152 kg
- CO₂ 누적 흡착량 ≈ DBD `loads.co2_kg_per_cycle` = 0.6283 kg
- 충진량(AA 36.3 kg + 13X 25.1 kg)과의 dynamic_loading 비율이 DBD §6 가정과 일치

### 4.5 `run_cycle.py`

5~10 cycle TSA 시뮬레이션 — 사이클 안정화 확인.

**Output:**
- 각 cycle별 흡착량/탈착량
- 사이클 안정화 도달 cycle 번호
- 정상상태에서의 outlet purity

### 4.6 `run_sensitivity.py`

**27 case 매트릭스:**

| 변수 | Low | Mid | High |
|---|---|---|---|
| GHSV | 0.5× | 1.0× (design) | 1.5× |
| T_regen | 150°C | 180°C | 200°C |
| t_cycle | 3 h | 4 h (design) | 5 h |

**Output:** `outputs/phase2/sensitivity_summary.xlsx`
- 27행 × [GHSV, T_regen, t_cycle, outlet_co2_avg, outlet_h2o_avg, working_capacity, regen_energy]
- 회귀분석 (어느 변수가 가장 영향이 큰가)

---

## 5. Validation Strategy (REX 패턴 적용)

### 5.1 단위 테스트 (Unit Tests)
- 각 모듈: pytest 통과 100%
- 등온식: 문헌 데이터 1점 이상 일치 (오차 < 10%)
- LDF: 단위 검증 + 차원 분석

### 5.2 Phase 1 엑셀과의 일관성 검증
- Breakthrough 시점 ≈ 4h (Phase 1 사이클 시간)
- 누적 흡착량 ≈ 충진량 × 동적용량 × SF
- ⚠️ 일치하지 않으면 **모델 또는 Phase 1 가정 재검토**

### 5.3 문헌 케이스 비교
- Skarstrom (1960) — 초기 PSA 케이스
- Cavenati et al. (2004) — CO₂/CH₄ on 13X
- 최소 1개 케이스에서 정성적 일치 확인 (breakthrough 형태)

### 5.4 Sanity Checks
- 등온식: T 증가 시 q 감소 (exothermic 흡착)
- 사이클: 시간 진행에 따라 출구 농도 단조 증가
- 에너지수지: 흡착열 발생 시 컬럼 온도 상승 확인

---

## 6. Deliverables

### 6.1 코드
- ✅ Phase 2 모든 모듈 + 단위 테스트
- ✅ pytest 통과 100%
- ✅ Type hints + docstring (Google style)

### 6.2 시뮬레이션 결과 (`outputs/phase2/`)
- `breakthrough_design_case.csv` + `.png` (4h, 200 Nm³/h, 15°C, 200°C 재생)
- `profile_animation_design.mp4` (컬럼 내부 농도/온도 진행)
- `cycle_stabilization.png` (5~10 cycle 안정화)
- `sensitivity_summary.xlsx` (27 case)

### 6.3 보고서
- `docs/PHASE2_REPORT_v1.0.md`
- 핵심 그래프 + 결과 해석 + Phase 3 권고사항

---

## 7. Known Risks & Mitigations

| Risk | Mitigation |
|---|---|
| 등온식 파라미터 불확실성 | 보수적 값 사용 + 시험 후 fitting |
| LDF 1차 근사의 정확도 한계 | 문헌 cross-check, 필요시 dual-resistance 모델로 확장 |
| Stiff PDE의 수치 발산 | BDF + dense grid + adaptive time step |
| 다성분 경쟁흡착 무시 | layered bed 가정으로 회피 (Alumina가 H₂O 흡수 → 13X 도달 시 ~0) |
| 비등온 효과의 단순화 | 1D 가정, 외벽 단열 가정. 시험 검증 후 update |

---

## 8. Estimated Schedule

| Week | Tasks |
|---|---|
| Week 1 | `isotherms.py`, `ldf_kinetics.py`, `adsorption_1d.py` 골격 + 단위 테스트 |
| Week 2 | `run_breakthrough.py`, Phase 1 일관성 검증, 시각화 |
| Week 3 | `run_cycle.py`, `run_sensitivity.py`, 27 case 자동 실행, 보고서 |

총 3주 (REX와 병행 시 4~5주 가능).

---

## 9. Phase 3 진입 조건

다음 항목이 모두 만족되어야 Phase 3 (주요기기 사양) 진입:

- [ ] 모든 단위 테스트 PASS
- [ ] Breakthrough 시뮬에서 4h@CO₂<0.1ppm 달성 확인
- [ ] 사이클 안정화 도달 (5~10 cycle 이내)
- [ ] 27 case 민감도 결과 검토 완료
- [ ] PHASE2_REPORT_v1.0.md 작성 완료
- [ ] design_decisions.md에 Phase 2 의사결정 기록

---

## Appendix A. References

1. Ruthven, D.M., *Principles of Adsorption and Adsorption Processes*, Wiley, 1984.
2. Yang, R.T., *Gas Separation by Adsorption Processes*, Butterworths, 1987.
3. Cavenati, S., Grande, C.A., Rodrigues, A.E., "Adsorption Equilibrium of Methane, Carbon Dioxide, and Nitrogen on Zeolite 13X at High Pressures", *J. Chem. Eng. Data* 49, 1095-1101 (2004).
4. Wakao, N., Funazkri, T., "Effect of fluid dispersion coefficients on particle-to-fluid mass transfer coefficients", *Chem. Eng. Sci.* 33, 1375-1384 (1978).
5. Glueckauf, E., "Theory of Chromatography. Part 10", *Trans. Faraday Soc.* 51, 1540 (1955).
