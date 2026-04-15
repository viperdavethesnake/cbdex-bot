# Implementation Guide: Base DEX Data Acquisition Layer

**Specification Reference:** TRD v1.4  
**Target:** Engineer implementing the data pipeline from scratch

---

## Prerequisites

Before writing any code, complete these pre-flight checks.

- [ ] Alchemy account created — Base mainnet endpoint obtained (free tier sufficient)
- [ ] The Graph API key obtained — https://thegraph.com/studio/
- [ ] Pool addresses recorded (already confirmed — do not substitute):
  - WETH/USDC: `0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59` (CL, 0.05%)
  - AERO/WETH: `0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6` (Classic vAMM, 0.30%)
- [ ] Pool types confirmed and understood — WETH/USDC uses `sqrtPriceX96`, AERO/WETH uses reserve ratio

---

## Phase 0: Environment Scaffolding

```bash
python3.12 -m venv .venv
source .venv/bin/activate

pip install polars httpx web3 python-dotenv
pip freeze > requirements.txt
```

Create `.env`:
```bash
BASE_RPC_URL=https://base-mainnet.g.alchemy.com/v2/<YOUR_KEY>
THEGRAPH_API_KEY=<YOUR_KEY>
# GeckoTerminal requires no API key
```

Create the directory structure:
```bash
mkdir -p data/base_mainnet/pairs/WETH_USDC
mkdir -p data/base_mainnet/pairs/AERO_WETH
mkdir -p data/base_mainnet/network
```

---

## Phase 0.5: Subgraph Sync Check (Required Before Any Truth Path Work)

Before writing a single Truth Path query, verify the Aerodrome subgraph is live and synced.

```python
# ingestion/check_subgraph.py
import httpx, os

SUBGRAPH_ID = "GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM"
API_KEY = os.environ["THEGRAPH_API_KEY"]
URL = f"https://gateway.thegraph.com/api/{API_KEY}/subgraphs/id/{SUBGRAPH_ID}"

query = '{ _meta { block { number } hasIndexingErrors } }'
resp = httpx.post(URL, json={"query": query})
meta = resp.json()["data"]["_meta"]

subgraph_block = meta["block"]["number"]
has_errors = meta["hasIndexingErrors"]

from web3 import Web3
w3 = Web3(Web3.HTTPProvider(os.environ["BASE_RPC_URL"]))
chain_head = w3.eth.block_number

delta = chain_head - subgraph_block
print(f"Chain head:       {chain_head}")
print(f"Subgraph block:   {subgraph_block}")
print(f"Delta:            {delta} blocks (~{delta * 2 / 60:.1f} minutes)")
print(f"Indexing errors:  {has_errors}")

if delta > 1000 or has_errors:
    print("\n⛔ SUBGRAPH IS STALE — Do not proceed with Truth Path queries.")
    print("Options: Bitquery Aerodrome API, newer community subgraph, or eth_getLogs fallback.")
else:
    print("\n✅ Subgraph is synced — safe to proceed.")
```

**Stop here if the check fails.**

---

## Phase 1: Smoke Test (7-Day Validation)

**Always run this before the full 90-day pull.**

### 1.1 Fast Path — GeckoTerminal 7-Day Pull

Write `ingestion/fast_path.py`:

```python
import httpx, time, polars as pl
from datetime import datetime, timezone

BASE_URL = "https://api.geckoterminal.com/api/v2"

def fetch_ohlcv(pool_address: str, days: int = 7) -> pl.DataFrame:
    url = f"{BASE_URL}/networks/base/pools/{pool_address}/ohlcv/minute"
    params = {"aggregate": 1, "limit": 1000, "currency": "usd"}

    end_ts = int(datetime.now(timezone.utc).timestamp())
    target_ts = end_ts - (days * 86400)
    before_ts = end_ts
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
        before_ts = candles[-1][0]   # oldest timestamp in batch
        time.sleep(2.1)              # 30 req/min → 2s between calls

    all_candles.reverse()  # returned newest-first — reverse for ascending order

    return pl.DataFrame(
        all_candles,
        schema=["timestamp", "open", "high", "low", "close", "volume_usd"],
        orient="row"
    ).with_columns([
        pl.from_epoch("timestamp", time_unit="s").dt.replace_time_zone("UTC"),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume_usd").cast(pl.Float64),
    ]).with_columns(
        pl.lit(None).cast(pl.Float64).alias("tvl_usd")   # GeckoTerminal has no TVL
    )
```

