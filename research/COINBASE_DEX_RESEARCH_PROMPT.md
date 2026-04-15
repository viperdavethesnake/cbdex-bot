# Research Prompt: Coinbase DEX Trading on Base — Operational Gap Analysis

**Document Type:** Agent Research Brief  
**Date:** 2026-04-14  
**Purpose:** Fill specific operational gaps in our understanding of Coinbase's DEX interface on Base. Do not re-research items marked as CONFIRMED below.

---

## What We Already Know — Do Not Re-Research

The following facts are confirmed from prior research. Skip them entirely:

| Fact | Status |
|---|---|
| Coinbase Wallet supports Aerodrome Finance on Base | CONFIRMED |
| Coinbase routes DEX trades through 0x Protocol + 1inch aggregators | CONFIRMED |
| Coinbase charges approximately ~1% service fee per swap on top of pool fees | CONFIRMED (approximate — exact figure needed, see below) |
| Coinbase Advanced Trade API is CEX order-book only, not DEX | CONFIRMED |
| Production bot must use Aerodrome Router `0xcF77a3Ba9A5CA399B7c97c74d94E92359DC59` directly via web3.py | CONFIRMED |
| New York state users are excluded from Coinbase DEX trading | CONFIRMED |
| Aerodrome is the dominant DEX on Base (~57% of volume) | CONFIRMED |

---

## Context

We are building a Python trading bot (sub-$100 trade sizes) targeting two Aerodrome pools on Base:
- **WETH/USDC** — pool `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59`, 0.05% fee
- **AERO/WETH** — pool `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6`, 0.30% fee

We want to use Coinbase as the **manual observation and testing interface only** before the bot goes fully programmatic. We need to understand exactly what Coinbase's interface can and cannot do for these specific tokens and pairs before writing the execution layer.

The user is located in **California**.

---

## Research Gap 1: Exact Fee Structure

**What we know:** ~1% service fee. **What we need:** Exact numbers.

### Questions

1. What is the exact published DEX service fee percentage for Coinbase's Base network swaps?
   - Is it a fixed 1.00% or variable (e.g., tiered by volume or trade size)?
   - Source: check https://help.coinbase.com (search "DEX fee"), https://www.coinbase.com/legal/user_agreement, and the in-app fee disclosure

2. Is the fee charged on **each swap individually** (buy = 1%, sell = 1%) or is there any netting for round-trips?

3. Does the fee scale differently for sub-$100 trades vs larger trades? (We are specifically targeting $50–$100 per swap.)

4. Does Coinbase retain **positive price improvement** (favorable slippage) or pass it to the user? This is separate from the stated fee.

5. Who pays the Base network gas fee — is it wrapped into the service fee, or is it a separate line item shown to the user?

6. **For a $100 AERO→WETH swap specifically:** What is the all-in cost breakdown?
   - Aerodrome pool fee: $0.30 (0.30%)
   - Coinbase service fee: $?
   - Gas on Base: $? (typically sub-cent on Base)
   - Total: $?

---

## Research Gap 2: Specific Token and Pair Availability

**What we know:** Aerodrome is supported generally. **What we need:** Confirmation for our specific tokens.

### Questions

1. Is **AERO** (Aerodrome Finance governance token, contract `0x940181a94A35A4569E4529A3CDfB74e38FD98631` on Base) specifically tradeable via Coinbase's DEX interface?
   - Check the Coinbase app DEX section, Coinbase Wallet swap interface, and any published supported token list
   - Note: AERO is not listed on Coinbase's CEX — confirm whether this affects DEX availability

2. Is **WETH** (Wrapped Ether on Base, contract `0x4200000000000000000000000000000000000006`) tradeable?

3. Is the **AERO/WETH pair** (direct swap, not via USDC intermediary) available? Or does Coinbase only support pairs routed through ETH or USDC?

4. Does Coinbase maintain a published allowlist of tokens available for DEX trading on Base? If yes, provide the URL.

5. Are there any token categories that are **always blocked** regardless of Base availability (e.g., governance tokens, tokens without CoinGecko listings, tokens below a market cap threshold)?

