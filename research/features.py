"""
Phase 2 — Feature Engineering (FEATURE_ENGINEERING_SPEC.md)

Builds the ML feature matrix for both pairs from final_90d.parquet files.
All rolling windows are index-based to handle AERO/WETH's sparse candles correctly.
No lookahead: features are computed on data up to and including t.
Label is the next candle's log return (computed in labels.py).

Usage:
    from research.features import build_features
    df = build_features("WETH_USDC")   # or "AERO_WETH"
"""

import math
from pathlib import Path

import polars as pl

DATA_BASE = Path("data/base_mainnet")

WETH_FEE_HURDLE = 0.0012   # 0.12% round-trip fee for WETH/USDC
AERO_FEE_HURDLE = 0.0065   # 0.65% round-trip fee for AERO/WETH
AERO_REGIME_THRESHOLD = 0.0098  # 1.5x AERO fee hurdle (vol_15 gate)


def _load_pair(pair: str) -> pl.DataFrame:
    path = DATA_BASE / "pairs" / pair / "final_90d.parquet"
    return pl.read_parquet(path).sort("timestamp")


def _load_gas() -> pl.DataFrame:
    return pl.read_parquet(DATA_BASE / "network" / "gas_prices_90d.parquet").sort("timestamp")


def _join_gas(df: pl.DataFrame, gas: pl.DataFrame) -> pl.DataFrame:
    """Attach gas price to each candle via backward join_asof."""
    gas_slim = gas.select(["timestamp", "base_fee_gwei"])
    return df.join_asof(gas_slim, on="timestamp", strategy="backward")


def _price_momentum(df: pl.DataFrame) -> pl.DataFrame:
    """Log returns at 1, 5, 15, 30, 60 candle lags (index-based)."""
    return df.with_columns([
        (pl.col("close") / pl.col("close").shift(1)).log().alias("ret_1"),
        (pl.col("close") / pl.col("close").shift(5)).log().alias("ret_5"),
        (pl.col("close") / pl.col("close").shift(15)).log().alias("ret_15"),
        (pl.col("close") / pl.col("close").shift(30)).log().alias("ret_30"),
        (pl.col("close") / pl.col("close").shift(60)).log().alias("ret_60"),
    ])


def _realized_volatility(df: pl.DataFrame) -> pl.DataFrame:
    """Rolling std of ret_1 at 5, 15, 30 candle windows. vol_ratio = vol_5/vol_30."""
    return df.with_columns([
        pl.col("ret_1").rolling_std(window_size=5).alias("vol_5"),
        pl.col("ret_1").rolling_std(window_size=15).alias("vol_15"),
        pl.col("ret_1").rolling_std(window_size=30).alias("vol_30"),
    ]).with_columns([
        (pl.col("vol_5") / pl.col("vol_30")).alias("vol_ratio"),
    ])


def _relative_volume(df: pl.DataFrame) -> pl.DataFrame:
    """Current volume / rolling mean volume at 5, 30, 60 candle windows."""
    return df.with_columns([
        (pl.col("volume_usd") / pl.col("volume_usd").rolling_mean(window_size=5)).alias("vol_rel_5"),
        (pl.col("volume_usd") / pl.col("volume_usd").rolling_mean(window_size=30)).alias("vol_rel_30"),
        (pl.col("volume_usd") / pl.col("volume_usd").rolling_mean(window_size=60)).alias("vol_rel_60"),
    ])


def _range_position(df: pl.DataFrame) -> pl.DataFrame:
    """Where is close within the recent high/low range (0=bottom, 1=top)."""
    for w in [15, 60]:
        lo = pl.col("low").rolling_min(window_size=w)
        hi = pl.col("high").rolling_max(window_size=w)
        rng = hi - lo
        # Avoid division by zero on flat candles
        pos = pl.when(rng > 0).then((pl.col("close") - lo) / rng).otherwise(0.5)
        df = df.with_columns(pos.alias(f"range_pos_{w}"))
    return df


