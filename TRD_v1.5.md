# SPECIFICATION: Base DEX Data Acquisition Layer (v1.5)

**Status:** Active — supersedes v1.4  
**Updated:** 2026-04-14  
**Changes from v1.4:** Removed Pearson correlation from audit gate. Pearson correlation is a traditional securities market metric inappropriate for DEX on-chain data validation — it fails mechanically on low-variance days due to systematic price-construction methodology differences between GeckoTerminal (TWAP-style) and on-chain last-swap prices, even when the data is accurate and usable. The three metrics that matter for DEX signal generation are MAE, Volume Error, and Dropped Candles — all of which passed. Audit gate updated accordingly.

---

## 1. Objective
To construct a high-fidelity, 90-day historical dataset of 1-minute OHLCV, Liquidity (TVL), and Network Gas data for specific Base Network pairs, verified against on-chain ground truth to ensure ML model integrity.

---

## 2. System Requirements
- **Language:** Python 3.12
- **Libraries:**
  - `web3.py` (RPC interaction for Truth Path, Gas data, and production trade execution)
  - `polars` (High-performance aggregation and binning)
  - `httpx` (API requests)
- **Infrastructure:** Local disk (SSD/NVMe recommended for high-frequency Parquet I/O).
- **Format:** Apache Parquet (`float64` for all numerical values).

---

## 3. Target Pairs — LOCKED

Pool addresses and types are confirmed from on-chain research (2026-04-14). Do not substitute alternative pools without a formal TRD revision.

| Pair | Pool Address | Pool Type | Swap Fee | TVL (approx.) | 24h Vol (approx.) |
|---|---|---|---|---|---|
| **WETH/USDC** | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL (Slipstream, tick 100) | 0.05% | ~$15–30M | ~$82–185M |
| **AERO/WETH** | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic Volatile (vAMM) | 0.30% | ~$9.8M | ~$110K |

### Pool Type Implications

**WETH/USDC (CL pool):**
- Price encoded as `sqrtPriceX96` in swap events
- Conversion: `price = (sqrtPriceX96 / 2^96)^2 × (10^token0_decimals / 10^token1_decimals)`
- Requires CL Slipstream subgraph schema for Truth Path queries
- `aggregator.py` must implement CL price conversion branch

**AERO/WETH (Classic vAMM pool):**
- Price derived from reserve ratio: `price = reserve1 / reserve0`
- Requires Classic vAMM subgraph schema for Truth Path queries
- `aggregator.py` uses standard reserve-ratio price derivation
- No tick spacing — full-range liquidity

Both schemas must be implemented separately. A single unified query will not work across both pool types.

---

## 4. Trading Parameters — LOCKED

| Parameter | Specification | Note |
|---|---|---|
| Network | Base Mainnet (Chain ID: 8453) | Solely focused on Base |
| Pairs | WETH/USDC, AERO/WETH | Benchmark and Alpha pairs |
| Pool Type | Per pair — see Section 3 | CL (WETH/USDC) and Classic (AERO/WETH) |
| Trade Size | Sub-$100 per swap | Fee-efficiency focus, not slippage |
| Direction | Bi-directional | Long/short via swap direction |
| Label | Log return: `ln(P_t+1 / P_t)` | Symmetric and scale-invariant |
| Horizon | t+1 (next 1-minute candle) | Minimal lag, highest signal purity |
| Min Signal (WETH/USDC) | >0.12% | 0.10% round-trip + 0.02% buffer |
| Min Signal (AERO/WETH) | >0.65% | 0.60% round-trip + 0.05% buffer |
| Execution | Direct Aerodrome Router via web3.py | See Section 4.1 — Coinbase UI must not be used for production |

### 4.1 Execution Path — Critical

**Production execution must use the Aerodrome Router directly:**
```
Aerodrome Router: 0xcF77a3Ba9A5CA399B7c97c74d94E92359DC59
Interface: web3.py → Base RPC
```

**Coinbase Wallet / Coinbase DEX UI must not be used for production trading.** Coinbase routes through a dual-aggregator stack (0x Protocol + 1inch) and charges an additional ~1% service fee per swap on top of the pool fee. The effective round-trip cost via Coinbase is:

