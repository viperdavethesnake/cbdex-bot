"""
Phase 1 — Fast Path Ingestion

Pulls 1-minute OHLCV candles from GeckoTerminal for a given pool address.
TVL is not available from GeckoTerminal — tvl_usd column will be null.

Usage:
    from ingestion.fast_path import fetch_ohlcv
    df = fetch_ohlcv("0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59", days=90)
"""

import time
from datetime import datetime, timezone

import httpx
import polars as pl

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

        # Retry up to 3 times on transient network errors
        for attempt in range(3):
            try:
                resp = httpx.get(url, params=params, timeout=30)
                break
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                if attempt == 2:
                    raise
                wait = (attempt + 1) * 4
                print(f"  Network error ({exc.__class__.__name__}), retrying in {wait}s...")
                time.sleep(wait)

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
