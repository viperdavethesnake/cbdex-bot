# Post-Mortem: Phase 2 Paper Trading — Live Feed Failure

**Date:** 2026-04-21  
**Status:** Paper trader stopped. Phase 2 closed.

---

## Executive Summary

The paper trader ran for 4 days (Apr 17–20) and recorded 17 trades. All were technically invalid because the live feature pipeline used **GeckoTerminal** as the OHLCV source for AERO/WETH — the same source that was explicitly rejected during Phase 1 data ingestion due to confirmed coverage gaps. The model was trained on continuous 1-minute eth_getLogs bars; live inference used a sparse, gap-filled GeckoTerminal feed. All 21 features (volatility, returns, rolling means) were computed on a fundamentally different time series. The paper trades cannot be used to validate the strategy.

---

## Root Cause

### The Mismatch

| Component | Data Source |
|---|---|
| Training data (Phase 1) | `eth_getLogs` — Swap events, Base RPC, continuous 1-minute OHLCV |
| Live inference (Phase 2) | **GeckoTerminal** — REST API, sparse candles only |

The Phase 1 audit explicitly rejected GeckoTerminal for AERO/WETH with the finding: _"24–60 dropped candles per 90-day window"_. Despite this, `execution/live_features.py` was written using GeckoTerminal's `/ohlcv/minute` endpoint.

### The Scale of the Problem

Diagnostic run on 2026-04-21:

```
On-chain Swap events (last 60 min via eth_getLogs): 41
GeckoTerminal candles returned for 65-candle request: 65
Actual time span of those 65 candles:               255 minutes
Silent minutes (gaps):                              191 minutes (75%)
```

When the model requested 65 minutes of 1-minute bars to compute its features, GeckoTerminal silently delivered 65 *active-trade* candles spanning over 4 hours, dropping 191 silent minutes entirely.

### Feature Corruption

Rolling window features were computed on candle *count*, not clock time:

| Feature | Intended meaning | Actual computation |
|---|---|---|
| `vol_15` | Std of returns over 15 minutes | Std over 15 active-trade candles, up to ~60 real minutes |
| `ret_5` | 5-minute log return | Return over 5 trades, up to ~30 real minutes |
| `vol_ratio` | vol_5 / vol_30 (short vs long regime) | Meaningless ratio of two differently-distorted windows |
| `range_pos_60` | Price position in 60-minute range | Price position in up to 4-hour range |

The **regime filter** (`vol_15 ≥ 0.0098`) was gating signals based on an incorrectly computed volatility measure. The model's output is undefined against this feature vector.

### How It Wasn't Caught

1. `live_features.py` was written at the same time as `paper_trader.py`, during rapid Phase 2 development.
2. The test suite (`tests/test_live_features.py`) mocked the GeckoTerminal API call — it verified that feature column names matched `FEATURE_COLS_AERO`, but could not detect that the underlying data was structurally wrong.
3. The paper trader's JSONL log had a blind spot: the stale-data path and regime-filter path both `continue` before writing to the JSONL file. For April 21, 474 log entries across 4 days accumulated, but zero entries for the final day — the missing JSONL records were the first clue something was wrong.

---

## Paper Trading Results (Invalid)

| | |
|---|---|
| Period | Apr 17 – Apr 20, 2026 |
| Total closed trades | 17 |
| Direction | SHORT only (p_long never cleared 0.60 threshold) |
| Win rate | 12/17 (71%) |
| Gross net PnL | +$5.42 (in-session only — resets on restart) |
| Position size | $50 per trade |
| Round-trip fee | $0.32 (pool 0.6% + gas $0.02) |

These numbers cannot be used for any validation purpose. The feature vector fed to the model during live operation did not match the feature distribution on which the model was trained.

---

## What Would Be Required to Fix This

The fix is surgical: replace `fetch_ohlcv()` in `execution/live_features.py` with an eth_getLogs implementation that:

1. Fetches the head block number + timestamp (one RPC call)
2. Fetches Swap event logs for the preceding ~70 minutes via `eth_getLogs` on `mainnet.base.org` (CHUNK_SIZE=2000, typically 1 request)
3. Decodes each Swap using the same logic as `decode_swap_logs()` in `ingestion/aero_weth_pipeline.py`
4. Estimates block timestamps as `head_ts - (head_block - log_block) * 2` (~2s/block on Base L2)
5. Aggregates to 1-minute OHLCV with zero-volume bars for silent minutes
6. Converts WETH volume to USD using `fetch_weth_usd()` (GeckoTerminal WETH/USDC — no gaps on that pool)

This approach is already fully proven in `ingestion/aero_weth_pipeline.py`. The live pipeline is a 70-minute window of the same logic.

Other issues to address before re-starting paper trading:
- Add JSONL log entries on the stale-data and regime-filter paths (currently invisible)
- `tvl_norm` is always 1.0 in live inference (scalar constant across the rolling window; low priority)
- Confirm the model threshold (currently 0.60) is still appropriate after fixing features

---

## Current State of the Codebase

### What is solid
- Phase 1 data pipeline: complete and audited (PASS)
- ML model training/evaluation (`strategies/model.py`): walk-forward, no leakage
- Paper trader execution logic (`execution/paper_trader.py`): PnL math, fee accounting, kill switch, daily loss limit, bar-count position tracking — all correct
- On-chain execution path (Aerodrome Router via web3.py): not yet wired, but architecture is sound

### What is broken
- `execution/live_features.py:fetch_ohlcv()` — must be replaced with eth_getLogs

### What is aspirational
- `execution/live_features.py:fetch_tvl()` — `tvl_norm` is neutralised to 1.0 (see CLAUDE.md)
- Systemd service files exist but are not installed
- Data refresh script exists but is not scheduled

---

## Lessons

1. **The test suite validated column names, not data quality.** Mocking the API is necessary for fast tests but insufficient for verifying that the live source is the same source as training.

2. **The audit gate principle — no data reaches ML without passing a quantitative audit — was applied rigorously to training data but not extended to the live inference path.** The same rejection criteria that eliminated GeckoTerminal for training should have been applied when selecting the live data source.

3. **Invisible log paths are a monitoring failure.** When the regime filter fires 100% of the time, the JSONL file goes silent. A heartbeat record or periodic status record would have surfaced this immediately.
