"""
Phase 4 — Truth Path: Swap Extraction + Aggregation

Fetches raw Swap events from The Graph (Aerodrome subgraph) for given time
windows using id_gt cursor pagination, then aggregates to 1-minute OHLCV
candles using pool-type-aware price conversion.

Usage:
    from ingestion.truth_path import fetch_swaps, aggregate_cl_swaps
"""

import os
import time

import httpx
import polars as pl
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

SUBGRAPH_URL = (
    "https://gateway.thegraph.com/api/"
    + os.environ["THEGRAPH_API_KEY"]
    + "/subgraphs/id/GENunSHWLBXm59mBSgPzQ8metBEp9YDfdqwFr91Av1UM"
)

# Confirmed via schema introspection 2026-04-15
CL_SWAPS_QUERY = """
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
"""


def fetch_swaps(pool: str, start_ts: int, end_ts: int, label: str = "") -> list[dict]:
    """
    Fetch all Swap events for a pool in [start_ts, end_ts] using id_gt pagination.
    Never uses $skip — hard ceiling of 5,000 records would silently truncate.
    """
    last_id = ""
    all_swaps = []
    page = 0

    while True:
        resp = httpx.post(
            SUBGRAPH_URL,
            json={
                "query": CL_SWAPS_QUERY,
                "variables": {
                    "pool": pool.lower(),
                    "start": start_ts,
                    "end": end_ts,
                    "lastId": last_id,
                },
            },
            timeout=30,
        )
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL error: {data['errors']}")

        swaps = data["data"]["swaps"]
        if not swaps:
            break

        all_swaps.extend(swaps)
        last_id = swaps[-1]["id"]
        page += 1
        tag = f"[{label}] " if label else ""
        print(f"  {tag}page {page}: +{len(swaps)} swaps (total: {len(all_swaps):,})", flush=True)
        time.sleep(0.25)

    return all_swaps


def aggregate_cl_swaps(swaps: list[dict]) -> pl.DataFrame:
    """
    Aggregate raw CL pool Swap events into 1-minute OHLCV candles.
    Price derived from sqrtPriceX96: price = (sqrtPriceX96 / 2**96)^2 * 1e18 / 1e6
    (WETH=token0 18 decimals, USDC=token1 6 decimals → USDC per WETH)
    """
    swaps_df = pl.DataFrame(
        {
            "timestamp": [int(s["timestamp"]) for s in swaps],
            "sqrtPriceX96": [int(s["sqrtPriceX96"]) for s in swaps],
            "amountUSD": [float(s["amountUSD"]) for s in swaps],
        }
    )

    return (
        swaps_df
        .with_columns([
            pl.from_epoch("timestamp", time_unit="s").dt.replace_time_zone("UTC"),
            ((pl.col("sqrtPriceX96") / (2**96)) ** 2 * (10**18) / (10**6))
            .alias("price"),
            pl.col("amountUSD").cast(pl.Float64),
        ])
        .with_columns(
            pl.col("timestamp").dt.truncate("1m").alias("bucket")
        )
        .group_by("bucket")
        .agg([
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("amountUSD").sum().alias("volume_usd"),
        ])
        .sort("bucket")
        .rename({"bucket": "timestamp"})
    )