def _tvl_feature(df: pl.DataFrame) -> pl.DataFrame:
    """TVL normalised to its 60-candle rolling mean."""
    df = df.with_columns(
        pl.col("tvl_usd").forward_fill().alias("tvl_usd")
    )
    return df.with_columns([
        (pl.col("tvl_usd") / pl.col("tvl_usd").rolling_mean(window_size=60)).alias("tvl_norm"),
    ])


def _gas_features(df: pl.DataFrame) -> pl.DataFrame:
    """Normalised gas (relative to 60-candle mean) and raw gas."""
    return df.with_columns([
        (pl.col("base_fee_gwei") / pl.col("base_fee_gwei").rolling_mean(window_size=60)).alias("gas_norm"),
        pl.col("base_fee_gwei").alias("gas_abs"),
    ])


def _time_features(df: pl.DataFrame) -> pl.DataFrame:
    """Cyclical hour-of-day encoding."""
    return df.with_columns([
        pl.col("timestamp").dt.hour().alias("hour_utc"),
        (pl.col("timestamp").dt.hour() * (2 * math.pi / 24)).sin().alias("hour_sin"),
        (pl.col("timestamp").dt.hour() * (2 * math.pi / 24)).cos().alias("hour_cos"),
    ])


def _gap_features(df: pl.DataFrame) -> pl.DataFrame:
    """Gap duration since previous active candle (AERO/WETH only)."""
    return df.with_columns([
        pl.col("timestamp").diff().dt.total_minutes().alias("gap_minutes"),
    ]).with_columns([
        (pl.col("gap_minutes") > 5).cast(pl.Int8).alias("post_gap"),
    ])


FEATURE_COLS_WETH = [
    "ret_1", "ret_5", "ret_15", "ret_30", "ret_60",
    "vol_5", "vol_15", "vol_30", "vol_ratio",
    "vol_rel_5", "vol_rel_30", "vol_rel_60",
    "range_pos_15", "range_pos_60",
    "tvl_norm",
    "gas_norm", "gas_abs",
    "hour_sin", "hour_cos",
]

FEATURE_COLS_AERO = FEATURE_COLS_WETH + ["gap_minutes", "post_gap"]


def build_features(pair: str, drop_nulls: bool = True) -> pl.DataFrame:
    """
    Build the full feature matrix for a pair.

    Args:
        pair: "WETH_USDC" or "AERO_WETH"
        drop_nulls: if True, drop rows where any feature is null
                    (caused by rolling window warm-up at the start)

    Returns:
        DataFrame with timestamp + all feature columns.
        Label column not included here — see labels.py.
    """
    df = _load_pair(pair)
    gas = _load_gas()

    df = _join_gas(df, gas)
    df = _price_momentum(df)
    df = _realized_volatility(df)
    df = _relative_volume(df)
    df = _range_position(df)
    df = _tvl_feature(df)
    df = _gas_features(df)
    df = _time_features(df)

    if pair == "AERO_WETH":
        df = _gap_features(df)
        feature_cols = FEATURE_COLS_AERO
    else:
        feature_cols = FEATURE_COLS_WETH

    keep = ["timestamp", "open", "high", "low", "close", "volume_usd", "tvl_usd"] + feature_cols
    df = df.select([c for c in keep if c in df.columns])

    if drop_nulls:
        df = df.drop_nulls(subset=feature_cols)

    return df


if __name__ == "__main__":
    for pair in ["WETH_USDC", "AERO_WETH"]:
        df = build_features(pair)
        feat_cols = FEATURE_COLS_AERO if pair == "AERO_WETH" else FEATURE_COLS_WETH
        print(f"\n{pair}: {len(df):,} rows after feature build")
        print(f"  Columns: {df.columns}")
        print(f"  Null counts: { {c: df[c].null_count() for c in feat_cols if df[c].null_count() > 0} }")
        print(f"  ret_1  mean={df['ret_1'].mean()*100:.4f}%  std={df['ret_1'].std()*100:.4f}%")
        print(f"  vol_5  mean={df['vol_5'].mean()*100:.4f}%")
        print(f"  gas_abs mean={df['gas_abs'].mean():.4f} Gwei")
