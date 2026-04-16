# Feature Engineering Specification
## Phase 2 — cbdex-bot Base DEX ML Pipeline

**Date:** 2026-04-15  
**Status:** Approved for implementation  
**Reference:** TRD v1.5, PROJECT.md Phase 1 (Backtest & Research)

---

## 1. Data Characterization (Confirmed)

| Metric | WETH/USDC | AERO/WETH |
|---|---|---|
| Candles (90d) | 126,000 | 33,053 |
| Std (log return/min) | 0.077% | 0.666% |
| Fee hurdle | 0.12% (1.5σ) | 0.65% (~1.0σ) |
| Profitable candles | 6.6% | 10.7% of active |
| True signal rate | 6.6% of minutes | ~2.5% of all minutes |
| Max gap | ~1 min (dense) | 86 min |
| Gas corr (price) | −0.025 | N/A |
| Gas corr (volatility) | +0.155 | N/A |

**Design implications:**
- Two separate models — pool types, volatility regimes, and data density are too different to share
- AERO/WETH: train only on active candles, encode gap duration as feature
- Gas is orthogonal to price — adds genuine information, not redundant
- WETH/USDC needs a model that finds conditions for 1.5σ+ moves
- AERO/WETH needs a model that filters the fat-tail regime from the noise

---

## 2. Label Definition

**Label:** Next-candle log return direction and magnitude  
**Formula:** `label = ln(close_t+1 / close_t)`

**For classification (preferred for first pass):**

| Class | Condition | WETH/USDC freq | AERO/WETH freq |
|---|---|---|---|
| LONG | label > +hurdle | 3.3% | 5.3% |
| SHORT | label < −hurdle | 3.4% | 5.3% |
| HOLD | |label| ≤ hurdle | 93.3% | 89.4% |

**Important:** Labels are computed on consecutive active candles only for AERO/WETH. If the next candle is more than 10 minutes away, the label is dropped — the model should not predict across large gaps.

---

## 3. Feature Set

Features are computed in Polars using rolling windows. No lookahead. All rolling windows use `.shift(1)` before the label candle to prevent leakage.

### 3.1 Price Momentum (both pairs)

| Feature | Formula | Window | Rationale |
|---|---|---|---|
| `ret_1` | `ln(close_t / close_{t-1})` | 1 min | Immediate momentum |
| `ret_5` | `ln(close_t / close_{t-5})` | 5 min | Short-term trend |
| `ret_15` | `ln(close_t / close_{t-15})` | 15 min | Medium momentum |
| `ret_30` | `ln(close_t / close_{t-30})` | 30 min | Trend context |
| `ret_60` | `ln(close_t / close_{t-60})` | 60 min | Hour context |

All computed on the raw close series — no interpolation across gaps for AERO/WETH.

### 3.2 Realized Volatility (both pairs)

| Feature | Formula | Window | Rationale |
|---|---|---|---|
| `vol_5` | `std(ret_1) over 5 min` | 5 min | Current regime |
| `vol_15` | `std(ret_1) over 15 min` | 15 min | Short regime |
| `vol_30` | `std(ret_1) over 30 min` | 30 min | Medium regime |
| `vol_ratio` | `vol_5 / vol_30` | — | Regime acceleration |

`vol_5 / vol_30 > 1.0` means volatility is expanding. This is the core of the AERO/WETH regime filter.

**AERO/WETH regime gate (mandatory):** Drop all training rows where `vol_15 < 0.0098` (1.5× the 0.65% fee hurdle). The model only sees active-regime candles.

### 3.3 Relative Volume (both pairs)

| Feature | Formula | Window | Rationale |
|---|---|---|---|
| `vol_rel_5` | `volume_usd_t / mean(volume_usd) over 5 min` | 5 min | Volume burst detection |
| `vol_rel_30` | `volume_usd_t / mean(volume_usd) over 30 min` | 30 min | Sustained flow |
| `vol_rel_60` | `volume_usd_t / mean(volume_usd) over 60 min` | 60 min | Session context |

High relative volume (>2×) on a DEX AMM indicates large swap flow — price impact is larger, moves are directional.

### 3.4 Price Range Position (both pairs)

| Feature | Formula | Window | Rationale |
|---|---|---|---|
| `range_pos_15` | `(close - min(low,15)) / (max(high,15) - min(low,15))` | 15 min | In-range location |
| `range_pos_60` | `(close - min(low,60)) / (max(high,60) - min(low,60))` | 60 min | Hour range location |

Values near 0 = at the bottom of the recent range, near 1 = at the top. Captures mean-reversion potential.

### 3.5 TVL (Pool Depth)