### 1.2 Write and Verify

```python
df = fetch_ohlcv("0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59", days=7)
df.write_parquet("data/base_mainnet/pairs/WETH_USDC/smoke_test_7d.parquet")

df_check = pl.read_parquet("data/base_mainnet/pairs/WETH_USDC/smoke_test_7d.parquet")
assert df_check.schema["close"] == pl.Float64
assert df_check["timestamp"].is_duplicated().sum() == 0, "Duplicate timestamps"
assert df_check.null_count()["close"][0] == 0, "Null prices"
assert df_check.null_count()["volume_usd"][0] == 0, "Null volumes"
# tvl_usd is expected to be null — do not assert on it
print(f"Row count: {len(df_check)} (expected ~10,080 for 7 days)")
```

**Stop here if assertions fail.**

---

## Phase 2: Full 90-Day Fast Path Pull

```python
for pair, address in [
    ("WETH_USDC", "0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59"),
    ("AERO_WETH", "0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6"),
]:
    df = fetch_ohlcv(address, days=90)
    df.write_parquet(f"data/base_mainnet/pairs/{pair}/candidate_90d.parquet")
    print(f"{pair}: {len(df)} rows")
```

---

## Phase 3: Stratified Sampling

```python
df = pl.read_parquet("data/base_mainnet/pairs/WETH_USDC/candidate_90d.parquet")

daily_variance = (
    df.with_columns(pl.col("timestamp").dt.date().alias("date"))
    .group_by("date")
    .agg(pl.col("close").var().alias("variance"))
    .sort("date")
)

median_var = daily_variance["variance"].median()

spike_date = daily_variance.sort("variance", descending=True)["date"][0]
flat_date  = daily_variance.sort("variance")["date"][0]
mean_date  = (
    daily_variance
    .with_columns((pl.col("variance") - median_var).abs().alias("dist"))
    .sort("dist")["date"][0]
)

print(f"Spike: {spike_date} | Flat: {flat_date} | Mean: {mean_date}")
```

---

## Phase 4: Truth Path Extraction

### 4.1 Schema Introspection (Run First)

```python
INTROSPECT = '{ __type(name: "Swap") { fields { name type { name kind } } } }'
```

Run against both CL and Classic subgraph endpoints before writing production queries.

### 4.2 GraphQL Query — CL Pool (WETH/USDC)

```graphql
query GetCLSwaps($pool: String!, $start: Int!, $end: Int!, $lastId: String!) {
  swaps(
    where: { pool: $pool, timestamp_gte: $start, timestamp_lte: $end, id_gt: $lastId }
    orderBy: id
    orderDirection: asc
    first: 1000
  ) {
    id
    timestamp
    amount0
    amount1
    sqrtPriceX96
    tick
    amountUSD
    transaction { id blockNumber }
  }
}
```

### 4.3 GraphQL Query — Classic Pool (AERO/WETH)

```graphql
query GetClassicSwaps($pool: String!, $start: Int!, $end: Int!, $lastId: String!) {
  swaps(
    where: { pool: $pool, timestamp_gte: $start, timestamp_lte: $end, id_gt: $lastId }
    orderBy: id
    orderDirection: asc
    first: 1000
  ) {
    id
    timestamp
    amount0In
    amount0Out
    amount1In
    amount1Out
    amountUSD
    transaction { id blockNumber }
  }
}
```

### 4.4 Pagination Loop (id_gt — Required)

> ⚠️ **Do not use `$skip`.** Hard ceiling of 5,000 records. High-volume pairs will be silently truncated.

