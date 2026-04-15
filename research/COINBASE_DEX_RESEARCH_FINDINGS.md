# Coinbase DEX Research Findings — Operational Gap Analysis

**Research Date:** 2026-04-14  
**Status:** Complete — 4 critical findings, all prior confirmations unchanged  
**Supersedes:** Research gaps identified in COINBASE_DEX_RESEARCH_PROMPT.md

---

## Gap 1: Exact Fee Structure

### Service Fee
- **Coinbase Wallet (self-custody app):** Fixed **1.00%** per swap — confirmed by TokenEcho and Milk Road citing Coinbase fee schedule, April 2026
- **Coinbase main app DEX feature:** Variable — not published as a fixed percentage. Disclosed only in the pre-trade preview screen. ~1% is the best available estimate.
- **Source URLs:** https://help.coinbase.com/en/coinbase/trading-and-funding/pricing-and-fees/fees · https://www.coinbase.com/dex
- **Per-side or per round-trip:** Charged on **each swap individually**. Buy = fee applies. Sell = fee applies again. No netting, no round-trip discount.
- **Price improvement:** Aggregators (0x/1inch) may retain positive price improvement. Not passed to user. Amount is unquantified.
- **Gas handling:** Coinbase **sponsors gas** for in-app DEX trades on Base. Zero gas cost to user. On Coinbase Wallet direct swaps, gas is charged separately (~$0.01–$0.03 on Base).
- **Sub-$100 treatment:** No tiered fee scaling documented for DEX swaps. Percentage fee applies uniformly regardless of trade size.
- **Coinbase One:** Members pay zero Coinbase service fee on DEX trades; pool fees still apply.

### $100 AERO→WETH Cost Breakdown

| Cost Component | Coinbase Main App | Coinbase Wallet | Direct Aerodrome Router |
|---|---|---|---|
| Aerodrome pool fee (0.30%) | $0.30 | $0.30 | $0.30 |
| Platform service fee (~1%) | ~$1.00 (variable) | ~$1.00 (fixed) | $0.00 |
| Base network gas | **$0.00** (sponsored) | ~$0.01–$0.03 | ~$0.01–$0.03 |
| Price improvement retained | Unknown | Unknown | Yours |
| **Total per swap** | **~$1.30** | **~$1.31** | **~$0.31** |
| **Round-trip (buy + sell)** | **~$2.60 (~2.60%)** | **~$2.62 (~2.62%)** | **~$0.62 (~0.62%)** |

⚠️ **The ~2.60% round-trip estimate from TRD v1.4 remains accurate.** The direct Aerodrome Router is ~4× cheaper than any Coinbase interface.

---

## Gap 2: Token and Pair Availability

### AERO Token
- **Tradeable via Coinbase DEX on Base: LIKELY YES — not confirmed in-app**
- AERO (`0x940181a94A35A4569E4529A3CDfB74e38FD98631`) is Base-native with significant liquidity (~$340M TVL on Aerodrome protocol)
- Coinbase does not maintain a published token allowlist for DEX trading — the system dynamically indexes Base tokens
- AERO is not listed on Coinbase CEX, but CEX listing status does not affect DEX token availability
- Only tokens flagged as confirmed malicious/fraudulent by a third-party vendor are blocked. AERO does not meet any blocking criteria.
- **Action required:** Verify AERO is searchable in the Coinbase app/Wallet DEX interface before the first manual test trade

### WETH Token
- **Tradeable via Coinbase DEX on Base: YES** — WETH is a core asset on Base and confirmed available

### AERO/WETH Pair (direct)
- **Likely available as a direct pair** — the 0x/1inch aggregator will route AERO→WETH directly through Aerodrome if sufficient liquidity exists (which it does at ~$9.8M TVL)
- If direct routing isn't available, fallback would be AERO→USDC→WETH (two hops, two fee events — would double cost)
- **Action required:** Verify direct AERO/WETH routing in the pre-trade screen before manual testing