| Feature | Formula | Window | Rationale |
|---|---|---|---|
| `tvl_norm` | `tvl_usd / rolling_mean(tvl_usd, 60 min)` | 60 min | Relative pool depth |

When TVL is declining relative to recent average, the pool is thinner and swap price impact is higher. Forward-filled hourly — low variation within the hour is expected.

### 3.6 Gas Price

| Feature | Formula | Rationale |
|---|---|---|
| `gas_norm` | `base_fee_gwei / rolling_mean(base_fee_gwei, 60 min)` | Relative gas level |
| `gas_abs` | `base_fee_gwei` (raw) | Absolute gas level |

Gas is joined to candle timestamps via `join_asof` backward. Corr(|ret|, gas) = 0.155 — modest but real.

### 3.7 AERO/WETH Only — Gap Duration

| Feature | Formula | Rationale |
|---|---|---|
| `gap_minutes` | Minutes since previous active candle | Encodes inactivity duration |
| `post_gap` | Binary: `gap_minutes > 5` | Post-gap candles are often directional |

The first candle after a quiet period frequently shows directional price movement as accumulated order flow executes.

### 3.8 Time Features (both pairs)

| Feature | Formula | Rationale |
|---|---|---|
| `hour_utc` | `timestamp.hour` | DEX volume peaks 18:00–02:00 UTC |
| `hour_sin` | `sin(2π × hour / 24)` | Cyclical encoding |
| `hour_cos` | `cos(2π × hour / 24)` | Cyclical encoding |

---

## 4. Feature Matrix Summary

| # | Feature | Pairs | Type |
|---|---|---|---|
| 1 | ret_1 | Both | Float |
| 2 | ret_5 | Both | Float |
| 3 | ret_15 | Both | Float |
| 4 | ret_30 | Both | Float |
| 5 | ret_60 | Both | Float |
| 6 | vol_5 | Both | Float |
| 7 | vol_15 | Both | Float |
| 8 | vol_30 | Both | Float |
| 9 | vol_ratio | Both | Float |
| 10 | vol_rel_5 | Both | Float |
| 11 | vol_rel_30 | Both | Float |
| 12 | vol_rel_60 | Both | Float |
| 13 | range_pos_15 | Both | Float |
| 14 | range_pos_60 | Both | Float |
| 15 | tvl_norm | Both | Float |
| 16 | gas_norm | Both | Float |
| 17 | gas_abs | Both | Float |
| 18 | hour_sin | Both | Float |
| 19 | hour_cos | Both | Float |
| 20 | gap_minutes | AERO only | Float |
| 21 | post_gap | AERO only | Binary |

**Total: 19 features (WETH/USDC), 21 features (AERO/WETH)**

---

## 5. Data Handling Rules

### No Lookahead
All rolling windows must be computed on data up to and including `t`. Use Polars `shift(1)` on all feature columns before training to ensure the label is strictly the *next* candle.

### Gap Handling (AERO/WETH)
- Do not interpolate or forward-fill OHLCV across gaps
- Rolling features over inactive gaps will produce incorrect values — compute rolling statistics only across consecutive active candles using index-based windows, not time-based windows
- Drop any row where the label candle is more than 10 minutes after the feature candle

### Null Handling
- Forward-fill `tvl_usd` nulls (31 WETH, 49 AERO) — TVL changes slowly
- Drop rows where rolling windows produce nulls (first N rows of each window)

### Normalization
- Log returns and relative features: no normalization needed
- `gas_abs`: standardize (z-score) using training set statistics only

---

## 6. Walk-Forward Validation

**Split:** 60-day train → 7-day validate → slide 7 days

| Fold | Train | Validate |
|---|---|---|
| 1 | Jan 15 → Mar 15 | Mar 16 → Mar 22 |
| 2 | Jan 22 → Mar 22 | Mar 23 → Mar 29 |
| 3 | Jan 29 → Mar 29 | Mar 30 → Apr 5 |
| 4 | Feb 5 → Apr 5 | Apr 6 → Apr 12 |

Apply the AERO/WETH regime filter after the walk-forward split, not before.

---

## 7. Baseline Model

Before ML, implement a naive baseline: predict LONG if `ret_1 > 0` and `vol_5 > hurdle`. Predict SHORT if `ret_1 < 0` and `vol_5 > hurdle`. Otherwise HOLD. The ML model must beat this after fees and gas.

---

## 8. Implementation Order

1. `research/features.py` — feature computation for both pairs
2. `research/labels.py` — label generation with gap filtering for AERO/WETH
3. `research/baseline.py` — naive momentum baseline
4. `backtest/simulator.py` — gas-aware, fee-aware trade simulator
5. `strategies/model.py` — Random Forest first, upgrade if needed

Do not build the backtest engine until features are validated on at least one walk-forward fold.
