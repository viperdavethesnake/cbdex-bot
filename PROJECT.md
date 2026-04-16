# PROJECT.md: Base DEX Trading Bot

## 1. Overview

A Python-based automated trading bot targeting decentralized exchanges (DEXs) on the Coinbase Base network (e.g., Aerodrome, Uniswap). The bot executes a strategy using machine learning trained on a sliding window of recent market data.

**Core Philosophy:** Focus on short-term regime adaptation over long-term historical patterns.
**Scope:** Base Network only, spot trading only, single machine. No cross-chain, no leverage, no CEX integration.

---

## 2. Tech Stack

| Category | Choice |
|---|---|
| Language | Python 3.13 |
| Blockchain Interface | `web3.py` (JSON-RPC) |
| RPC Provider | Alchemy, QuickNode, or similar (Base Mainnet + Base Sepolia) |
| Data Source | The Graph (GraphQL) or DEX-specific indexers for historicals; RPC for live data |
| Data Handling | `polars` (primary), `pandas`, `numpy` |
| Storage | Parquet files on local NVMe |
| ML | `scikit-learn` (baselines/Random Forest), `PyTorch` (if LSTM/Deep Learning required) |
| Containerization | Docker + docker-compose |
| Networking | macvlan (isolated network stack) |
| Secrets | `.env` (encrypted/mounted); private keys via environment variables — never committed |
| Logging | Structured JSON logs; Grafana/Loki for monitoring |

---

## 3. Repo Structure

```
project/
├── data/           # Historical OHLCV + Liquidity + Gas (gitignored)
├── ingestion/      # RPC/GraphQL scripts to pull and normalize chain data
├── research/       # Notebooks for feature engineering and regime analysis
├── strategies/     # Signal logic, ML model definitions, and regime filters
├── backtest/       # Event-driven engine (gas-aware + slippage-aware)
├── execution/      # Web3 transaction routing (Testnet vs Mainnet)
├── risk/           # Position sizing, slippage caps, and kill-switch
├── infra/          # Dockerfile, docker-compose, RPC configs
├── monitoring/     # Heartbeat, balance tracking, alert system
├── tests/          # Unit tests for smart contract interactions
├── .env.example
└── PROJECT.md
```

---

## 4. Strategy Approach

**Model:** Hybrid Adaptive Strategy.

- **Feature Set:** 1-minute OHLCV + Pool Liquidity + Base Network Gas Price + Relative Volume.
- **Training Window:** 30–90 days of historical data.
- **Validation:** Walk-Forward Validation — Train on 60 days → Validate on 7 days → Slide window.
- **Logic:** Classical momentum/mean-reversion signals tuned by an ML model that identifies the current Market Regime (Bull, Bear, or Chop).

---

## 5. Phases and Milestones

### Phase 0 — Foundation ✅
- [x] Project repo initialized with structure above.
- [x] Docker + macvlan environment verified.
- [x] Connection to Base RPC (Mainnet) established via `web3.py`.
- [x] Ability to read real-time pool prices and liquidity from a DEX contract.

### Phase 1 — Data Ingestion ✅
- [x] **Data Ingestion:** 90 days of 1-min OHLCV + Liquidity + Gas for WETH/USDC and AERO/WETH.
- [x] **WETH/USDC:** The Graph CL subgraph — 126,000 rows, audit PASS (MAE < 0.10%, Volume Error < 1%).
- [x] **AERO/WETH:** eth_getLogs direct (GeckoTerminal gaps confirmed; The Graph GEN is CL-only) — 33,053 rows.
- [x] **Gas data:** 129,600 rows, `baseFeePerGas` via Alchemy.

### Phase 2 — ML Model + Paper Trading 🟢
- [x] **Feature Engineering:** 21-feature set — momentum, volatility, relative volume, range position, TVL, gas, time, gap features.
- [x] **Backtest Engine:** Gas-aware + slippage-aware event-driven simulator (`backtest/simulator.py`).
- [x] **ML Training:** Walk-forward RF on AERO/WETH — precision 0.600, ann. ROI +5.8% after costs.
- [x] **WETH/USDC ML:** Precision 0.238 at 1-min — not viable. Shelved for 5/15-min future investigation.
- [x] **Paper Trading:** `execution/paper_trader.py` running live on mainnet data (no real funds).
  - 1-bar hold strategy, regime filter (vol_15 ≥ 0.0098), signal threshold 0.70
  - JSONL logging: signal events + close events with realized PnL
  - Kill switch (`.kill` file), daily loss limit ($50), stale data gate
- [ ] **Validation:** Accumulate 1–2 weeks of paper trade data before live deployment.

### Phase 3 — Live Trading
- [ ] **Wallet Funding:** Fund a dedicated "Hot Wallet" with a strict capital cap.
- [ ] **Risk Limits:** Hard-coded caps on position size and daily loss (already implemented in paper trader).
- [ ] **Small-Scale Launch:** Start with minimum viable positions ($50).
- [ ] **Scaling:** Increase capital only after 30 days of consistent performance.

---

## 6. Risk Limits (Non-Negotiable)

- **Private Key Security:** Bot holds only a "Hot Wallet" with limited funds; main capital stays in a Cold Wallet.
- **Slippage Cap:** Hard limit on `max_slippage` (e.g., 0.5%–1%). Transactions must revert if exceeded.
- **Gas Ceiling:** Bot halts if Base network gas prices exceed a predefined threshold (to prevent fee-bleeding).
- **Max Exposure:** Hard cap on the percentage of wallet deployed in a single pair.
- **Daily Stop-Loss:** Bot halts all trading for 24h if total wallet value drops by X%.
- **Kill Switch:** Single command to cancel all pending transactions and halt the container.

---

## 7. Out of Scope

- Other networks (Solana, Ethereum L1, Arbitrum, etc.)
- Leverage, margin, or lending protocols
- Automated liquidity providing (LPing)
- Web UI or mobile dashboard
- Multi-sig integration (until Phase 4)

---

## 8. Open Questions for Implementation

1. **Primary DEX:** Which pool has the highest liquidity for target pairs (Aerodrome vs. Uniswap v3)?
2. **RPC Provider:** Which provider offers the lowest latency for Base?
3. **Feature Importance:** Does Gas Price actually correlate with price volatility on Base? (To be determined in Phase 1.)
4. **Slippage Strategy:** Should the bot use a fixed slippage % or dynamic slippage based on current pool volatility?
