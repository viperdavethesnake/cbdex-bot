# Pool Research Findings
## Aerodrome Finance on Base — Pair Verification & Volatility Profile

**Research Date:** April 14, 2026  
**Sources:** GeckoTerminal, DexScreener, BaseScan, Aerodrome Docs, Coinbase Help, Mellow Protocol, CoinLore, CoinMarketCap, 0x Protocol documentation, Coinbase pricing disclosures  
**Status:** Complete. All critical findings resolved in TRD v1.4.

---

## Target 1: Pool Verification

### WETH/USDC

Multiple pools exist at different fee tiers. Pool address is pinned to the highest-volume CL pool.

| Pool Name | Pool Address | Type | Fee | TVL | 24h Vol |
|---|---|---|---|---|---|
| **CL100-WETH/USDC (SlipStream)** ✅ | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | **CL** | **0.05%** | ~$12–30M | ~$82M–$185M |
| vAMM-USDC/WETH (Classic) | `0xcdac0d6c6c59727a65f871236188350531885c43` | Classic Volatile | ~0.3% | ~$14.3M | ~$230K |
| vAMM-WETH/USDC (Classic, older) | `0x3548029694fbb241d45fb24ba0cd9c9d4e745f16` | Classic Volatile | ~0.3% | ~$67K | ~$3.6K |

**Locked pool address:** `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59`

- **Pool Type:** Concentrated Liquidity (CL / Slipstream) — Aerodrome's Uniswap v3-style pool
- **Swap Fee:** 0.05%
- **Tick Spacing:** 100
- **TVL:** ~$12.5M–$30M
- **24h Volume:** ~$82M–$185M — Aerodrome's dominant pool
- **Price encoding:** `sqrtPriceX96` — CL subgraph schema required for Truth Path queries

---

### AERO/WETH

Multiple pools exist. Dominant TVL sits in the Classic vAMM pool.

| Pool Name | Pool Address | Type | Fee | TVL | 24h Vol |
|---|---|---|---|---|---|
| **vAMM-AERO/WETH (Classic)** ✅ | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | **Classic Volatile** | **0.3%** | ~$9.8M | ~$110K |
| CL200-WETH/AERO (SlipStream) | `0x82321f3beb69f503380d6b233857d5c43562e2d0` | CL | 1% | ~$1.5M | ~$1.2M |
| AERO/WETH Uniswap V3 (0.3%) | `0x3d5d143381916280ff91407febeb52f2b60f33cf` | Uniswap V3 CL | 0.3% | ~$790K | ~$320K |
| AERO/WETH Uniswap V3 (1%) | `0x0d5959a52e7004b601f0be70618d01ac3cdce976` | Uniswap V3 CL | 1% | ~$530K | ~$70K |

**Locked pool address:** `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6`

- **Pool Type:** Classic Volatile (vAMM) — uses `x*y=k` constant product formula
- **Swap Fee:** 0.30%
- **Tick Spacing:** N/A — full-range liquidity
- **TVL:** ~$9.8M
- **24h Volume:** ~$110K (note: CL200 pool captures more aggregator-routed volume on some days)
- **Price encoding:** Reserve ratio — Classic vAMM subgraph schema required

---

## Target 2: Coinbase DEX Access

### Coinbase Wallet + Aerodrome

Coinbase Wallet supports swapping on Aerodrome pools on Base. Aerodrome is explicitly listed as a supported DEX in Coinbase's DEX trading announcement: "You can now trade on popular DEXes like Aerodrome and Uniswap in just a few clicks." Rollout to U.S. users (excluding New York) from October 2025.

### Coinbase Aggregator — CRITICAL FINDING

**Coinbase does not route DEX trades directly to pool contracts.** It routes through a **dual-aggregator stack powered by 0x Protocol and 1inch**.

- Trades settle directly in the Aerodrome pool contract but the call originates from the aggregator router
- **Coinbase charges a ~1% DEX service fee per swap** on top of the underlying pool fee
- Aggregators may also retain positive price improvement rather than passing it to the user

**Effective round-trip cost via Coinbase:**

| Pair | Pool Fee (RT) | Coinbase Fee (RT) | Effective Total |
|---|---|---|---|
| WETH/USDC | 0.10% | ~2.00% | ~2.10% |
| AERO/WETH | 0.60% | ~2.00% | ~2.60% |

**Resolution (applied in TRD v1.4):** Production trading must use the Aerodrome Router directly:
```
Aerodrome Router: 0xcF77a3Ba9A5CA399B7c97c74d94E92359DC59
Interface: web3.py → Base RPC
```
Coinbase's interface is acceptable for manual observation only.

### Base DEX Landscape (Top 3 by Volume)

1. **Aerodrome Finance** — Dominant DEX on Base (~57% of all Base DEX volume at peak)
2. **Uniswap V3** (Base deployment) — Second historically
3. **PancakeSwap** — Overtook Uniswap on Base in January 2026

### Programmatic Access

**Coinbase does not offer a programmatic DEX API for on-chain swaps.** Coinbase Advanced Trade API is CEX-only. Direct smart contract interaction via `web3.py` is the correct approach for programmatic DEX trading on Base.

---

## Target 3: AERO/WETH Volatility Profile

### Price History

