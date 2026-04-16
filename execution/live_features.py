"""
Live Feature Pipeline

Computes the same 21 features as research/features.py but from live data:
  - Price/OHLCV: last 60 minutes of 1-minute candles from GeckoTerminal
  - Gas: current baseFeePerGas from Base RPC
  - TVL: most recent Sync event reserve0 from pool contract

The feature vector is used by the live model to generate a signal
for the *next* 1-minute candle.

Usage:
    from execution.live_features import LiveFeaturePipeline
    pipeline = LiveFeaturePipeline()
    features = pipeline.get_features()  # returns dict of feature values
"""

import math
import os
import time
import logging
from datetime import datetime, timezone

import httpx
import polars as pl
from web3 import Web3
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

log = logging.getLogger(__name__)

# Pool addresses
POOL_AERO_WETH = "0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6"

# Sync event topic (for TVL from reserves)
SYNC_TOPIC = "0xcf2aa50876cdfbb541206f89af0ee78d44a2abf8d328e37fa4917f982149848a"

# GeckoTerminal
GECKO_URL = "https://api.geckoterminal.com/api/v2/networks/base/pools/{pool}/ohlcv/minute"

# Minimum candles needed to compute all features (60-candle rolling windows)
MIN_CANDLES = 65


class LiveFeaturePipeline:
    def __init__(
        self,
        pool:    str = POOL_AERO_WETH,
        rpc_url: str | None = None,
    ):
        self.pool    = Web3.to_checksum_address(pool)
        self.rpc_url = rpc_url or os.environ["BASE_RPC_URL"]
        self.w3      = Web3(Web3.HTTPProvider(self.rpc_url))
        self.client  = httpx.Client(timeout=15)

    # ── Data fetching ──────────────────────────────────────────────────────────

    def fetch_ohlcv(self, n_candles: int = MIN_CANDLES) -> pl.DataFrame:
        """
        Fetch the last n_candles 1-minute candles from GeckoTerminal.
        Returns DataFrame with columns: timestamp, open, high, low, close, volume_usd.
        """
        url    = GECKO_URL.format(pool=self.pool)
        params = {"aggregate": 1, "limit": min(n_candles, 1000), "currency": "usd"}
        resp   = self.client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()["data"]["attributes"]["ohlcv_list"]
        raw.reverse()  # ascending order

        return pl.DataFrame(
            raw,
            schema=["timestamp", "open", "high", "low", "close", "volume_usd"],
            orient="row",
        ).with_columns([
            pl.from_epoch("timestamp", time_unit="s").dt.replace_time_zone("UTC"),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("volume_usd").cast(pl.Float64),
        ])

    def fetch_gas(self) -> float:
        """Return current baseFeePerGas in Gwei."""
        block = self.w3.eth.get_block("latest")
        return block["baseFeePerGas"] / 1e9

    def fetch_tvl(self) -> float | None:
        """
        Get current WETH reserves from the most recent Sync event.
        Uses raw httpx JSON-RPC (explicit hex block numbers) — web3.py's get_logs
        passes integers which some RPC providers reject with 400.
        Returns raw WETH reserve0 (wei units / 1e18), or None if unavailable.
        Caller multiplies by weth_price to get TVL in USD.
        """
        try:
            head = self.w3.eth.block_number
            resp = self.client.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method":  "eth_getLogs",
                    "params":  [{
                        "address":   self.pool,
                        "topics":    [SYNC_TOPIC],
                        "fromBlock": hex(head - 500),
                        "toBlock":   hex(head),
                    }],
                    "id": 1,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                log.warning(f"TVL RPC error: {result['error']}")
                return None
            logs = result.get("result", [])
            if not logs:
                return None
            last_data = logs[-1]["data"]
            hex_data  = last_data[2:] if last_data.startswith("0x") else last_data
            reserve0  = int(hex_data[:64], 16) / 1e18   # WETH (token0)
            return reserve0
        except Exception as e:
            log.warning(f"TVL fetch failed: {e}")
            return None

    # ── Feature computation ────────────────────────────────────────────────────

    def get_features(self, weth_usd_price: float | None = None) -> dict | None:
        """
        Compute live feature vector for the current candle.

        Returns dict with all 21 AERO/WETH features, or None if insufficient data.
        Features match exactly the training feature set in research/features.py.
        """
        df = self.fetch_ohlcv(n_candles=MIN_CANDLES)
        if len(df) < MIN_CANDLES - 5:
            log.warning(f"Insufficient candles: {len(df)} < {MIN_CANDLES}")
            return None

        gas_gwei  = self.fetch_gas()
        reserve0  = self.fetch_tvl()
        tvl_usd   = (2 * reserve0 * weth_usd_price) if (reserve0 and weth_usd_price) else None

        # Add gas column (uniform for join compatibility)
        df = df.with_columns(pl.lit(gas_gwei).alias("base_fee_gwei"))
        df = df.with_columns(pl.lit(tvl_usd).alias("tvl_usd"))

        # Compute all features (same logic as research/features.py)
        df = df.with_columns([
            (pl.col("close") / pl.col("close").shift(1)).log().alias("ret_1"),
            (pl.col("close") / pl.col("close").shift(5)).log().alias("ret_5"),
            (pl.col("close") / pl.col("close").shift(15)).log().alias("ret_15"),
            (pl.col("close") / pl.col("close").shift(30)).log().alias("ret_30"),
            (pl.col("close") / pl.col("close").shift(60)).log().alias("ret_60"),
        ]).with_columns([
            pl.col("ret_1").rolling_std(5).alias("vol_5"),
            pl.col("ret_1").rolling_std(15).alias("vol_15"),
            pl.col("ret_1").rolling_std(30).alias("vol_30"),
        ]).with_columns([
            (pl.col("vol_5") / pl.col("vol_30")).alias("vol_ratio"),
            (pl.col("volume_usd") / pl.col("volume_usd").rolling_mean(5)).alias("vol_rel_5"),
            (pl.col("volume_usd") / pl.col("volume_usd").rolling_mean(30)).alias("vol_rel_30"),
            (pl.col("volume_usd") / pl.col("volume_usd").rolling_mean(60)).alias("vol_rel_60"),
        ])

        # Range position
        for w in [15, 60]:
            lo  = pl.col("low").rolling_min(w)
            hi  = pl.col("high").rolling_max(w)
            rng = hi - lo
            df  = df.with_columns(
                pl.when(rng > 0).then((pl.col("close") - lo) / rng).otherwise(0.5)
                .alias(f"range_pos_{w}")
            )

        # tvl_norm: normalised TVL. Falls back to 1.0 (the distribution mean) when
        # TVL is unavailable — neutral value, no TVL signal injected.
        tvl_expr = (
            (pl.col("tvl_usd").forward_fill() / pl.col("tvl_usd").forward_fill().rolling_mean(60))
            if tvl_usd is not None
            else pl.lit(1.0)
        )
        df = df.with_columns([
            tvl_expr.alias("tvl_norm"),
            (pl.col("base_fee_gwei") / pl.col("base_fee_gwei").rolling_mean(60)).alias("gas_norm"),
            pl.col("base_fee_gwei").alias("gas_abs"),
        ]).with_columns([
            pl.col("timestamp").dt.hour().alias("hour_utc"),
            (pl.col("timestamp").dt.hour() * (2 * math.pi / 24)).sin().alias("hour_sin"),
            (pl.col("timestamp").dt.hour() * (2 * math.pi / 24)).cos().alias("hour_cos"),
            pl.col("timestamp").diff().dt.total_minutes().alias("gap_minutes"),
        ]).with_columns(
            (pl.col("gap_minutes") > 5).cast(pl.Int8).alias("post_gap")
        )

        # Take last row = current candle
        row = df.drop_nulls().tail(1)
        if len(row) == 0:
            log.warning("All rows null after feature computation")
            return None

        from research.features import FEATURE_COLS_AERO
        available = [c for c in FEATURE_COLS_AERO if c in row.columns]
        return row.select(available).to_dicts()[0]