```python
def fetch_swaps(pool: str, start_ts: int, end_ts: int, query: str) -> list:
    last_id = ""
    all_swaps = []

    while True:
        result = run_graphql(query, variables={
            "pool": pool.lower(),
            "start": start_ts,
            "end": end_ts,
            "lastId": last_id
        })
        swaps = result["data"]["swaps"]
        if not swaps:
            break
        all_swaps.extend(swaps)
        last_id = swaps[-1]["id"]
        time.sleep(0.25)

    return all_swaps
```

### 4.5 TVL From PoolHourData

```graphql
query GetTVL($pool: String!, $start: Int!, $end: Int!) {
  poolHourDatas(
    where: { pool: $pool, periodStartUnix_gte: $start, periodStartUnix_lte: $end }
    orderBy: periodStartUnix
    orderDirection: asc
    first: 1000
  ) {
    periodStartUnix
    tvlUSD
  }
}
```

Forward-fill to 1-minute buckets:
```python
tvl_df = pl.DataFrame(hourly_tvl).with_columns(
    pl.from_epoch("periodStartUnix", time_unit="s").dt.replace_time_zone("UTC").alias("timestamp")
)
candles = candles.join_asof(tvl_df, on="timestamp", strategy="backward")
```

### 4.6 Price Aggregation — Pool-Type Aware

```python
def aggregate_cl_swaps(swaps_df: pl.DataFrame) -> pl.DataFrame:
    """For CL pools (WETH/USDC) — price from sqrtPriceX96."""
    return (
        swaps_df
        .with_columns([
            ((pl.col("sqrtPriceX96") / (2**96)) ** 2
             * (10**18) / (10**6)).alias("price"),
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")).dt.truncate("1m").alias("bucket")
        ])
        .group_by("bucket")
        .agg([
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("amountUSD").sum().alias("volume_usd"),
        ])
        .sort("bucket")
    )

def aggregate_classic_swaps(swaps_df: pl.DataFrame) -> pl.DataFrame:
    """For Classic pools (AERO/WETH) — price from reserve ratio."""
    return (
        swaps_df
        .with_columns([
            pl.when(pl.col("amount0Out") > 0)
              .then(pl.col("amount1In") / pl.col("amount0Out"))
              .otherwise(pl.col("amount1Out") / pl.col("amount0In"))
              .alias("price"),
            pl.col("timestamp").cast(pl.Datetime("us", "UTC")).dt.truncate("1m").alias("bucket")
        ])
        .group_by("bucket")
        .agg([
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("amountUSD").sum().alias("volume_usd"),
        ])
        .sort("bucket")
    )
```

---

## Phase 5: Audit Execution

### 5.1 Metric Calculation

```python
from scipy.stats import pearsonr

def calculate_window_metrics(fast: pl.DataFrame, truth: pl.DataFrame) -> dict:
    joined = fast.join(truth, on="timestamp", suffix="_truth")
    corr, _ = pearsonr(joined["close"].to_list(), joined["close_truth"].to_list())
    mae_pct = ((joined["close"] - joined["close_truth"]).abs() / joined["close_truth"]).mean() * 100
    vol_err = abs(fast["volume_usd"].sum() - truth["volume_usd"].sum()) / truth["volume_usd"].sum() * 100
    dropped = len(set(truth["timestamp"].to_list()) - set(fast["timestamp"].to_list()))
    filled  = len(set(fast["timestamp"].to_list()) - set(truth["timestamp"].to_list()))

    return {
        "price_correlation": round(corr, 6),
        "mae_pct": round(mae_pct, 4),
        "volume_error_pct": round(vol_err, 4),
        "tvl_error_pct": None,
        "gap_count_dropped": dropped,
        "gap_count_filled": filled,
    }
```

### 5.2 Pass/Fail Gate

```python
THRESHOLDS = {
    "price_correlation": (">", 0.999),
    "mae_pct":           ("<", 0.10),
    "volume_error_pct":  ("<", 1.0),
    "gap_count_dropped": ("==", 0),
}

def evaluate_window(metrics: dict) -> bool:
    for key, (op, threshold) in THRESHOLDS.items():
        value = metrics.get(key)
        if value is None:
            continue
        if op == ">" and not (value > threshold): return False
        if op == "<" and not (value < threshold): return False
        if op == "==" and not (value == threshold): return False
    return True
```

