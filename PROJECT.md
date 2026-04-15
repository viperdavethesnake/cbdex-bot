# PROJECT.md: Base DEX Trading Bot

## 1. Overview

A Python-based automated trading bot targeting decentralized exchanges (DEXs) on the Coinbase Base network (e.g., Aerodrome, Uniswap). The bot executes a strategy using machine learning trained on a sliding window of recent market data.

**Core Philosophy:** Focus on short-term regime adaptation over long-term historical patterns.
**Scope:** Base Network only, spot trading only, single machine. No cross-chain, no leverage, no CEX integration.

---

## 2. Tech Stack

| Category | Choice |
|---|---|
| Language | Python 3.12 |
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

### Phase 0 — Foundation
- [ ] Project repo initialized with structure above.
- [ ] Docker + macvlan environment verified.
- [ ] Connection to Base RPC (Mainnet & Sepolia) established via `web3.py`.
- [ ] Ability to read real-time pool prices and liquidity from a DEX contract.

### Phase 1 — Backtest & Research
- [ ] **Data Ingestion:** Pull 30–90 days of 1m data (OHLCV + Liquidity + Gas) for target pairs.
- [ ] **Feature Engineering:** Create relative volume and volatility indicators using Polars.
- [ ] **Backtest Engine:** Build a "Gas-Aware" simulator (every trade must subtract Base gas fees).
- [ ] **Slippage Modeling:** Implement a "Price Impact" function based on pool depth.
- [ ] **ML Training:** Train model using Walk-Forward validation; outperform simple buy-and-hold after gas/slippage.

### Phase 2 — Paper Trading (Testnet)
- [ ] **Execution Layer:** Abstracted interface to switch between Base Sepolia and Base Mainnet.
- [ ] **Testnet Deployment:** Deploy bot on Base Sepolia using test-ETH.
- [ ] **Latency Check:** Measure time from signal generation to transaction confirmation.
- [ ] **Slippage Tuning:** Verify that `minAmountOut` parameters correctly prevent bad fills.
- [ ] **Overfit Detector:** Compare Testnet performance against Backtest expectations.

### Phase 3 — Live Trading
- [ ] **Wallet Funding:** Fund a dedicated "Hot Wallet" with a strict capital cap.
- [ ] **Risk Limits:** Enable hard-coded caps on position size and daily loss.
- [ ] **Small-Scale Launch:** Start with minimum viable positions ($50–100).
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
