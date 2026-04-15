# API Research Findings — Base DEX Data Acquisition Layer

**Document Type:** Research Output  
**Prepared for:** API_REFERENCE.md population and TRD v1.4 verification  
**Research date:** 2026-04-14  
**Status:** Complete — critical blockers identified, see Section 6  
**Resolution status:** All blockers resolved in TRD v1.4 and updated docs.

---

## Table of Contents

1. [API-1: GeckoTerminal](#api-1-geckoterminal)
2. [API-2: DexScreener](#api-2-dexscreener)
3. [API-3: The Graph — Aerodrome Subgraph on Base](#api-3-the-graph--aerodrome-subgraph-on-base)
4. [API-4: Alchemy / QuickNode — Base RPC](#api-4-alchemy--quicknode--base-rpc)
5. [Recommended Primary Fast Path Source](#recommended-primary-fast-path-source)
6. [Critical Blockers Found](#critical-blockers-found)

---

## API-1: GeckoTerminal

**Base URL:** `https://api.geckoterminal.com/api/v2`  
**Docs:** https://apiguide.geckoterminal.com  
**Swagger:** https://api.geckoterminal.com/docs/index.html

---

### OHLCV Endpoint

**Endpoint:**
```
GET /networks/{network}/pools/{pool_address}/ohlcv/{timeframe}
```

**For Base mainnet 1-minute candles:**
```
GET https://api.geckoterminal.com/api/v2/networks/base/pools/{pool_address}/ohlcv/minute?aggregate=1&limit=1000
```

**Parameters:**

| Parameter | Required | Location | Type | Values / Notes |
|---|---|---|---|---|
| `network` | Yes | path | string | `base` for Base mainnet |
| `pool_address` | Yes | path | string | Pool contract address (checksummed) |
| `timeframe` | Yes | path | string | `day`, `hour`, `minute` |
| `aggregate` | No | query | integer | For `minute`: `1`, `5`, `15` — default `1` |
| `before_timestamp` | No | query | integer | Unix epoch seconds. Returns candles *before* this timestamp. Primary pagination handle. |
| `limit` | No | query | integer | Default `100`, **max `1000`** |
| `currency` | No | query | string | `usd` (default) or `token` |
| `token` | No | query | string | `base` (default), `quote`, or a token contract address |

**Candle capacity and pagination math for 90-day pull:**

- 90 days × 1,440 min/day = **129,600 candles** required
- Max per request: **1,000**
- Requests needed: **≥ 130 paginated calls**, stepping backward via `before_timestamp`
- Pagination pattern: set `before_timestamp` to the `timestamp` of the oldest candle returned in the previous response; repeat until the target start date is reached

**Example pagination loop (Python):**
```python
url = "https://api.geckoterminal.com/api/v2/networks/base/pools/{addr}/ohlcv/minute"
params = {"aggregate": 1, "limit": 1000, "currency": "usd"}
before_ts = int(datetime.now(UTC).timestamp())
target_ts  = before_ts - (90 * 86400)

all_candles = []
while before_ts > target_ts:
    params["before_timestamp"] = before_ts
    resp = httpx.get(url, params=params)
    if resp.status_code == 429:
        time.sleep(4)
        continue
    candles = resp.json()["data"]["attributes"]["ohlcv_list"]
    if not candles:
        break
    all_candles.extend(candles)
    before_ts = candles[-1][0]   # oldest timestamp in this batch
    time.sleep(2.1)              # 30 req/min → ~2s between calls
```

**Max historical lookback (free tier):** **6 months from today.** The 90-day requirement falls well within this window.

---

### Volume Denomination

- Volume is the **6th element** of each OHLCV array: `[timestamp, open, high, low, close, volume]`.
- When `currency=usd` (the default), volume is denominated in **USD**.
- **USD conversion methodology: NOT FOUND — manual verification required.** The documentation does not specify whether the USD value is based on a 1-minute TWAP, the last-tick price at candle close, or another method. The audit gate will surface any systematic divergence from Truth Path volume.

---

### TVL Data

**GeckoTerminal does NOT provide historical TVL at any resolution.**

- The pool endpoint returns a `reserve_in_usd` field representing **current/real-time** total liquidity only.
- The OHLCV response arrays contain **no TVL field**.
- **The TVL split path will be triggered for every pair, every run.** TVL must be sourced entirely via the Truth Path.

**Pool endpoint (current TVL only):**
```
GET https://api.geckoterminal.com/api/v2/networks/base/pools/{pool_address}
```
Response includes `attributes.reserve_in_usd` (string, USD).

---

### Rate Limits

| Tier | Rate Limit | API Key Required |
|---|---|---|
| Free (public) | **30 requests per minute** | No |
| Paid (CoinGecko API) | Up to 250 requests per minute | Yes |

- **HTTP 429** returned on rate limit breach.
- `Retry-After` header not documented — implement exponential backoff: 1s → 2s → 4s.
- At 30 req/min, the 130-request 90-day pull completes in approximately **5 minutes**.

---

### Response Schema

**Full JSON structure for 1-minute OHLCV:**

```json
{
  "data": {
    "id": "bc786a99-7205-4c80-aaa1-b9634d97c926",
    "type": "ohlcv_request_response",
    "attributes": {
      "ohlcv_list": [
        [1712534400, 3454.615, 3660.859, 3417.918, 3660.859, 306823.277],
        [1712448000, 3362.602, 3455.288, 3352.953, 3454.615, 242144.864]
      ]
    }
  }
}
```

**`ohlcv_list` element structure:**

| Index | Field | Type | Notes |
|---|---|---|---|
| 0 | `timestamp` | integer | Unix seconds. Candle *start* time. |
| 1 | `open` | float | First trade price in bucket |
| 2 | `high` | float | Highest trade price in bucket |
| 3 | `low` | float | Lowest trade price in bucket |
| 4 | `close` | float | Last trade price in bucket |
| 5 | `volume` | float | USD volume if `currency=usd` |

Candles returned in **descending order** (newest first). Reverse before writing to Parquet.

---

### Base Network Support

- **Base mainnet is explicitly supported.**
- Network identifier in URL path: **`base`**
- Confirmed: `https://api.geckoterminal.com/api/v2/networks/base/pools/{pool_address}/ohlcv/minute?aggregate=1`

---

## API-2: DexScreener

**Base URL:** `https://api.dexscreener.com`  
**Docs:** https://docs.dexscreener.com/api/reference

---

### OHLCV Endpoint

**DexScreener does NOT provide a public endpoint for historical OHLCV candles at any resolution.**

The complete public API surface consists of current-state endpoints only:

| Endpoint | Rate Limit | Description |
|---|---|---|
| `GET /latest/dex/pairs/{chainId}/{pairId}` | 300 req/min | **Current** pair data snapshot |
| `GET /latest/dex/search` | 300 req/min | Search pairs |
| `GET /tokens/v1/{chainId}/{tokenAddresses}` | 300 req/min | Token data |

None expose historical OHLCV, trade history, or candlestick data.

---

### Practical Assessment

**DexScreener is NOT viable as a primary or backup source for 90-day 1-minute OHLCV.** It is a real-time market data display tool only.

**Resolution (applied in TRD v1.4):** GeckoTerminal designated as sole Fast Path source. DexScreener removed from pipeline role.

---

## API-3: The Graph — Aerodrome Subgraph on Base

**Registry:** https://thegraph.com/explorer  
**Billing / API Keys:** https://thegraph.com/studio/

---

### Subgraph Discovery

**Primary subgraph — Aerodrome Base Full:**

| Property | Value |
|---|---|
| Subgraph ID | `GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM` |
| Indexing network | base |
| Last updated (Explorer) | **~2 years ago** ⚠️ |
| Query URL | `https://gateway.thegraph.com/api/{THEGRAPH_API_KEY}/subgraphs/id/GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM` |

**Secondary subgraph — CL / Slipstream:**

| Property | Value |
|---|---|
| Name | base-v3-aerodrome |
| Subgraph ID | `nZnftbmERiB2tY6t2ika7kP9srTcKnYFEnqG3RKa38r` |

> ⚠️ **The primary subgraph shows "Updated 2 years ago." Verify sync status before any production queries using the subgraph sync check in the Implementation Guide.**

---

### Schema: Classic Pool Swaps (AERO/WETH)

> ⚠️ **Verify all field names via live subgraph introspection before writing production code.**

Expected fields:

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | `{txHash}-{logIndex}` |
| `timestamp` | `BigInt!` | Block timestamp (Unix seconds) |
| `amount0In` | `BigDecimal!` | Token0 amount flowing in |
| `amount0Out` | `BigDecimal!` | Token0 amount flowing out |
| `amount1In` | `BigDecimal!` | Token1 amount flowing in |
| `amount1Out` | `BigDecimal!` | Token1 amount flowing out |
| `amountUSD` | `BigDecimal!` | USD value of the swap |
| `transaction` | `Transaction!` | Parent transaction reference |

**Price derivation:** No `sqrtPriceX96`. Compute from token amounts:
```python
price = amount1In / amount0Out   # when selling token0
price = amount1Out / amount0In   # when buying token0
```

---

### Schema: CL Pool Swaps (WETH/USDC)

> ⚠️ **Verify all field names via live subgraph introspection before use.**

Expected fields:

| Field | Type | Description |
|---|---|---|
| `id` | `ID!` | `{txHash}#{logIndex}` |
| `timestamp` | `BigInt!` | Block timestamp (Unix seconds) |
| `amount0` | `BigDecimal!` | Token0 delta (negative = out of pool) |
| `amount1` | `BigDecimal!` | Token1 delta (negative = out of pool) |
| `sqrtPriceX96` | `BigInt!` | Pool sqrt price **after** the swap |
| `tick` | `BigInt!` | Current tick after the swap |
| `amountUSD` | `BigDecimal!` | USD value of the swap |
| `transaction` | `Transaction!` | Parent transaction reference |

**Converting `sqrtPriceX96` to price (WETH/USDC):**
```python
price_usdc_per_weth = (sqrtPriceX96 / 2**96) ** 2 * (10**18) / (10**6)
```

---

### Schema: Liquidity / TVL

**The Graph does not provide 1-minute TVL snapshots.**

| Entity | Resolution | TVL Field |
|---|---|---|
| `Pool` | Current (live) | `totalValueLockedUSD` (CL) or `reserveUSD` (Classic) |
| `PoolHourData` | **Hourly** | `tvlUSD` or `reserveUSD` |
| `PoolDayData` | Daily | `tvlUSD` or `reserveUSD` |

**Resolution:** Source TVL from `PoolHourData` and forward-fill to 1-minute buckets. Record `tvl_source: "truth_path_hourly_forward_filled"` in audit report.

---

### Pagination

- **Max `first` per query:** **1,000 records**
- **`skip` limit:** Hard ceiling of **5,000** — will silently truncate high-volume pairs

**Required pattern — `id_gt` cursor:**

```graphql
query GetSwaps($pool: String!, $startTs: Int!, $lastId: String!) {
  swaps(
    where: { pool: $pool, timestamp_gte: $startTs, id_gt: $lastId }
    orderBy: id
    orderDirection: asc
    first: 1000
  ) {
    id
    timestamp
    amountUSD
    transaction { id blockNumber }
  }
}
```

```python
last_id = ""
all_swaps = []

while True:
    result = run_query(SWAPS_QUERY, variables={
        "pool": pool_address.lower(),
        "startTs": start_unix_ts,
        "lastId": last_id
    })
    swaps = result["data"]["swaps"]
    if not swaps:
        break
    all_swaps.extend(swaps)
    last_id = swaps[-1]["id"]
    time.sleep(0.25)
```

---

### API Key Requirement

| Plan | Monthly Queries | Cost |
|---|---|---|
| Free | **100,000** | $0 |
| Growth | 100,000 free + metered overage | GRT or credit card |

- API key required. Obtain at https://thegraph.com/studio/
- Set in `.env` as `THEGRAPH_API_KEY=<key>`
- Free budget is sufficient for audit pulls and even the full 90-day fallback pull.

---

### Schema Introspection Query

Run this first against both subgraph endpoints to verify actual field names:

```graphql
{
  __type(name: "Swap") {
    fields {
      name
      type { name kind }
    }
  }
}
```

---

## API-4: Alchemy / QuickNode — Base RPC

**Alchemy docs:** https://www.alchemy.com/docs  
**QuickNode docs:** https://www.quicknode.com/docs

---

### `eth_getBlockByNumber` on Base

**Confirmed supported** on Base mainnet for both Alchemy and QuickNode.

**Request format:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "eth_getBlockByNumber",
  "params": ["0x1234567", false]
}
```

**Key block header fields:**

| Field | Type | Description |
|---|---|---|
| `timestamp` | hex string | Unix timestamp in seconds |
| `baseFeePerGas` | hex string | **EIP-1559 base fee in Wei** ← target field |
| `number` | hex string | Block number |
| `gasLimit` | hex string | Block gas limit |
| `gasUsed` | hex string | Total gas consumed |
| `nonce` | hex string | Always `0x0000000000000000` on Base (L2) |
| `uncles` | array | Always `[]` on Base |

**Extraction:**
```python
block = w3.eth.get_block(block_number)
base_fee_gwei = block["baseFeePerGas"] / 1e9
```

---

### Block Timing

| Metric | Value |
|---|---|
| Average block time | **~2 seconds** (OP Stack — fixed cadence) |
| Blocks per minute | **~30** |
| Blocks per 90 days | **~3,888,000** |
| Samples at 30-block interval | **~129,600 RPC calls** |

---

### Rate Limits and CU Math (Alchemy Free Tier)

| Item | Value |
|---|---|
| Monthly CU budget | 30,000,000 CU |
| Cost per `eth_getBlockByNumber` | 20 CU |
| Total CUs for 90-day gas pull | 2,592,000 CU |
| Budget utilization | **~8.6%** |

**Conclusion: Alchemy free tier is sufficient. No upgrade required.**

Safe pacing: one call per 50ms (20 req/sec) — completes in under 2 hours.

---

### `baseFeePerGas` Specifics

| Item | Confirmed | Notes |
|---|---|---|
| Base uses EIP-1559 | ✅ Yes | Enabled from genesis |
| Correct field | ✅ `baseFeePerGas` | Not `gasPrice` (legacy field) |
| Units | ✅ Wei | Divide by 1e9 for Gwei |
| Available from genesis | ✅ Yes | No minimum block restriction |
| Present on every block | ✅ Yes | All Base blocks |

---

## Recommended Primary Fast Path Source

**Recommendation: GeckoTerminal** — the only viable option.

| Criterion | GeckoTerminal | DexScreener |
|---|---|---|
| Historical 1-min OHLCV | ✅ Yes — up to 6 months | ❌ No — current data only |
| 90-day lookback | ✅ Supported (free tier) | ❌ Not available |
| Base network support | ✅ Explicit (`base` identifier) | ✅ Yes |
| API key required | ❌ No | ❌ No |
| Historical TVL | ❌ No | ❌ No |
| Rate limit (free) | 30 req/min | 300 req/min |
| Requests for 90d pull | ~130 | N/A |

---

## Critical Blockers Found

All blockers below have been resolved in TRD v1.4 and updated documentation.

---

### Blocker 1 — DexScreener has no historical OHLCV endpoint
**Severity: High** | **Status: Resolved in TRD v1.4**

DexScreener exposes only current pair statistics. No historical candle endpoint exists.

**Resolution:** GeckoTerminal designated as sole Fast Path source. DexScreener removed from pipeline.

---

### Blocker 2 — GeckoTerminal provides no historical TVL
**Severity: High** | **Status: Resolved in TRD v1.4**

GeckoTerminal OHLCV endpoint returns no TVL. TVL split path triggers for every pair, every run.

**Resolution:** TVL always sourced from Truth Path. `tvl_source: "truth_path_hourly_forward_filled"` set in all audit reports.

---

### Blocker 3 — The Graph does not provide 1-minute TVL
**Severity: Medium** | **Status: Resolved in TRD v1.4**

Finest TVL resolution available from The Graph is hourly (`PoolHourData`).

**Resolution:** Source TVL from `PoolHourData`, forward-fill to 1-minute buckets. Documented as expected behavior.

---

### Blocker 4 — Aerodrome subgraph staleness risk
**Severity: High** | **Status: Pre-flight check required**

Primary Aerodrome subgraph shows "Updated 2 years ago." May not be indexed to current chain tip.

**Resolution:** Subgraph sync check is mandatory first step in the pipeline. See `ingestion/check_subgraph.py`.

---

### Blocker 5 — Separate subgraphs required for Classic vs. CL pools
**Severity: Medium** | **Status: Resolved in TRD v1.4 and IMPLEMENTATION_GUIDE**

Classic and CL pools use different contracts, events, and swap field names.

**Resolution:** Both schemas documented separately. Pool-type-aware branching required in all Truth Path code.

---

### Blocker 6 — `skip`-based pagination silently fails for large datasets
**Severity: High** | **Status: Resolved in IMPLEMENTATION_GUIDE**

The Graph enforces a hard `skip` ceiling of 5,000 records. High-volume pairs will be silently truncated.

**Resolution:** All queries use `id_gt` cursor-based pagination. `$skip` is prohibited.

---

*End of API Research Findings*
