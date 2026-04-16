# SPECIFICATION: Base DEX Data Acquisition Layer (v1.5)

**Status:** Active — supersedes v1.4  
**Updated:** 2026-04-15  
**Changes from v1.4:** Removed Pearson correlation from audit gate. Pearson correlation is a traditional securities market metric inappropriate for DEX on-chain data validation.  
**Changes within v1.5 (2026-04-15):** Dropped Candle definition updated to exclude zero-volume on-chain ghost candles. AERO/WETH ingestion method updated: The Graph GEN subgraph indexes CL pools only — Classic vAMM pools require direct eth_getLogs via Base RPC. GeckoTerminal confirmed to have genuine coverage gaps on AERO/WETH (sparse pair). AERO/WETH uses eth_getLogs as primary source, not fallback.

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
| **AERO/WETH** | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic Volatile (vAMM) | 0.30% | ~$2.3–4.6M | ~$110K |

### Pool Type Implications

**WETH/USDC (CL pool):**
- Price encoded as `sqrtPriceX96` in swap events
- Conversion: `price = (sqrtPriceX96 / 2^96)^2 × (10^18) / (10^6)` (WETH=token0 18dec, USDC=token1 6dec)
- Truth Path: The Graph CL subgraph (`nZnftbmERiB2tY6t2ika7kP9srTcKnYFEnqG3RKa38r`)
- TVL: `PoolHourData` from The Graph, forward-filled to 1-minute

**AERO/WETH (Classic vAMM pool):**
- Token ordering: token0 = WETH (18 dec), token1 = AERO (18 dec)
- Price (WETH per AERO): `amount0In / amount1Out` (selling AERO) or `amount0Out / amount1In` (buying AERO)
- **Truth Path: eth_getLogs via Base RPC — NOT The Graph**
  - The Graph GEN subgraph (`GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM`) indexes CL (Slipstream) pools only. It does not index Classic vAMM pools. Querying `pool(id: "0x7f670f78...")` returns null.
  - Direct on-chain log fetching via `eth_getLogs` is the correct and only approach.
  - Swap event: `Swap(address,address,uint256,uint256,uint256,uint256)` — both addrs indexed
  - Sync event: `Sync(uint256,uint256)` — used for TVL from pool reserves
- **GeckoTerminal Fast Path not viable:** GeckoTerminal has confirmed coverage gaps on AERO/WETH — it does not index every 1-minute trading window for this lower-priority pair. The audit gate correctly rejected it. `aero_weth_pipeline.py` uses eth_getLogs as primary source.
- **Candle density:** AERO/WETH produces ~33,000 candles per 90 days (vs 129,600 for WETH/USDC) — approximately 1 active minute in 4. Gaps during low-activity periods are expected and normal.
- TVL: 2 × reserve0 (WETH) × WETH/USD price, sourced from Sync events, hourly forward-filled

Both schemas must be implemented separately. A single unified ingestion approach will not work across both pool types.

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
Aerodrome Router (verified on BaseScan): 0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43
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

During quiet periods (which represent ~75% of minutes for this pair), the model holds and does not trade.

---

## 5. Data Standards & Definitions

### 5.1 Volume Denomination
- **Standard:** All volume denominated in **USD-Equivalent**.
- **WETH/USDC:** `amountUSD` from The Graph subgraph.
- **AERO/WETH:** `weth_amount × weth_close_price` derived from on-chain swap data and WETH/USDC `final_90d.parquet`.

### 5.2 Liquidity (TVL) Definition
- **WETH/USDC:** `PoolHourData` from The Graph, hourly, forward-filled to 1-minute. `tvl_source: "truth_path_hourly_forward_filled"`.
- **AERO/WETH:** Derived from Sync events via eth_getLogs. `TVL = 2 × reserve0_weth × weth_price`. Hourly, forward-filled to 1-minute. `tvl_source: "on_chain_sync_hourly_forward_filled"`.
- GeckoTerminal provides no historical TVL at any resolution for either pair.

### 5.3 Network Gas Data
- **Standard:** `baseFeePerGas` sampled once per 30 blocks (~1 min resolution).
- **Retrieval:** Via `eth_getBlockByNumber` from Base RPC using `web3.py`.
- **Unit:** Gwei (`baseFeePerGas` in Wei ÷ 1e9).
- **Coverage:** 90 days — 129,600 rows, blocks 40,836,070 → 44,724,040, range 0.0005–2.9245 Gwei.