---

## Research Gap 3: Geographic and Account Restrictions

**What we know:** New York excluded. **What we need:** Full restriction picture.

### Questions

1. Is **California** fully supported for Coinbase DEX trading on Base? (User is in Rolling Hills, California.)

2. Beyond New York, what other U.S. states are restricted or have limited access to Coinbase's DEX features?

3. Are there any **KYC or account level** requirements for DEX trading? (e.g., must be a verified account, must have 2FA enabled, must have traded before?)

4. Is there a minimum account age or trading history required to access the DEX feature?

---

## Research Gap 4: Trade Size Limits

**What we know:** Nothing specific. **What we need:** Confirmed limits for sub-$100 trading.

### Questions

1. Is there a **minimum trade size** for DEX swaps via Coinbase's interface on Base?
   - Specifically: can you swap $10 worth of AERO for WETH?
   - Can you swap $50?

2. Is there a **maximum trade size** per swap?

3. Are sub-$100 trades treated differently (e.g., higher fee percentage, different routing)?

---

## Research Gap 5: Coinbase Wallet vs Coinbase App — Which to Use for Testing

**What we know:** Both support Aerodrome. **What we need:** Which is better for our use case.

### Questions

1. **Coinbase App (custodial):** When you swap tokens via the main Coinbase app's DEX feature on Base:
   - Does it require you to first move funds to a self-custody wallet, or does it swap directly from your Coinbase custodial balance?
   - Is the underlying transaction visible on BaseScan with the actual Aerodrome pool address showing?

2. **Coinbase Wallet (self-custody):** When you swap via Coinbase Wallet on Base:
   - Does it show you the routing path before confirming (e.g., "routing through Aerodrome")?
   - Does it show estimated price impact?
   - Is there more fee transparency than the main app?

3. **For a developer wanting to:**
   - Manually execute a $50 AERO→WETH swap
   - Observe exactly which pool was used and at what price
   - Compare the executed price against the on-chain price from our data pipeline
   
   Which interface — Coinbase App or Coinbase Wallet — gives better visibility and control for this use case?

4. After a swap via either interface, does the BaseScan transaction show:
   - The Aerodrome pool contract address?
   - The 0x or 1inch aggregator router address?
   - The exact amounts in/out?

---

## Research Gap 6: Coinbase Developer Platform (CDP) — DEX Capabilities

**What we know:** Advanced Trade API is CEX-only. **What we need:** Whether CDP has any DEX swap functionality.

### Questions

1. Does **Coinbase Developer Platform (CDP)** — https://docs.cdp.coinbase.com — expose any API endpoint for executing DEX swaps on Base?
   - Search for "swap", "DEX", "onchain swap" in CDP docs
   - If yes: provide the endpoint name, authentication method, and documentation URL

