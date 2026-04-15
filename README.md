# Base DEX Data Acquisition Layer

A high-fidelity historical data pipeline for Base Network DEX pairs. Collects, verifies, and stores 1-minute OHLCV, Liquidity (TVL), and Network Gas data for use as ML model training inputs.

---

## Overview

This pipeline implements a **Hybrid Ingestion Workflow**: a fast bulk download path verified against on-chain ground truth before any data is approved for model training. No dataset reaches the training phase without passing a quantitative audit gate.

**Specification:** [TRD v1.5 — Base DEX Data Acquisition Layer](./TRD_v1.5.md)

---

## Target Pairs — Locked

| Pair | Pool Address | Type | Fee |
|---|---|---|---|
| **WETH/USDC** | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL Slipstream (tick 100) | 0.05% |
| **AERO/WETH** | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic vAMM | 0.30% |

Both pools are on Aerodrome Finance, Base mainnet (Chain ID: 8453).

---

## System Requirements

| Requirement | Detail |
|---|---|
| **Python** | 3.12 |
| **Storage** | Local SSD/NVMe (recommended) |
| **Format** | Apache Parquet |
| **RPC Provider** | Alchemy (free tier sufficient — Base mainnet) |

### Dependencies

```
web3.py     # RPC interaction (Truth Path, Gas data, trade execution)
polars      # High-performance aggregation and binning
httpx       # API requests
```

---

## Quick Setup

```bash
# 1. Clone the repository
git clone <repo-url>
cd cbdex-bot

# 2. Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env with your RPC URL and API keys
```

### Required Environment Variables

```bash
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<YOUR_KEY>
THEGRAPH_API_KEY=<YOUR_KEY>
```

> **Note:** GeckoTerminal (Fast Path) requires no API key.

---

## Critical Architecture Notes

### Fast Path: GeckoTerminal Only
DexScreener does not provide historical OHLCV data at any resolution. **GeckoTerminal is the sole Fast Path source.** It provides 1-minute candles up to 6 months back, free tier, no authentication required.

### TVL: Always From Truth Path
GeckoTerminal provides no historical TVL. The Graph provides TVL at hourly resolution only. The `tvl_usd` column in all final datasets represents **hourly TVL forward-filled to 1-minute buckets**. This is expected behavior.

### Execution: Aerodrome Router Directly
Production swaps must call the Aerodrome Router (`0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`, verified on BaseScan) directly via web3.py. Coinbase Wallet adds ~1% service fee per swap on top of pool fees, making round-trip costs ~2.1–2.6% — far exceeding the minimum signal threshold.

### Two Different Schemas
WETH/USDC (CL pool) and AERO/WETH (Classic vAMM pool) use different on-chain event structures and subgraph schemas. Both must be implemented separately. See [ARCHITECTURE.md](./ARCHITECTURE.md).

---

## Directory Structure

```
cbdex-bot/
├── README.md                       ← You are here
├── PROJECT.md                      ← High-level bot overview
├── TRD_v1.5.md                     ← Authoritative data spec (read-only)
├── ARCHITECTURE.md                 ← System design and data flow
├── IMPLEMENTATION_GUIDE.md         ← Step-by-step developer instructions
├── API_REFERENCE.md                ← Verified API endpoints and schemas
├── POOL_RESEARCH_FINDINGS.md       ← Pool verification and volatility research
├── data/
│   └── base_mainnet/
│       ├── pairs/
│       │   ├── WETH_USDC/
│       │   │   ├── candidate_90d.parquet   ← Fast Path (tvl_usd = null)
│       │   │   ├── final_90d.parquet       ← Approved + TVL populated
│       │   │   └── audit_log.json
│       │   └── AERO_WETH/
│       │       └── ...
│       └── network/
│           └── gas_prices_90d.parquet
├── ingestion/
│   ├── fast_path.py                ← GeckoTerminal OHLCV pull
│   ├── truth_path.py               ← The Graph swap + TVL pull
│   └── gas.py                      ← baseFeePerGas collection
├── strategies/
├── execution/
├── risk/
├── backtest/
├── monitoring/
├── tests/
├── research/                       ← Archived research docs
└── infra/
```

---

## Running the Pipeline

### Step 0: Subgraph Sync Check (Required First)
```bash
python ingestion/check_subgraph.py
```

### Step 1: Smoke Test (Always run before full pull)
```bash
python ingestion/smoke_test.py --pair WETH_USDC --days 7
```

### Step 2: Full Pipeline
```bash
python ingestion/run_pipeline.py --pair WETH_USDC --days 90
python ingestion/run_pipeline.py --pair AERO_WETH --days 90
```

---

## Audit Gate (TRD v1.5)

Three metrics determine whether GeckoTerminal Fast Path data is approved for ML training:

| Metric | Target | What It Catches |
|---|---|---|
| MAE | < 0.10% | Price error vs on-chain ground truth |
| Volume Error | < 1% | Aggregation methodology differences |
| Dropped Candles | 0 | Missing swap activity (zero-volume ghost candles excluded) |

---

## Minimum Signal Thresholds (Production)

| Pair | Round-Trip Fee | Buffer | Min Signal |
|---|---|---|---|
| WETH/USDC | 0.10% | 0.02% | > 0.12% |
| AERO/WETH | 0.60% | 0.05% | > 0.65% |

These thresholds assume **direct Aerodrome Router execution**. Coinbase UI adds ~2% additional round-trip cost and must not be used for production.

---

## Reference Documents

| Document | Purpose |
|---|---|
| [TRD v1.5](./TRD_v1.5.md) | Authoritative data specification — do not modify |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | System design and data flow |
| [IMPLEMENTATION_GUIDE.md](./IMPLEMENTATION_GUIDE.md) | Developer instructions |
| [API_REFERENCE.md](./API_REFERENCE.md) | Verified API endpoints and schemas |
| [POOL_RESEARCH_FINDINGS.md](./POOL_RESEARCH_FINDINGS.md) | Pool verification and volatility profile |
| [PROJECT.md](./PROJECT.md) | High-level bot architecture overview |