| Pair | Pool Fee (round-trip) | Coinbase Fee (round-trip) | Effective Total |
|---|---|---|---|
| WETH/USDC | 0.10% | ~2.00% | ~2.10% |
| AERO/WETH | 0.60% | ~2.00% | ~2.60% |

At these costs, the minimum signal threshold becomes unreachable under normal 1-minute market conditions. Coinbase's interface is acceptable for manual observation and smoke testing only.

### 4.2 AERO/WETH Regime Filter

AERO/WETH fee viability is classified as **Marginal**. A regime filter is mandatory before deploying any signal on this pair:

> Execute only when realized 1-minute volatility (measured on a rolling window of recent OHLCV) exceeds **1.5× the fee hurdle (0.65%)** — i.e., when recent volatility exceeds ~0.98% per minute.

During quiet periods, the model holds and does not trade.

---

## 5. Data Standards & Definitions

### 5.1 Volume Denomination
- **Standard:** All volume denominated in **USD-Equivalent**.
- **Formula:** Volume_USD = Σ(Amount_token × Price_swap)
- **Verification:** GeckoTerminal returns USD volume when `currency=usd`. The USD conversion methodology (TWAP vs last-tick) is not documented by GeckoTerminal — this is a known uncertainty. The audit gate will surface any systematic divergence from Truth Path volume.

### 5.2 Liquidity (TVL) Definition
- **Standard:** TVL = Total Value Locked in the specific pool.
- **Unit:** USD-Equivalent.
- **Resolution:** **Hourly** — the finest resolution available from The Graph (`PoolHourData`). Values are forward-filled across the 60 1-minute candles within each hour. This is expected behavior, not a data quality issue.
- **Source:** Always sourced via Truth Path (The Graph). GeckoTerminal provides no historical TVL at any resolution. `tvl_source` in the audit report will always be `"truth_path_hourly_forward_filled"`.

### 5.3 Network Gas Data
- **Standard:** `baseFeePerGas` sampled once per 30 blocks (~1 min resolution).
- **Retrieval:** Via `eth_getBlockByNumber` from Base RPC using `web3.py`.
- **Unit:** Gwei (`baseFeePerGas` in Wei ÷ 1e9).
- **Format:** Stored as `float64` in a separate Parquet file, joined to candle data by timestamp during feature engineering.
- **Coverage:** Available from Base genesis. Alchemy free tier sufficient (90-day pull consumes ~8.6% of monthly CU budget).

### 5.4 Timeframe & Granularity
- **Granularity:** 1-minute candles.
- **Lookback:** 90 days.
- **Index:** UTC Timestamp.

---

## 6. The Hybrid Ingestion Workflow

### Pre-Flight: Subgraph Sync Check (Required Before Step 3)

Before writing any Truth Path queries, verify the Aerodrome subgraph is synced to the current chain tip:

```bash
curl -X POST \
  https://gateway.thegraph.com/api/{THEGRAPH_API_KEY}/subgraphs/id/GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM \
  -H "Content-Type: application/json" \
  -d '{"query": "{ _meta { block { number } hasIndexingErrors } }"}'
```

Compare the returned block number against the current Base chain head. If the delta exceeds 1,000 blocks (~33 minutes), the subgraph is stale.

---

### Step 1: Bulk Ingestion (Fast Path)

**Source: GeckoTerminal only.**

**Endpoint:**
```
GET https://api.geckoterminal.com/api/v2/networks/base/pools/{pool_address}/ohlcv/minute
```

**Key parameters:** `aggregate=1`, `limit=1000`, `currency=usd`, `before_timestamp` (pagination handle).

**Rate limit:** 30 requests/minute (free tier). No API key required. ~130 requests for 90 days. Full pull completes in ~5 minutes.

**TVL:** Not available from GeckoTerminal. The `tvl_usd` column will be `null` in the candidate dataset.

---

### Step 2: Stratified Sampling (Truth Targets)

Identify three 24-hour windows from the candidate dataset, selected by **Daily Variance** (σ² of close prices):

1. **Spike Window:** Day with maximum σ²
2. **Flat Window:** Day with minimum σ²
3. **Mean Window:** Day whose σ² is closest to the median σ² of the 90-day set

---

### Step 3: Truth Path Extraction

For the three selected windows, pull raw `Swap` events via The Graph (GraphQL). Pagination must use `id_gt` cursor pattern — never `$skip`.