### Token Allowlist
- Published list: **Does not exist.** Dynamic indexing only.
- Blocking criteria: Confirmed malicious/fraudulent tokens only. No market cap, CoinGecko listing, or governance token restrictions.

---

## Gap 3: Geographic Restrictions

- **California: FULLY SUPPORTED** — no DEX trading restrictions. California's staking restriction (DFPI enforcement) does not apply to DEX trading.
- **Other restricted U.S. states:** New York only for DEX trading. (Staking restrictions also affect CA, MD, NJ, SC, WI — irrelevant here.)
- **KYC requirements:** Standard Coinbase account verification required. One-time self-custody wallet setup required within the app before first DEX trade. No minimum account age or trading history requirement found.
- **Geographic scope:** Currently U.S. only (excluding NY). International expansion planned.

---

## Gap 4: Trade Size Limits

- **Minimum swap size:** Not published. Likely $1–$5 functional floor given gas is sponsored. Not confirmed.
- **Maximum swap size:** Not published. Governed by pool liquidity and slippage tolerance shown pre-trade, not a hard Coinbase ceiling.
- **Sub-$100 treatment:** No tiered pricing or different routing for sub-$100 trades. Percentage fee scales proportionally.

---

## Gap 5: Interface Comparison for Manual Testing

### Recommended Interface: Coinbase Wallet (self-custody)

**Rationale:**
- Shows routing path before confirmation (which DEX/pool is being used)
- Shows estimated price impact
- Better fee transparency than main app
- Transaction is fully visible on BaseScan showing aggregator router and pool addresses
- Main app DEX routes from custodial balance — less transparent, harder to match against our data pipeline

### Transaction Visibility on BaseScan
- **Pool address visible:** Yes — Aerodrome pool contract appears in the transaction trace
- **Aggregator router visible:** Yes — 0x or 1inch router address is the `to` field of the transaction
- **Amounts in/out visible:** Yes — full token transfer log visible

### Manual Test Trade Checklist (before first real trade)
1. Verify AERO is searchable in Coinbase Wallet on Base network
2. Verify AERO/WETH routes directly (not via USDC hop)
3. Record the exact service fee shown in the pre-trade preview
4. Execute a small test swap ($10–$20)
5. Find the transaction on BaseScan and record pool address and amounts
6. Compare executed price against our `candidate_90d.parquet` data for the same timestamp

---

## Gap 6: CDP and Programmatic Options

### CDP Trade API — NEW FINDING
- **Exists: YES** — https://docs.cdp.coinbase.com/trade-api/welcome
- Coinbase launched a Trade API that wraps 0x routing for DEX execution on Base
- **Python accessible:** Yes — REST API callable from Python
- **Fee structure:** Not published. Requires testing to determine cost vs direct router
- **Assessment:** Not worth integrating for current scope (sub-$100 fixed pools, maximum fee control). Direct Aerodrome Router remains superior. Flag for re-evaluation if routing complexity increases.

### OnchainKit Swap
- **Swap feature exists:** Yes — https://onchainkit.xyz
- **Python accessible:** No — JavaScript/TypeScript only. No REST API backend for external callers.
- **Assessment:** Not relevant for Python bot.

### 0x API Direct Access
- **Public endpoint:** Yes — https://0x.org/docs
- **Fee:** ~0.15% protocol fee (vs ~1% via Coinbase)
- **Viable for our bot:** Technically yes, but adds routing complexity and trust dependency for marginal gain over direct Aerodrome Router at sub-$100 sizes
- **Assessment:** Not worth adding to current scope. At $100 trade size, 0x fee = $0.15 vs direct router fee ≈ $0. The complexity cost exceeds the fee saving.

### Confirmed Lowest-Cost Path
- **Direct Aerodrome Router via web3.py: CONFIRMED OPTIMAL**
- No Coinbase or 0x integration justified for current scope

