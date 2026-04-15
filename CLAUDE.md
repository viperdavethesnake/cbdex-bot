# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**cbdex-bot** is a Python 3.12 automated trading bot targeting Aerodrome Finance DEX pools on Base mainnet (Chain ID: 8453). It collects historical market data, trains an ML model on regime-classified signals, and executes trades directly via the Aerodrome Router contract using web3.py.

**Current phase:** Phase 1 — Data ingestion pipeline (TRD v1.4).

## Environment

**Python:** 3.13 (system default — `python3.13` or `python3`). Python 3.12 is not installed.
**Venv:** `.venv/` — activate with `source .venv/bin/activate`.
**Packages:** Do not `pip install` anything without explicit user approval. Use what is already in the venv.

```bash
# .env (copy from .env.example)
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<YOUR_KEY>
THEGRAPH_API_KEY=<YOUR_KEY>
# GeckoTerminal requires no API key
```

## Running the Pipeline

```bash
# Step 0: Verify subgraph is synced (required before any Truth Path work)
python ingestion/check_subgraph.py

# Step 1: Smoke test (always run before full pull)
python ingestion/smoke_test.py --pair WETH_USDC --days 7

# Step 2: Full 90-day ingestion
python ingestion/run_pipeline.py --pair WETH_USDC --days 90
python ingestion/run_pipeline.py --pair AERO_WETH --days 90
```

## Architecture

### Data Pipeline (Hybrid Ingestion Workflow)

```
GeckoTerminal ──► fast_path.py ──► candidate_90d.parquet
                                         │
The Graph ──────► truth_path.py ──► aggregator.py ──► Audit Gate ──► final_90d.parquet
                                         │                │
Alchemy RPC ────► gas.py ────────────────┘          PASS / FAIL
                                                         │
                                              FAIL: full Truth Path pull (8–20h)
```

**Fast Path** (GeckoTerminal, ~5 min): Pulls 90 days of 1-minute OHLCV candles for both pairs. No TVL available — `tvl_usd` column is `null` in the candidate file.

**Truth Path** (The Graph + Alchemy): Pulls raw on-chain `Swap` events for 3 stratified windows (Spike / Flat / Mean), aggregates to 1-minute candles via Polars, and always pulls TVL from `PoolHourData` (hourly, forward-filled to 1-minute).

**Audit Gate**: Compares Fast Path vs. Truth Path candles. All windows must pass — ρ > 0.999, MAE < 0.10%, Volume Error < 1%, zero dropped candles. A PASS approves the fast-path OHLCV. A FAIL triggers a full 90-day Truth Path pull.

**Gas Pull** (Alchemy): `baseFeePerGas` sampled every 30 blocks (~1-min resolution) via `eth_getBlockByNumber`. Independent of OHLCV pipeline — can run concurrently.

### Target Pools (LOCKED — do not substitute without TRD revision)

| Pair | Pool Address | Type | Swap Fee | Min Signal |
|---|---|---|---|---|
| WETH/USDC | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL Slipstream (tick 100) | 0.05% | >0.12% |
| AERO/WETH | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic vAMM (x\*y=k) | 0.30% | >0.65% |

**Aerodrome Router:** `0xcF77a3Ba9A5CA399B7c97c74d94E92359DC59`

### Pool Type Branching (Critical)

The two pools have fundamentally different on-chain schemas. All Truth Path code must branch on pool type.

**WETH/USDC (CL):** Price comes from `sqrtPriceX96`:
```python
price = (sqrtPriceX96 / 2**96) ** 2 * (10**18) / (10**6)
```
Subgraph fields: `amount0`, `amount1`, `sqrtPriceX96`, `tick`, `amountUSD`

**AERO/WETH (Classic vAMM):** Price comes from reserve ratio:
```python
price = amount1In / amount0Out   # selling token0
price = amount1Out / amount0In   # buying token0
```
Subgraph fields: `amount0In`, `amount0Out`, `amount1In`, `amount1Out`, `amountUSD`

Always run schema introspection before writing production Truth Path queries:
```graphql
{ __type(name: "Swap") { fields { name type { name kind } } } }
```

### The Graph Pagination (Critical)

**Always use `id_gt` cursor pagination. Never use `$skip`.** The `skip` parameter has a hard ceiling of 5,000 records and silently truncates high-volume pairs.

```python
last_id = ""
while True:
    result = run_query(query, variables={..., "lastId": last_id})
    swaps = result["data"]["swaps"]
    if not swaps:
        break
    all_swaps.extend(swaps)
    last_id = swaps[-1]["id"]
    time.sleep(0.25)
```

Subgraph IDs:
- Classic (AERO/WETH): `GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM` — verify sync before use
- CL (WETH/USDC): `nZnftbmERiB2tY6t2ika7kP9srTcKnYFEnqG3RKa38r`

### Storage Schema

**Parquet files** (columnar, `float64` for all numerics, UTC timestamps):

```
data/base_mainnet/
├── pairs/
│   ├── WETH_USDC/
│   │   ├── candidate_90d.parquet    # Fast Path OHLCV (tvl_usd = null)
│   │   ├── final_90d.parquet        # Approved + TVL populated
│   │   └── audit_log.json
│   └── AERO_WETH/
│       └── ...
└── network/
    └── gas_prices_90d.parquet       # block_number, timestamp, base_fee_gwei
```

OHLCV schema: `timestamp (Datetime[us,UTC])`, `open`, `high`, `low`, `close`, `volume_usd`, `tvl_usd` — all `Float64`. Max 129,600 rows (90 days × 1,440 min).

### Execution Path (Production)

Production trades call the Aerodrome Router directly via web3.py. **Coinbase Wallet / UI must not be used** — it routes through 0x + 1inch and adds ~1% service fee per swap, making round-trip costs ~2.1–2.6% (far above minimum signal thresholds).

### ML Strategy (Phase 1+)

- **Label:** Log return `ln(P_t+1 / P_t)` at t+1 (next 1-minute candle)
- **Features:** 1-min OHLCV, pool TVL, Base network gas price, relative volume
- **Training:** Walk-forward validation — 60-day train → 7-day validate → slide window
- **Regime filter for AERO/WETH:** Execute only when realized 1-min volatility exceeds 1.5× fee hurdle (~0.98%/min). Hold during quiet periods.

## Rate Limits

| Source | Limit | Sleep |
|---|---|---|
| GeckoTerminal | 30 req/min | 2.1s between calls; 4s on 429 |
| The Graph | 100K queries/month free | 0.25s between pages |
| Alchemy (Base RPC) | 30M CU/month free | 50ms (20 req/sec) |

## Key Documents

| File | Purpose |
|---|---|
| `TRD_v1.4.md` | Authoritative data specification — **do not modify** |
| `ARCHITECTURE.md` | System design and data flow diagrams |
| `IMPLEMENTATION_GUIDE.md` | Step-by-step code for all pipeline phases |
| `API_REFERENCE.md` | Verified API endpoints, schemas, and pagination patterns |
| `POOL_RESEARCH_FINDINGS.md` | Pool selection rationale and volatility profile |
| `PROJECT.md` | Full bot roadmap (Phases 0–3) |