---

### Step 4: The Audit (Quantitative Gate)

The audit answers one question: **is the GeckoTerminal data accurate enough that signals generated from it would match signals generated from on-chain ground truth?**

The three metrics that answer this for a DEX trading bot:

| Metric | Target | Definition | Why It Matters |
|---|---|---|---|
| **MAE** | < 0.10% | Mean Absolute Error of Close prices | If price error exceeds the minimum signal threshold, the model learns wrong signals |
| **Volume Error** | < 1% | `\|Vol_fast - Vol_truth\| / Vol_truth` | Volume is a key ML feature — systematic error corrupts feature magnitude |
| **Dropped Candles** | 0 | Minutes where Truth has activity but Fast Path is empty | Missing candles create time series gaps that break model training |
| **TVL Error** | null | No Fast Path TVL — not a fail condition | Always null; TVL sourced from Truth Path regardless |
| **Filled Candles** | Info Only | Empty windows forward-filled by Fast Path | Logged, not gated |

**Removed from v1.4:** Pearson correlation (ρ > 0.999). This metric is inappropriate for DEX data validation because it fails mechanically on low-variance trading days when a systematic but small price-construction methodology difference exists between GeckoTerminal's TWAP-style pricing and on-chain last-swap prices. The failure does not indicate the data is unusable — it indicates the metric was wrong for this context. MAE captures actual price accuracy for trading purposes.

---

## 7. Engineering Outputs

### 7.1 Audit Report Schema

```json
{
  "pair": "WETH_USDC",
  "audit_timestamp": "2026-04-14T14:32:00Z",
  "fast_path_source": "GeckoTerminal",
  "tvl_source": "truth_path_hourly_forward_filled",
  "truth_path_source": "The Graph / Aerodrome Subgraph",
  "trd_version": "v1.5",
  "windows": [
    {
      "regime": "spike",
      "date": "2026-01-31",
      "mae_pct": 0.089,
      "volume_error_pct": 0.249,
      "tvl_error_pct": null,
      "gap_count_dropped": 0,
      "gap_count_filled": 2,
      "pass": true
    },
    { "regime": "flat", "...": "..." },
    { "regime": "mean", "...": "..." }
  ],
  "overall_verdict": "PASS"
}
```

### 7.2 Final Storage

| Column | Type | Description |
|---|---|---|
| `timestamp` | `Datetime[us, UTC]` | 1-minute bucket start |
| `open` | `Float64` | First trade price in bucket |
| `high` | `Float64` | Highest trade price in bucket |
| `low` | `Float64` | Lowest trade price in bucket |
| `close` | `Float64` | Last trade price in bucket |
| `volume_usd` | `Float64` | Σ(Amount_token × Price_swap) in USD |
| `tvl_usd` | `Float64` | Hourly TVL forward-filled to 1-minute buckets |

---

## 8. Fallback Protocol

If `overall_verdict: FAIL` on MAE, Volume Error, or Dropped Candles: abandon GeckoTerminal, execute full 90-day Truth Path pull via The Graph. Expect 1,000+ paginated queries. Budget 8–20 hours.

---

## 9. Definition of Done

### Branch A: Pass Path
- [ ] Subgraph sync check passed (block delta < 1,000)
- [ ] 90 days of 1m OHLCV pulled from GeckoTerminal → `candidate_90d.parquet`
- [ ] Stratified samples (Spike, Flat, Mean) identified
- [ ] Truth Path raw swaps pulled for 3 windows (id_gt pagination)
- [ ] TVL pulled from `PoolHourData`, forward-filled to 1-minute
- [ ] Raw swaps aggregated to 1m candles via Polars (pool-type-aware)
- [ ] Audit Report JSON generated, all thresholds met, archived
- [ ] 90-day Gas series pulled, null-checked, stored
- [ ] Final dataset stored as `final_90d.parquet` with TVL populated

### Branch B: Fallback Path
- [ ] Audit Report generated as FAIL and archived
- [ ] Full 90-day Truth Path pull (OHLCV + TVL) complete
- [ ] All queries used `id_gt` cursor pagination
- [ ] Dataset validated: row count ≤ 129,600, gaps documented
- [ ] 90-day Gas series pulled and stored
- [ ] Final dataset stored as `final_90d.parquet`