### 5.4 Timeframe & Granularity
- **Granularity:** 1-minute candles.
- **Lookback:** 90 days.
- **Index:** UTC Timestamp.
- **Candle counts:** WETH/USDC ~129,600 (dense). AERO/WETH ~33,053 (sparse — gaps are expected).

---

## 6. The Hybrid Ingestion Workflow

### Per-Pair Ingestion Methods

| Pair | Fast Path | Truth Path | TVL Source |
|---|---|---|---|
| WETH/USDC | GeckoTerminal (PASS ✅) | The Graph CL subgraph | PoolHourData (The Graph) |
| AERO/WETH | eth_getLogs direct (GeckoTerminal rejected — gaps confirmed) | eth_getLogs via Base RPC | Sync events via eth_getLogs |

### WETH/USDC — Hybrid workflow (Fast Path approved)

1. Fast Path: GeckoTerminal → `candidate_90d.parquet`
2. Stratified sampling (Spike/Flat/Mean by daily variance)
3. Truth Path: The Graph CL subgraph, `id_gt` pagination
4. Audit gate: MAE < 0.10%, Volume Error < 1%, Dropped Candles = 0 → **PASS**
5. TVL: PoolHourData → hourly forward-fill
6. Final: `final_90d.parquet` with TVL populated

### AERO/WETH — Direct eth_getLogs (Fast Path rejected)

1. eth_getLogs: Full 90-day Swap + Sync event pull via Base RPC (2,000-block chunks)
2. Aggregate: `aggregate_classic_swaps()` → 1-minute candles
3. TVL: Sync events → reserve0 × WETH price → hourly forward-fill
4. Final: `final_90d.parquet` written directly — no audit gate (on-chain is ground truth)
5. `audit_log.json`: `overall_verdict: PASS`, `method: eth_getLogs_direct`

### The Graph Pagination (WETH/USDC only)

**Always use `id_gt` cursor pagination. Never use `$skip`.** The `skip` parameter has a hard ceiling of 5,000 records. All three WETH/USDC audit windows exceeded 22,000+ swaps — skip would have silently failed.

---

## 7. Engineering Outputs

### 7.1 Actual Ingestion Results (Phase 1 Complete)

| Pair | Method | Candles | Price Range | TVL Range | Status |
|---|---|---|---|---|---|
| WETH/USDC | GeckoTerminal + The Graph | ~129,600 | $2,071–$2,966 | — | ✅ PASS |
| AERO/WETH | eth_getLogs direct | 33,053 | $0.2447–$0.5984 | $2.3M–$4.6M | ✅ PASS |
| Gas | eth_getBlockByNumber | 129,600 | 0.0005–2.9245 Gwei | — | ✅ PASS |

### 7.2 Final Storage Schema

| Column | Type | Description |
|---|---|---|
| `timestamp` | `Datetime[us, UTC]` | 1-minute bucket start |
| `open` | `Float64` | First swap price in bucket |
| `high` | `Float64` | Highest swap price in bucket |
| `low` | `Float64` | Lowest swap price in bucket |
| `close` | `Float64` | Last swap price in bucket |
| `volume_usd` | `Float64` | USD volume for the bucket |
| `tvl_usd` | `Float64` | Hourly TVL forward-filled to 1-minute |

---

## 8. Audit Gate (TRD v1.5)

| Metric | Target | Why It Matters |
|---|---|---|
| **MAE** | < 0.10% | Price error must stay below minimum signal threshold |
| **Volume Error** | < 1% | Volume feature accuracy |
| **Dropped Candles** | 0 | Real swap activity (volume ≥ $0.01) present in Truth but absent from Fast Path |
| **TVL Error** | null | No Fast Path TVL — always null |
| **Filled Candles** | Info only | Empty minutes forward-filled — logged, not gated |

**Not included:** Pearson correlation — securities-market metric, removed in v1.5.

---

## 9. Definition of Done — Phase 1 COMPLETE ✅

- [x] WETH/USDC: candidate_90d.parquet pulled, audit PASS, TVL merged, final_90d.parquet written
- [x] AERO/WETH: 90-day eth_getLogs pull, 33,053 candles, TVL from Sync events, final_90d.parquet written
- [x] Gas: 129,600 rows, blocks 40,836,070–44,724,040, 0 nulls
- [x] All ingestion scripts committed: fast_path.py, truth_path.py, audit.py, gas.py, aero_weth_pipeline.py, check_subgraph.py
