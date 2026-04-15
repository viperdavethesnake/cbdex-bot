# DEX Architecture Audit — Securities Contamination Report

**Audit Date:** 2026-04-14  
**Auditor:** Claude (Opus 4.6) — Master Auditor Role  
**Files Reviewed:** TRD_v1_4.md, ARCHITECTURE.md, IMPLEMENTATION_GUIDE.md, README.md, API_REFERENCE.md, POOL_RESEARCH_FINDINGS.md, COINBASE_DEX_RESEARCH_FINDINGS.md  
**Overall Status:** ⚠️ ISSUES FOUND — All resolved in subsequent updates (see Resolution Status column)

---

## Preliminary Finding: Missing Files at Time of Audit

The audit was run against the Claude Project files. TRD_v1.5.md and CLAUDE.md existed locally but had not been synced to the project at audit time. Both files have since been updated and pushed.

---

## Category 1: Inappropriate Metrics

| # | File | Issue | Severity | Resolution Status |
|---|---|---|---|---|
| 1.1 | TRD_v1_4.md | Pearson correlation (ρ > 0.999) in audit gate | HIGH | ✅ Removed in TRD v1.5 |
| 1.2 | ARCHITECTURE.md | Pearson correlation in Section 5.2 audit thresholds | HIGH | ✅ Removed |
| 1.3 | IMPLEMENTATION_GUIDE.md | `pearsonr` import and computation in Phase 5.1 | HIGH | ✅ Removed from code |
| 1.4 | IMPLEMENTATION_GUIDE.md | `price_correlation` in THRESHOLDS dict | HIGH | ✅ Removed |
| 1.5 | README.md | Pearson correlation in audit thresholds table | MEDIUM | ✅ Removed |
| 1.6 | TRD_v1_4.md | `price_correlation` field in example JSON | MEDIUM | ✅ Archived (TRD v1.4 superseded) |

**Items confirmed clean:** Sharpe ratio, Sortino ratio, alpha/beta, VaR, bid-ask spread analysis, CAPM/MPT — none found in any file.

---

## Category 2: Wrong DEX Pricing Assumptions

| # | File | Issue | Severity | Resolution Status |
|---|---|---|---|---|
| 2.1 | ARCHITECTURE.md | TVL Error `< 5%` threshold — implies Fast Path TVL exists (it never does) | MEDIUM | ✅ Fixed — TVL Error now documented as null |
| 2.2 | IMPLEMENTATION_GUIDE.md | `tvl_error_pct: ("<", 5.0)` in THRESHOLDS dict | LOW | ✅ Removed |
| 2.3 | POOL_RESEARCH_FINDINGS.md | "Average spread" — order-book terminology | LOW | ⚠️ Minor — acceptable as noted context |

**Items confirmed clean:** AMM formula understanding, slippage model, price discovery framing, pool TVL as liquidity measure, bonding curve/constant product descriptions.

---

## Category 3: Wrong Data Representation Assumptions

| # | File | Issue | Severity | Resolution Status |
|---|---|---|---|---|
| 3.1 | ARCHITECTURE.md | `tvl_usd` described as "Pool TVL at candle close" — DEX pools don't close | LOW | ✅ Fixed — updated to "Hourly TVL forward-filled" |
| 3.2 | ARCHITECTURE.md | "liquidation events" — leveraged trading term, not DEX-native | LOW | ✅ Fixed — updated to "large swap events" |

**Items confirmed clean:** OHLCV open/close definitions, 24/7 trading assumption, volume USD methodology, forward-filled TVL documentation.

---

## Category 4: Wrong Terminology

| # | File | Issue | Severity | Resolution Status |
|---|---|---|---|---|
| 4.1 | ARCHITECTURE.md | "liquidation events" (see 3.2) | LOW | ✅ Fixed |
| 4.2 | POOL_RESEARCH_FINDINGS.md | "Average spread" | LOW | ⚠️ Minor — noted as not measurable anyway |
| 4.3 | ARCHITECTURE.md | DexScreener in component map | MEDIUM | ✅ Removed |
| 4.4 | ARCHITECTURE.md | DexScreener in Mermaid flowchart | MEDIUM | ✅ Removed |
| 4.5 | README.md | DexScreener in directory structure comment | LOW | ✅ Fixed |

