# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**cbdex-bot** is a Python trading bot targeting Aerodrome Finance DEX pools on Base mainnet (Chain ID: 8453). It collects historical market data, trains an ML model on regime-classified signals, and executes swaps directly via the Aerodrome Router smart contract using web3.py.

**Current phase:** Phase 2 — Paper Trading active on AERO/WETH. Phases 1 (Data) and 2 (ML Model) are COMPLETE.

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

## Architecture

### Data Pipeline — COMPLETE

```
WETH/USDC:  GeckoTerminal → fast_path.py → candidate_90d.parquet
                                 │
            The Graph ──────► truth_path.py → audit.py (PASS) → final_90d.parquet
                                                    │
            TVL: PoolHourData (The Graph) ──────────┘

AERO/WETH:  eth_getLogs (Base RPC) → aero_weth_pipeline.py → final_90d.parquet
            TVL: Sync events → reserve0 × WETH price

Gas:        eth_getBlockByNumber → gas.py → gas_prices_90d.parquet
```

### Target Pools (LOCKED — do not substitute without TRD revision)

| Pair | Pool Address | Type | Fee | Min Signal | Candles |
|---|---|---|---|---|---|
| WETH/USDC | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL Slipstream (tick 100) | 0.05% | >0.12% | ~129,600 (dense) |
| AERO/WETH | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic vAMM (x\*y=k) | 0.30% | >0.65% | ~33,053 (sparse) |

**Aerodrome Router (verified on BaseScan):** `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`

### Critical: Per-Pair Ingestion Methods

**WETH/USDC:** The Graph CL subgraph (`nZnftbmERiB2tY6t2ika7kP9srTcKnYFEnqG3RKa38r`) — use `truth_path.py`.

**AERO/WETH:** eth_getLogs via Base RPC — use `aero_weth_pipeline.py`. The Graph GEN subgraph (`GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM`) indexes CL (Slipstream) pools ONLY — it returns null for Classic vAMM pools. Do NOT attempt to query Classic vAMM pools via The Graph. GeckoTerminal also rejected for this pair — it has confirmed coverage gaps (sparse pair). eth_getLogs is both the correct and complete source.

### Pool Type Branching (Critical)

**WETH/USDC (CL):** Price from `sqrtPriceX96`:
```python
price = (sqrtPriceX96 / 2**96) ** 2 * (10**18) / (10**6)
```

**AERO/WETH (Classic vAMM):** Token ordering: token0=WETH, token1=AERO. Price from swap amounts:
```python
price_weth_per_aero = amount0In / amount1Out   # selling AERO
price_weth_per_aero = amount0Out / amount1In   # buying AERO
```
Multiply by WETH/USD price (from WETH/USDC `final_90d.parquet`) for USD/AERO.

### The Graph Pagination (WETH/USDC only)

**Always use `id_gt` cursor pagination. Never use `$skip`.** Hard ceiling of 5,000 records — all audit windows exceeded 22,000+ swaps.

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

### Audit Gate (TRD v1.5 — applies to WETH/USDC only)

| Metric | Target |
|---|---|
| MAE | < 0.10% |
| Volume Error | < 1% |
| Dropped Candles | 0 (zero-volume ghost candles excluded — volume ≥ $0.01 threshold) |

Pearson correlation: **not used** — removed in TRD v1.5.

### AERO/WETH Regime Filter

Execute only when realized 1-min volatility exceeds 1.5× fee hurdle (~0.98%/min). AERO/WETH is active ~25% of minutes — the regime filter gates out the quiet 75%.

### Storage Schema

```
data/base_mainnet/
├── pairs/
│   ├── WETH_USDC/
│   │   ├── candidate_90d.parquet    # Fast Path OHLCV (tvl_usd = null)
│   │   ├── final_90d.parquet        # Approved + TVL populated (~129,600 rows)
│   │   └── audit_log.json           # PASS, trd_version: v1.5
│   └── AERO_WETH/
│       ├── candidate_90d.parquet    # GeckoTerminal (rejected — gaps confirmed)
│       ├── final_90d.parquet        # eth_getLogs direct (33,053 rows)
│       └── audit_log.json           # PASS, method: eth_getLogs_direct
└── network/
    └── gas_prices_90d.parquet       # 129,600 rows, 0.0005–2.9245 Gwei
```

### Execution Path (Production)

Production swaps call the Aerodrome Router directly via web3.py. **Coinbase Wallet / UI must not be used** — it routes through 0x + 1inch and adds ~1% service fee per swap.

### ML Strategy (Phase 2)

