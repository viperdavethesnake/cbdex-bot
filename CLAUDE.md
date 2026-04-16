# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working in this repository.

## Project

**cbdex-bot** is a Python trading bot targeting Aerodrome Finance DEX pools on Base mainnet (Chain ID: 8453). Executes swaps directly via the Aerodrome Router smart contract using web3.py.

**Current phase:** Phase 2 — Feature Engineering and Backtesting. Phase 1 Data Collection is COMPLETE.

## Environment

**Python:** 3.13 (system default). Python 3.12 is not installed.  
**Venv:** `.venv/` — activate with `source .venv/bin/activate`.  
**Packages:** Do not `pip install` anything without explicit user approval.

```bash
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<YOUR_KEY>
THEGRAPH_API_KEY=<YOUR_KEY>
# GeckoTerminal: no API key required
```

## Target Pools (LOCKED)

| Pair | Pool Address | Type | Fee | Min Signal | Candles |
|---|---|---|---|---|---|
| WETH/USDC | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL Slipstream (tick 100) | 0.05% | >0.12% | ~129,600 (dense) |
| AERO/WETH | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic vAMM (x\*y=k) | 0.30% | >0.65% | ~33,053 (sparse) |

**Aerodrome Router (verified on BaseScan):** `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`

## Critical: Per-Pair Ingestion Methods

**WETH/USDC:** The Graph CL subgraph (`nZnftbmERiB2tY6t2ika7kP9srTcKnYFEnqG3RKa38r`) via `truth_path.py`.

**AERO/WETH:** eth_getLogs via Base RPC via `aero_weth_pipeline.py`.
- The Graph GEN subgraph indexes CL (Slipstream) pools ONLY. Querying Classic vAMM pool `0x7f670f78...` returns null. Do NOT attempt The Graph for Classic vAMM pools.
- GeckoTerminal rejected — confirmed coverage gaps on this sparse pair.
- eth_getLogs is the correct and complete source for AERO/WETH.

## Pool Type Branching

**WETH/USDC (CL):** Price from `sqrtPriceX96`:
```python
price = (sqrtPriceX96 / 2**96) ** 2 * (10**18) / (10**6)
```

**AERO/WETH (Classic vAMM):** Token ordering: token0=WETH, token1=AERO.
```python
price_weth_per_aero = amount0In / amount1Out   # selling AERO
price_weth_per_aero = amount0Out / amount1In   # buying AERO
```
Multiply by WETH/USD price from WETH/USDC `final_90d.parquet` for USD/AERO.

## The Graph Pagination (WETH/USDC only)

**Always use `id_gt`. Never use `$skip`** — hard ceiling of 5,000 records.

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
- CL (WETH/USDC): `nZnftbmERiB2tY6t2ika7kP9srTcKnYFEnqG3RKa38r`
- Classic (AERO/WETH): **Does not exist** — use eth_getLogs

## Audit Gate (TRD v1.5 — WETH/USDC only)

| Metric | Target |
|---|---|
| MAE | < 0.10% |
| Volume Error | < 1% |
| Dropped Candles | 0 (volume ≥ $0.01 threshold; zero-volume ghost candles excluded) |

Pearson correlation: **not used** — removed in TRD v1.5.

## Data — Phase 1 Complete

```
data/base_mainnet/
├── pairs/
│   ├── WETH_USDC/
│   │   ├── candidate_90d.parquet    # GeckoTerminal Fast Path (tvl_usd = null)
│   │   ├── final_90d.parquet        # Approved + TVL (~129,600 rows)
│   │   └── audit_log.json           # PASS, trd_version: v1.5
│   └── AERO_WETH/
│       ├── candidate_90d.parquet    # GeckoTerminal (rejected — gaps confirmed)
│       ├── final_90d.parquet        # eth_getLogs direct (33,053 rows)
│       └── audit_log.json           # PASS, method: eth_getLogs_direct
└── network/
    └── gas_prices_90d.parquet       # 129,600 rows, 0.0005–2.9245 Gwei
```

## Execution Path (Production)

Direct Aerodrome Router via web3.py. **Coinbase UI must not be used** — ~1% service fee makes round-trip costs ~2.1–2.6%, far above minimum signal thresholds.

## ML Strategy (Phase 2)

- **Label:** Log return `ln(P_t+1 / P_t)` at t+1
- **Features:** 1-min OHLCV, pool TVL, gas price, relative volume
- **Training:** Walk-forward — 60-day train → 7-day validate → slide
- **AERO/WETH regime filter:** Execute only when realized 1-min volatility > 1.5× fee hurdle (~0.98%/min). Active ~25% of minutes.

## Rate Limits

| Source | Limit | Sleep |
|---|---|---|
| GeckoTerminal | 30 req/min | 2.1s between calls |
| The Graph | 100K queries/month | 0.25s between pages |
| Alchemy (Base RPC) | 30M CU/month | 50ms (20 req/sec) |
| mainnet.base.org | 2,000 block range per eth_getLogs | — |

## Key Documents

| File | Purpose |
|---|---|
| `TRD_v1.5.md` | Authoritative data specification |
| `ARCHITECTURE.md` | System design and data flow |
| `IMPLEMENTATION_GUIDE.md` | Step-by-step pipeline code |
| `API_REFERENCE.md` | Verified API endpoints and schemas |
| `POOL_RESEARCH_FINDINGS.md` | Pool selection rationale |
| `PROJECT.md` | Full bot roadmap |
