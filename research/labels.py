"""
Phase 2 — Label Generation

Attaches the next-candle log return label to the feature matrix.
For AERO/WETH, drops rows where the next candle is >10 minutes away
(model should not predict across large gaps).

Label classes:
    LONG  = 1  : next return >  +hurdle
    HOLD  = 0  : |next return| <= hurdle
    SHORT = -1 : next return <  -hurdle

Usage:
    from research.labels import attach_labels
    df = attach_labels(feature_df, pair="WETH_USDC")
"""

import polars as pl
from research.features import WETH_FEE_HURDLE, AERO_FEE_HURDLE

MAX_GAP_MINUTES = 10   # AERO/WETH: drop label if next candle is >10 min away


def attach_labels(df: pl.DataFrame, pair: str) -> pl.DataFrame:
    """
    Add label_raw (continuous next return) and label (LONG/HOLD/SHORT class).

    Args:
        df: feature DataFrame from build_features() — must be sorted by timestamp
        pair: "WETH_USDC" or "AERO_WETH"

    Returns:
        DataFrame with label_raw (float) and label (int: 1/0/-1) columns.
        Rows with no valid next candle are dropped.
    """
    hurdle = WETH_FEE_HURDLE if pair == "WETH_USDC" else AERO_FEE_HURDLE

    df = df.sort("timestamp")

    # Next candle log return (shift -1 = look forward one candle)
    df = df.with_columns([
        (pl.col("close").shift(-1) / pl.col("close")).log().alias("label_raw"),
        pl.col("timestamp").diff(n=-1).dt.total_minutes().abs().alias("next_gap_minutes"),
    ])

    # Drop last row (no next candle)
    df = df.filter(pl.col("label_raw").is_not_null())

    # AERO/WETH: drop rows where the next candle is too far away
    if pair == "AERO_WETH":
        df = df.filter(pl.col("next_gap_minutes") <= MAX_GAP_MINUTES)

    # Ternary label: 1=LONG, 0=HOLD, -1=SHORT
    df = df.with_columns(
        pl.when(pl.col("label_raw") > hurdle)
          .then(pl.lit(1))
          .when(pl.col("label_raw") < -hurdle)
          .then(pl.lit(-1))
          .otherwise(pl.lit(0))
          .alias("label")
    )

    return df.drop("next_gap_minutes")


if __name__ == "__main__":
    from research.features import build_features

    for pair, hurdle in [("WETH_USDC", WETH_FEE_HURDLE), ("AERO_WETH", AERO_FEE_HURDLE)]:
        features = build_features(pair)
        df = attach_labels(features, pair)
        n = len(df)
        n_long  = (df["label"] == 1).sum()
        n_short = (df["label"] == -1).sum()
        n_hold  = (df["label"] == 0).sum()
        print(f"\n{pair}: {n:,} labelled rows")
        print(f"  LONG:  {n_long:,}  ({n_long/n*100:.1f}%)")
        print(f"  HOLD:  {n_hold:,}  ({n_hold/n*100:.1f}%)")
        print(f"  SHORT: {n_short:,}  ({n_short/n*100:.1f}%)")
        print(f"  label_raw std: {df['label_raw'].std()*100:.4f}%")