**Items confirmed clean:** Buy/Sell usage (acceptable shorthand), liquidity provider usage, exchange vs DEX distinction, pool terminology, swap terminology, router terminology.

---

## Category 5: Document Inconsistencies

| # | File | Issue | Severity | Resolution Status |
|---|---|---|---|---|
| 5.1 | ARCHITECTURE.md | Referenced TRD v1.3 in header | HIGH | ✅ Updated to v1.5 |
| 5.2 | IMPLEMENTATION_GUIDE.md | Referenced TRD v1.3 in header | HIGH | ✅ Updated to v1.5 |
| 5.3 | README.md | Referenced TRD v1.3 | HIGH | ✅ Updated to v1.5 |
| 5.7 | ARCHITECTURE.md | TVL Error `< 5%` contradicted TRD v1.4's `null` | HIGH | ✅ Fixed |
| 5.9 | ARCHITECTURE.md | DexScreener in component map | HIGH | ✅ Removed |
| 5.10 | ARCHITECTURE.md | Flowchart Step 1 said "OHLCV + TVL" from Fast Path | MEDIUM | ✅ Fixed — TVL never from Fast Path |
| 5.11 | IMPLEMENTATION_GUIDE.md | `$skip`-based GraphQL pagination | **CRITICAL** | ✅ Replaced with `id_gt` cursor pagination |
| 5.14 | TRD_v1_4.md | Aerodrome Router address truncated (38 hex chars, not 40) | **CRITICAL** | ✅ Corrected to `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43` (verified BaseScan) |

---

## Critical Findings (Both Resolved)

### Router Address — RESOLVED
Two different router addresses existed across documents. The correct address verified on BaseScan:
```
0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43
```
All documents corrected. A wrong router address would have sent production funds to an unknown contract — unrecoverable.

### skip Pagination — RESOLVED
IMPLEMENTATION_GUIDE.md Phase 4 used `$skip`-based GraphQL pagination. The Graph enforces a hard ceiling of 5,000 records via skip. All three audit windows exceeded 22,000+ swaps — skip would have silently failed on every one. Replaced with `id_gt` cursor pagination throughout.

---

## Items That Are Correct and DEX-Native (Preserve As-Is)

| Item | Why It's Correct |
|---|---|
| MAE < 0.10% | Direct price comparison between on-chain derivations. DEX-native. |
| Volume Error < 1% | Catches aggregation methodology differences. DEX-native. |
| Dropped Candles = 0 | Detects missing swap activity. Most important on-chain data quality check. |
| Filled Candles (info only) | Empty minutes expected on DEX — no activity ≠ data error. |
| Log return `ln(P_t+1 / P_t)` | Symmetric, scale-invariant. Correct for DEX price series. |
| sqrtPriceX96 conversion | Correct Uniswap V3 / Slipstream math. |
| Reserve ratio price derivation | Correct for Classic vAMM (x*y=k). |
| Stratified sampling by daily variance | Statistical sampling applied to swap data — not securities-specific. |
| id_gt cursor pagination | Correct for The Graph. Avoids 5,000 skip ceiling. |
| Regime filter for AERO/WETH | Cost-threshold filter. Not a securities concept. |
| Direct Aerodrome Router execution | Bypasses Coinbase aggregator fee. Pure on-chain interaction. |
| Min signal thresholds (0.12%, 0.65%) | Derived from actual pool fees. DEX-native cost model. |
| TVL as hourly forward-fill | Acknowledges The Graph's actual data resolution. No fake precision. |
| Gas data via baseFeePerGas | EIP-1559 base fee from block headers. Correct for Base L2. |

---

## Summary

**Overall contamination level at time of audit: MODERATE — concentrated in one metric (Pearson correlation) spread across multiple files, plus two critical non-securities issues (router address, skip pagination).**

**Current status: All critical and high-severity items resolved.** The stack is now grounded in DEX AMM mechanics throughout. The Pearson correlation has been eliminated from all documentation and executable code. The router address is verified on-chain. All pagination uses id_gt.

Remaining minor items (spread terminology in POOL_RESEARCH_FINDINGS.md) are documentation-only, do not affect implementation, and are acceptable given context.