- **90-day range (Jan–Apr 2026):** ~$1.58 → ~$0.31–$0.40 (~45% decline)
- **30-day range (Mar–Apr 2026):** ~$0.31–$0.43
- **ATH:** $2.33–$2.37 (December 2024)
- **Significant dislocations (>10% single day):**
  - Late Feb 2026: ~15% surge in 24h, >109% volume increase
  - Early Mar 2026: ~12.7% rally in 24h
  - Multiple days with >6% intraday swings visible on GeckoTerminal

### 1-Minute Volatility Assessment

Meaningful 1-minute moves >0.6% are **intermittent, not consistent**:
- High-volatility days (spike sessions): 1-minute candles exceeding 0.6% are frequent
- Flat days: moves likely well below the 0.6% threshold
- Active 24/7 on-chain, but liquidity thins during overnight UTC hours
- Regime-aware filtering is mandatory

### Volume Consistency

- Classic vAMM pool (`0x7f670f78`): ~$110K/day average, highly variable ($50K–$500K range)
- Periods with <$100K/day volume are likely during low-activity windows
- Sparse 1-minute candles and gaps are expected during quiet periods

### Fee Viability Verdict

**[x] Marginal — fee is achievable but only during volatile sessions**

- 0.60% round-trip (direct contract) is technically beatable during high-volatility sessions
- Via Coinbase (~2.6% round-trip): non-viable under any normal conditions
- Regime filter mandatory: execute only when realized 1-minute volatility exceeds 1.5× the fee hurdle (~0.98% per minute)

---

## Target 4: Alternative Pair Candidates

| Pair | Pool Address | Type | Fee | TVL | 24h Vol | Viable? |
|---|---|---|---|---|---|---|
| cbETH/WETH | `0x47cA...D7348` (verify on BaseScan) | CL Stable (tick 1) | 0.01% | ~$7.7M | ~$21.9M | Low signal — correlated pair |
| BRETT/WETH | `0x4e829F8A5213c42535AB84AA40BD4aDCCE9cBa02` | CL200 | 1% | ~$1.0M | ~$130K | Fee too high |
| DEGEN/WETH | `0x2c4909355b0c036840819484c3a882a95659abf3` | Classic Volatile | ~0.3% | ~$362K | ~$36K | Liquidity too thin |
| USDC/USDbC | Deprecated — USDbC no longer active on Base | — | — | — | — | Not viable |

**Notes:**
- **cbETH/WETH:** 0.01% fee (0.02% round-trip) makes signal threshold very low, but cbETH ≈ WETH × staking yield — moves are tiny by design. Better for statistical arbitrage, not volatility capture.
- **BRETT/WETH:** High volatility but 1% fee (2% round-trip) requires very large moves to profit.
- **DEGEN/WETH:** Better liquidity on Uniswap V3 Base (`0xc9034c3e`, ~$915K TVL, ~$443K/day) than Aerodrome. Not viable on Aerodrome.
- **If AERO/WETH proves unworkable** after smoke test: best substitute is **VIRTUAL/WETH** (`0xc200f21efe67c7f41b81a854c26f9cda80593065`, 0.7% fee, ~$2.66M/day volume).

---

## Critical Findings

### Finding 1 — COINBASE SERVICE FEE (CRITICAL — RESOLVED IN TRD v1.4)

Coinbase adds ~1% per swap on top of pool fees. Round-trip cost via Coinbase:
- WETH/USDC: ~2.10% (vs 0.12% minimum signal threshold)
- AERO/WETH: ~2.60% (vs 0.65% minimum signal threshold)

**Resolution:** Production bot uses Aerodrome Router directly via web3.py. Coinbase UI for manual observation only.

### Finding 2 — MIXED POOL TYPES (RESOLVED IN TRD v1.4)

WETH/USDC is CL (sqrtPriceX96), AERO/WETH is Classic (reserve ratio). Two different subgraph schemas and aggregation functions required.

**Resolution:** Both schemas documented in ARCHITECTURE.md and IMPLEMENTATION_GUIDE.md. Pool-type-aware branching required throughout Truth Path code.

### Finding 3 — AERO/WETH VOLUME SPLIT ACROSS TWO POOLS (INFORMATIONAL)

Volume split between Classic vAMM (`0x7f670f78`, ~$110K/day, ~$9.8M TVL) and SlipStream CL200 (`0x82321f3b`, ~$1.17M/day some days, ~$1.5M TVL). Classic vAMM selected for deeper TVL and simpler schema.

**Action:** Validate candle gap rate during 7-day smoke test. If >5% of minutes show gaps, consider adding CL200 pool volume.

### Finding 4 — AERO 90-DAY DRAWDOWN (INFORMATIONAL)

The 90-day training window (Jan–Apr 2026) covers a ~45% AERO decline. Training data heavily weighted toward bear-market conditions. Model may not generalize to bull or sideways regimes. Flag in model specification.

---

## Confirmed Pair Selection (Locked)

| Pair | Pool Address | Type | Fee | Status |
|---|---|---|---|---|
| WETH/USDC | `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` | CL Slipstream | 0.05% | ✅ CONFIRMED |
| AERO/WETH | `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` | Classic vAMM | 0.30% | ✅ CONFIRMED WITH CAVEATS |

---

*Data freshness note: TVL and 24h volume figures are point-in-time snapshots from April 14, 2026. Verify current values against aerodrome.finance/liquidity before implementation.*