2. Does **OnchainKit** (Coinbase's developer toolkit) include swap functionality?
   - URL: https://onchainkit.xyz
   - Is it JavaScript/TypeScript only, or does it have a Python interface?
   - If JS only: is there a REST API backend we could call from Python?

3. Does the **0x Swap API** (which Coinbase uses internally) have a public endpoint we could call directly from Python to get the same routing as Coinbase without their 1% fee?
   - URL: https://0x.org/docs
   - If yes: what is the fee structure for direct 0x API usage vs going through Coinbase?

4. **Confirmation question:** For a Python bot executing sub-$100 DEX swaps on Base, is direct Aerodrome Router interaction via web3.py still the lowest-cost and most reliable approach, even after considering any CDP or 0x alternatives?

---

## Research Gap 7: Coinbase Smart Wallet — Relevance Assessment

**What we know:** It exists. **What we need:** Whether it matters for our bot.

### Questions

1. What is **Coinbase Smart Wallet** (https://www.coinbase.com/wallet/smart-wallet)?
   - How does it differ from regular Coinbase Wallet?
   - Does it support DEX trading on Base?

2. Does Smart Wallet use **ERC-4337 account abstraction**?
   - If yes: does this change how transactions are submitted (e.g., via a bundler instead of direct RPC)?
   - Does this affect transaction speed or gas costs on Base compared to a standard EOA wallet?

3. Is Smart Wallet relevant for a Python trading bot, or is it consumer-facing only?
   - Specifically: can a Python script interact with a Smart Wallet contract to execute swaps, or does it require Coinbase's SDK?

4. **Bottom line:** Should we use a standard EOA wallet (externally owned account, standard private key) for the bot, or is there any advantage to Smart Wallet for our use case?

---

## Required Output Format

```markdown
# Coinbase DEX Research Findings — Operational Gap Analysis

**Research Date:** [date]
**Status:** Complete

---

## Gap 1: Exact Fee Structure

### Service Fee
- Exact fee: X% (fixed) / Variable: [describe]
- Source URL: ...
- Per-side (each swap) or per round-trip: ...
- Price improvement retained by Coinbase: Yes / No / Unknown
- Gas handling: Included in fee / Separate line item / Absorbed by Coinbase

### $100 AERO→WETH Cost Breakdown
- Pool fee (0.30%): $0.30
- Coinbase service fee: $X.XX
- Gas on Base: $X.XX
- Total all-in cost: $X.XX
- Effective round-trip cost (buy + sell): $X.XX / X%

---

## Gap 2: Token and Pair Availability

### AERO Token
- Tradeable via Coinbase DEX on Base: Yes / No / Unknown
- Notes: ...

### WETH Token
- Tradeable via Coinbase DEX on Base: Yes / No

### AERO/WETH Pair (direct)
- Available as direct pair: Yes / No / Routes via intermediary
- If via intermediary: route is AERO → [token] → WETH

### Token Allowlist
- Published list exists: Yes [URL] / No / Unknown
- Blocking criteria (if known): ...

---

## Gap 3: Geographic Restrictions

- California: Fully supported / Restricted / Unknown
- Other restricted U.S. states beyond New York: [list]
- KYC/account requirements: [describe]

---

## Gap 4: Trade Size Limits

- Minimum swap size: $X / None / Unknown
- Maximum swap size: $X / None / Unknown
- Sub-$100 treatment: Same as larger / Different fees / Unknown

---

## Gap 5: Interface Comparison for Manual Testing

### Recommended Interface: Coinbase App / Coinbase Wallet
### Rationale: ...

### Transaction Visibility on BaseScan
- Pool address visible: Yes / No
- Aggregator router visible: Yes / No
- Amounts visible: Yes / No

---

## Gap 6: CDP and Programmatic Options

### CDP DEX API
- Exists: Yes [URL] / No
- Python accessible: Yes / No / Via REST

### OnchainKit Swap
- Swap feature exists: Yes / No
- Python accessible: Yes / No

### 0x API Direct Access
- Public endpoint: Yes [URL] / No
- Fee vs Coinbase: X% vs X%
- Viable for our bot: Yes / No

### Confirmed Lowest-Cost Path
- Direct Aerodrome Router via web3.py: Confirmed / Better alternative found: [describe]

---

## Gap 7: Smart Wallet Relevance

- ERC-4337 account abstraction: Yes / No
- Relevant for Python bot: Yes / No
- Recommended wallet type for bot: Standard EOA / Smart Wallet / Other
- Rationale: ...

---

## Critical Findings

[Any finding that changes the cost model, execution strategy, or pair availability]

## Unchanged Confirmations

[Confirm which prior findings remain accurate]
```

---

## What Happens After Research

Findings will be used to:
1. Finalize the execution layer design in `execution/` 
2. Update the cost model in TRD v1.4 if the exact Coinbase fee differs from ~1%
3. Determine the correct manual testing workflow for verifying bot trades against real market prices
4. Decide whether any CDP or 0x API integration is worth adding alongside direct Aerodrome Router calls
5. Confirm EOA vs Smart Wallet choice for the bot's hot wallet

**Flag as Critical Finding:** Any discovery that AERO is not directly tradeable via Coinbase, any fee that is materially different from 1%, or any programmatic path that is cheaper than direct contract interaction.