### 5.3 Generate Report

```python
import json
from datetime import datetime, timezone

def generate_report(pair, windows_results):
    report = {
        "pair": pair,
        "audit_timestamp": datetime.now(timezone.utc).isoformat(),
        "fast_path_source": "GeckoTerminal",
        "tvl_source": "truth_path_hourly_forward_filled",
        "truth_path_source": "The Graph / Aerodrome Subgraph",
        "windows": windows_results,
        "overall_verdict": "PASS" if all(w["pass"] for w in windows_results) else "FAIL",
    }
    path = f"data/base_mainnet/pairs/{pair}/audit_log.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Audit report: {report['overall_verdict']} → {path}")
    return report
```

**Always archive the report — even on FAIL.**

---

## Phase 6: Gas Data Pull

```python
# ingestion/gas.py
from web3 import Web3
import polars as pl, time, os

w3 = Web3(Web3.HTTPProvider(os.environ["BASE_RPC_URL"]))

def get_base_fee_series(start_block: int, end_block: int, sample_every: int = 30) -> pl.DataFrame:
    records = []
    for block_num in range(start_block, end_block, sample_every):
        block = w3.eth.get_block(block_num)
        records.append({
            "block_number": block_num,
            "timestamp": block["timestamp"],
            "base_fee_gwei": block["baseFeePerGas"] / 1e9,
        })
        time.sleep(0.05)   # 20 req/sec — well within Alchemy free tier
    return pl.DataFrame(records).with_columns(
        pl.from_epoch("timestamp", time_unit="s").dt.replace_time_zone("UTC")
    )
```

Verify:
```python
gas_df = pl.read_parquet("data/base_mainnet/network/gas_prices_90d.parquet")
assert gas_df.null_count()["base_fee_gwei"][0] == 0
print(f"Gas records: {len(gas_df)} (expected ~129,600 for 90d @ 30-block sampling)")
```

---

## Phase 7: Final Storage

### Pass Path

```python
df_ohlcv = pl.read_parquet("data/base_mainnet/pairs/WETH_USDC/candidate_90d.parquet")
df_final = df_ohlcv.join_asof(tvl_df, on="timestamp", strategy="backward")
df_final.write_parquet("data/base_mainnet/pairs/WETH_USDC/final_90d.parquet")

df = pl.read_parquet("data/base_mainnet/pairs/WETH_USDC/final_90d.parquet")
assert df["timestamp"].is_duplicated().sum() == 0
assert df.null_count()["close"][0] == 0
assert df.null_count()["volume_usd"][0] == 0
assert len(df) <= 129_600
print(f"Final rows: {len(df)} | Gaps: {129_600 - len(df)}")
```

### Fallback Path

> **Warning:** Full 90-day Truth Path pull requires 1,000+ paginated GraphQL queries. Use `id_gt` throughout. Budget 8–20 hours. Build exponential backoff before starting.

---

## Definition of Done

### Branch A: Pass Path
- [ ] Subgraph sync check passed
- [ ] 90-day OHLCV pulled from GeckoTerminal for both pairs
- [ ] Stratified samples (Spike, Flat, Mean) identified
- [ ] Truth Path swaps pulled for 3 windows (id_gt pagination)
- [ ] TVL pulled from PoolHourData, forward-filled to 1-minute
- [ ] Audit Report generated, all thresholds met, archived
- [ ] Gas series pulled, null-checked, stored
- [ ] `final_90d.parquet` written with TVL populated for both pairs

### Branch B: Fallback Path
- [ ] Audit Report generated as FAIL and archived
- [ ] Full 90-day Truth Path pull (OHLCV + TVL) complete
- [ ] All queries used id_gt cursor pagination
- [ ] Row count ≤ 129,600, gaps documented
- [ ] Gas series pulled and stored
- [ ] `final_90d.parquet` written for both pairs