- **Label:** Log return `ln(P_t+1 / P_t)` at t+1
- **Features:** 21 features — 1-min OHLCV, pool TVL, gas price, relative volume, time, gap
- **Training:** Walk-forward — 60-day train → 7-day validate → 4 folds
- **Evaluation metrics:** precision, F1-macro (LONG/SHORT only), per-trade Sharpe (annualised at 52 folds/year)
- **Backtest cost model:** pool fee (round-trip) + gas $0.02 + slippage + latency 10bps each way
- **AERO/WETH regime filter:** vol_15 ≥ 0.0098 (applied per fold after split — no leakage)
- **Model saved:** `models/aero_weth_rf.pkl` — Random Forest, threshold 0.70, precision 0.600, ann. ROI +5.8%
- **Retrain:** `strategies/model.py train_final_model()` trains on most recent 60 days and saves pkl
- **WETH/USDC:** Not viable at 1-min resolution (precision 0.238). Shelved for future 5/15-min investigation.

## Paper Trading (Phase 2 — Active)

Paper trader runs as a background OS process, **survives session disconnects but not server reboots**.

```bash
# Check it's alive
pgrep -fa "paper_trader"

# Tail recent signals and closes
tail -20 logs/paper_trades.jsonl | python3 -m json.tool

# Restart if dead
source .venv/bin/activate
PYTHONPATH=. python3 execution/paper_trader.py >> logs/paper_trader.log 2>&1 &
```

**JSONL record types:**
- `"event": "signal"` — model evaluated; includes `signal`, `p_long`, `p_short`, `vol_15`, `ret_1`, `close`, `data_age_min`
- `"event": "close"` — 1-bar position closed; includes `direction`, `entry_price`, `exit_price`, `label_raw`, `pnl_net_usd`, `cumulative_pnl_usd`

**Live feature pipeline (`execution/live_features.py`):**
- AERO/WETH OHLCV: GeckoTerminal (65 candles)
- WETH/USD price: GeckoTerminal WETH/USDC pool (`fetch_weth_usd`) — used to compute TVL
- Gas: `baseFeePerGas` via Alchemy RPC
- TVL: most recent Sync event reserves via `eth_getLogs` on `mainnet.base.org`
- Staleness detection: warns when latest candle > 5 min old; signals blocked, closes still allowed
- `candle_ts` returned in feature dict — paper trader uses it to guard against $0 closes on frozen candles

**Known limitation:** `tvl_norm` is always 1.0 in live inference because TVL is set as a scalar constant across the 65-candle window (rolling_mean of a constant = the constant). Real fix requires fetching a 60-candle historical TVL series via eth_getLogs. Low priority for paper trading phase.

## Data Refresh

Training data expires after 90 days. Refresh weekly with:

```bash
source .venv/bin/activate
PYTHONPATH=. python3 scripts/refresh_data_and_model.py
# Flags: --skip-gas  --skip-weth  --eval-only
```

Steps: gas pull (~110 min) → WETH/USDC via GeckoTerminal (~10 min) → AERO/WETH via eth_getLogs (~45 min) → walk-forward eval → save model pkl. Logs to `logs/refresh.log`.

Systemd timer (`infra/cbdex-refresh.service` + `cbdex-refresh.timer`) is in the repo but **not installed** — run manually for now.

## Tests

```bash
PYTHONPATH=. python3 -m unittest tests.test_simulator tests.test_live_features -v
```

- `tests/test_simulator.py` — 13 unit tests: LONG/SHORT PnL math, fees, gas, latency, precision, summary keys
- `tests/test_live_features.py` — 10 unit tests: feature column parity with `FEATURE_COLS_AERO`, tvl_norm fallback, ret_1 correctness (API calls mocked)

## Infrastructure

`infra/cbdex-paper-trader.service` and `infra/cbdex-refresh.timer` are committed but **not installed**. Install with:
```bash
sudo cp infra/*.service infra/*.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cbdex-paper-trader cbdex-refresh.timer
```

## Rate Limits

| Source | Limit | Sleep |
|---|---|---|
| GeckoTerminal | 30 req/min | 2.1s between calls; 4s on 429 |
| The Graph | 100K queries/month free | 0.25s between pages |
| Alchemy (Base RPC) | 30M CU/month free | 50ms (20 req/sec) |
| mainnet.base.org | 2,000 block range per eth_getLogs | — |

## Key Documents

| File | Purpose |
|---|---|
| `TRD_v1.5.md` | Authoritative data specification — **do not modify** |
| `ARCHITECTURE.md` | System design and data flow diagrams |
| `IMPLEMENTATION_GUIDE.md` | Step-by-step code for all pipeline phases |
| `API_REFERENCE.md` | Verified API endpoints, schemas, and pagination patterns |
| `POOL_RESEARCH_FINDINGS.md` | Pool selection rationale and volatility profile |
| `PROJECT.md` | Full bot roadmap (Phases 0–3) |