### Execution Path Summary

| Path | Pool Fee | Platform Fee | Gas | Round-trip Total | Decision |
|---|---|---|---|---|---|
| **Direct Aerodrome Router (web3.py)** | 0.30% | 0% | ~$0.03 | **~0.62%** | ✅ Production |
| 0x API (direct) | 0.30% | ~0.15% | ~$0.03 | ~0.90% | ❌ Not justified |
| CDP Trade API | 0.30% | Unknown | Optional | TBD | 🔄 Future eval |
| Coinbase Wallet | 0.30% | ~1.00% | ~$0.02 | ~2.62% | 🔍 Manual testing only |
| Coinbase Main App | 0.30% | ~1.00% variable | $0.00 | ~2.60% | 🔍 Manual testing only |

---

## Gap 7: Smart Wallet Relevance

- **ERC-4337 account abstraction:** Yes — Coinbase Smart Wallet uses account abstraction
- **Transactions via bundler:** Yes — transactions are submitted through a bundler, not direct RPC. This adds a hop vs standard EOA.
- **Relevant for Python bot:** No — requires Coinbase SDK (JavaScript). No Python interface.
- **Impact on transaction speed:** Bundler adds ~1–2 seconds of latency vs direct EOA submission. Material for HFT, negligible for 1-minute signal trading.
- **Gas costs:** Smart Wallet can sponsor gas via paymasters, but we already have near-zero gas on Base.
- **Recommended wallet type for bot: Standard EOA (externally owned account)**
  - Direct private key control
  - Direct RPC submission via web3.py — no bundler latency
  - Full transaction composability
  - No SDK dependency
  - Smart Wallet offers no meaningful advantage for our use case

---

## Critical Findings

### Finding 1 — CDP TRADE API EXISTS (INFORMATIONAL)
**Severity: Low — no immediate action required**

A new CDP Trade API wraps 0x routing for DEX execution. Not suitable for current scope but should be re-evaluated if the project expands to multi-pool routing or requires smart order routing.

**Documentation:** https://docs.cdp.coinbase.com/trade-api/welcome

---

### Finding 2 — COINBASE MAIN APP SPONSORS GAS (MINOR COST MODEL UPDATE)
**Severity: Low — strategic conclusion unchanged**

Gas cost via Coinbase main app is $0 (sponsored). This makes the all-in cost $1.30 per $100 swap (not $1.31 as previously estimated including gas). The ~2.60% round-trip figure in TRD v1.4 remains the correct planning number.

---

### Finding 3 — COINBASE MAIN APP DEX FEE IS VARIABLE (QUALIFIER ON ~1% ESTIMATE)
**Severity: Medium — affects manual test planning**

The ~1% figure is confirmed for Coinbase Wallet but is variable for the main app's DEX feature. Always check the pre-trade preview screen and record the actual fee before any manual test trade.

---

### Finding 4 — CALIFORNIA FULLY SUPPORTED (CONFIRMS PRIOR ASSUMPTION)
**Severity: None — no action required**

---

## Unchanged Confirmations

| Prior Confirmed Fact | Status |
|---|---|
| Coinbase Wallet supports Aerodrome Finance on Base | ✅ CONFIRMED |
| Coinbase routes DEX trades through 0x Protocol + 1inch | ✅ CONFIRMED |
| ~1% service fee per swap (Coinbase Wallet) | ✅ CONFIRMED — main app is variable |
| Coinbase Advanced Trade API is CEX order-book only | ✅ CONFIRMED |
| Production bot must use Aerodrome Router directly via web3.py | ✅ CONFIRMED — optimal path |
| New York excluded from Coinbase DEX trading | ✅ CONFIRMED |
| Aerodrome is dominant DEX on Base | ✅ CONFIRMED — 55%+ of volume |
| TRD v1.4 cost model (~2.60% Coinbase round-trip) | ✅ CONFIRMED — no revision needed |
