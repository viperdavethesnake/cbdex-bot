# Audit Prompt: DEX Architecture Review — Securities Contamination Check

**Document Type:** Agent Audit Brief  
**Date:** 2026-04-14  
**Purpose:** Conduct a full review of all project documentation and flag any concepts, metrics, terminology, or assumptions that were imported from traditional securities/equities markets and are inappropriate for a DEX on-chain trading system. This is not a DEX-to-CEX comparison — this is a pure on-chain DEX bot trading AMM pools directly via smart contract interaction.

---

## Project Context

This is a Python bot that:
- Trades directly on Aerodrome Finance AMM pools on Base mainnet
- Executes swaps by calling the Aerodrome Router smart contract via web3.py
- Targets two specific on-chain liquidity pools (WETH/USDC CL, AERO/WETH Classic vAMM)
- Uses 1-minute OHLCV data derived from on-chain swap events
- Sub-$100 trade sizes
- No order book. No bid/ask spread in the traditional sense. No market makers. No clearing house. No exchange. No broker. No custody.

Price discovery happens entirely through AMM swap mechanics (constant product formula or concentrated liquidity tick math). Every "trade" is a smart contract call that changes pool reserves and moves the price along the bonding curve.

---

## Files to Audit

Review each of the following files in full:

1. `TRD_v1.5.md` — Technical Requirement Document (authoritative spec)
2. `ARCHITECTURE.md` — System design (still references TRD v1.4 — note this)
3. `IMPLEMENTATION_GUIDE.md` — Step-by-step code spec
4. `CLAUDE.md` — Claude Code context file (still references TRD v1.4 and Pearson correlation)

---

## What to Look For

### Category 1: Inappropriate Metrics
Flag any metric that was designed for securities markets and does not directly answer "will this data produce correct trading signals on a DEX?"

**Known confirmed issue (already fixed in TRD v1.5):**
- Pearson correlation (ρ > 0.999) — removed from TRD v1.5 but still present in IMPLEMENTATION_GUIDE.md Phase 5 code and CLAUDE.md audit gate description. These must be updated.

**Check for any others, including but not limited to:**
- Sharpe ratio, Sortino ratio, alpha, beta — securities portfolio metrics
- Bid-ask spread analysis (AMMs don't have an order book)
- Market microstructure concepts (price impact models from securities lit)
- Value at Risk (VaR) — securities risk model
- Any reference to "securities", "equities", "stocks", "bonds"
- Any reference to "exchange" meaning a CEX
- Any reference to "order book" as a feature or data source

### Category 2: Wrong Assumptions About How DEX Pricing Works
Flag any assumption that misunderstands AMM price mechanics:

- Assuming price is set by supply/demand matching (it's set by the AMM formula)
- Assuming there is a "bid" and "ask" (there isn't — there's a bonding curve)
- Assuming slippage works like a securities market (it's pool-depth dependent, not order-flow dependent)
- Any reference to "market depth" as a bid/ask concept (TVL/pool reserves is the correct DEX concept)
- Any reference to "liquidity providers" as market makers in the traditional sense

### Category 3: Wrong Assumptions About What the Data Represents
Flag any statement that misunderstands on-chain OHLCV:

- Treating candles as if they represent a centralized exchange feed
- Assuming OHLCV "open" means the first price of the day in a traditional sense (it's the first swap price in the 1-minute bucket)
- Any reference to "closing price" as if there's a market close (DEX trades 24/7, no close)
- Assuming volume in USD means the same thing as CEX volume

### Category 4: Wrong Terminology
Flag terminology borrowed from securities markets that should use DEX-native language:

| Securities Term | Correct DEX Term |
|---|---|
| "Buy/Sell" | "Swap token0 for token1" or "Swap token1 for token0" |
| "Market maker" | "Liquidity provider (LP)" |
| "Order book depth" | "Pool TVL / reserves" |
| "Spread" | "Price impact" (function of swap size vs pool depth) |
| "Exchange" | "DEX" or "AMM pool" |
| "Short selling" | Not applicable — DEX swaps are directional only |

Note: "Buy" and "Sell" are acceptable shorthand in context (buying WETH = swapping USDC for WETH). Flag only where the securities-specific meaning would cause confusion or incorrect implementation.

### Category 5: Inconsistencies Between Documents
Flag any place where documents contradict each other, particularly:

- ARCHITECTURE.md and IMPLEMENTATION_GUIDE.md still reference TRD v1.4 — should reference v1.5
- CLAUDE.md still references Pearson correlation in the audit gate description
- CLAUDE.md still says TRD v1.4 is authoritative — should be v1.5
- Any metric threshold that differs between documents
- Any pool address or contract address that differs between documents

---

## Required Output Format

```markdown
# DEX Architecture Audit — Securities Contamination Report

**Audit Date:** [date]
**Files Reviewed:** TRD_v1.5.md, ARCHITECTURE.md, IMPLEMENTATION_GUIDE.md, CLAUDE.md
**Overall Status:** CLEAN / ISSUES FOUND

---

## Category 1: Inappropriate Metrics

### Confirmed Issues
| File | Section | Issue | Recommended Fix |
|---|---|---|---|
| IMPLEMENTATION_GUIDE.md | Phase 5 | Pearson correlation still in code | Remove from calculate_window_metrics() and THRESHOLDS dict |
| CLAUDE.md | Audit Gate | ρ > 0.999 still listed | Update to reflect TRD v1.5 gate (MAE, Volume Error, Dropped Candles only) |
| [any others found] | ... | ... | ... |

### No Issues Found
[List any items that were checked and are clean]

---

## Category 2: Wrong DEX Pricing Assumptions

### Issues Found
[List with file, section, issue, fix]

### No Issues Found
[List items checked and clean]

---

## Category 3: Wrong Data Representation Assumptions

### Issues Found
[List]

### No Issues Found
[List]

---

## Category 4: Wrong Terminology

### Issues Found
[List — only flag where it would cause implementation confusion, not casual shorthand]

### No Issues Found
[List]

---

## Category 5: Document Inconsistencies

### Confirmed Inconsistencies
| File | Issue | Fix Required |
|---|---|---|
| ARCHITECTURE.md | References TRD v1.4 in header | Update to v1.5 |
| CLAUDE.md | References TRD v1.4 as authoritative | Update to v1.5 |
| IMPLEMENTATION_GUIDE.md | References TRD v1.4 in header | Update to v1.5 |
| [any others] | ... | ... |

---

## Priority Fix List

Ordered by impact — fix these first:

1. [Highest impact issue]
2. ...

## Items That Are Correct and DEX-Native
[List concepts/metrics/terminology that are correctly framed for a DEX context — confirm these are right so we know what to preserve]

## Summary
[Overall assessment: how contaminated is the stack? What is the risk if unfixed?]
```

---

## What Happens After the Audit

Findings will be used to:
1. Update IMPLEMENTATION_GUIDE.md to remove Pearson correlation from Phase 5 code
2. Update CLAUDE.md to reference TRD v1.5 and remove the old audit gate description
3. Update ARCHITECTURE.md to reference TRD v1.5
4. Fix any other contamination found
5. Ensure all documents are internally consistent before Phase 5 re-run

**The audit should be thorough and critical. Do not assume anything is correct just because it was in the spec. The goal is a stack that is 100% grounded in how DEX AMMs actually work.**
