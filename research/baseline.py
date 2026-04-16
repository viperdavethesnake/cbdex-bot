"""
Phase 2 — Baseline Model

Naive momentum + volatility threshold baseline.
The ML model must beat this after fees and gas on every walk-forward fold.

Baseline logic:
    LONG  if ret_1 > 0 and vol_5 > hurdle
    SHORT if ret_1 < 0 and vol_5 > hurdle
    HOLD  otherwise

This captures the simplest possible signal: is the market moving right now
(ret_1) and is volatility high enough to clear the fee hurdle (vol_5)?
No lookahead, no fitting, no parameters.

Usage:
    from research.baseline import run_baseline
    results = run_baseline("WETH_USDC")
"""

import polars as pl
from research.features import (
    build_features,
    WETH_FEE_HURDLE,
    AERO_FEE_HURDLE,
    AERO_REGIME_THRESHOLD,
)
from research.labels import attach_labels


def _predict_baseline(df: pl.DataFrame, hurdle: float) -> pl.DataFrame:
    """
    Apply the naive baseline rule and return a DataFrame with a pred column.
    pred: 1=LONG, 0=HOLD, -1=SHORT
    """
    return df.with_columns(
        pl.when((pl.col("ret_1") > 0) & (pl.col("vol_5") > hurdle))
          .then(pl.lit(1))
          .when((pl.col("ret_1") < 0) & (pl.col("vol_5") > hurdle))
          .then(pl.lit(-1))
          .otherwise(pl.lit(0))
          .alias("pred")
    )


def _evaluate(df: pl.DataFrame, pair: str) -> dict:
    """
    Compute classification and trading metrics from pred vs label columns.

    Metrics:
        precision_long/short  : of trades taken, what fraction were correct
        recall_long/short     : of profitable candles, what fraction we caught
        accuracy              : overall correct predictions
        trade_rate            : fraction of candles where we took a trade
        correct_trades        : trades where pred == label (excludes HOLD)
        total_trades          : total non-HOLD predictions
        pnl_gross_pct         : sum of label_raw where pred != 0 and correct direction
        fee_cost_pct          : total fee cost of all trades (hurdle * n_trades)
        pnl_net_pct           : pnl_gross - fee_cost
    """
    hurdle = WETH_FEE_HURDLE if pair == "WETH_USDC" else AERO_FEE_HURDLE

    n = len(df)
    trades = df.filter(pl.col("pred") != 0)
    n_trades = len(trades)

    correct = trades.filter(pl.col("pred") == pl.col("label"))
    n_correct = len(correct)

    long_trades  = trades.filter(pl.col("pred") == 1)
    short_trades = trades.filter(pl.col("pred") == -1)
    long_correct  = correct.filter(pl.col("pred") == 1)
    short_correct = correct.filter(pl.col("pred") == -1)

    actual_long  = df.filter(pl.col("label") == 1)
    actual_short = df.filter(pl.col("label") == -1)

    prec_long  = len(long_correct)  / len(long_trades)  if len(long_trades)  > 0 else 0.0
    prec_short = len(short_correct) / len(short_trades) if len(short_trades) > 0 else 0.0
    rec_long   = len(long_correct)  / len(actual_long)  if len(actual_long)  > 0 else 0.0
    rec_short  = len(short_correct) / len(actual_short) if len(actual_short) > 0 else 0.0

    # Gross PnL: sum of label_raw on correct directional trades
    pnl_gross = correct["label_raw"].sum() * 100  # percent

    # Fee cost: each trade costs hurdle% one-way (already a round-trip in the hurdle definition)
    fee_cost = n_trades * hurdle * 100  # percent

    pnl_net = pnl_gross - fee_cost

    return {
        "pair":           pair,
        "n_candles":      n,
        "n_trades":       n_trades,
        "trade_rate_pct": round(n_trades / n * 100, 2),
        "n_correct":      n_correct,
        "precision_long":  round(prec_long,  4),
        "precision_short": round(prec_short, 4),
        "recall_long":     round(rec_long,   4),
        "recall_short":    round(rec_short,  4),
        "accuracy":        round(n_correct / n_trades, 4) if n_trades > 0 else 0.0,
        "pnl_gross_pct":  round(pnl_gross, 4),
        "fee_cost_pct":   round(fee_cost,  4),
        "pnl_net_pct":    round(pnl_net,   4),
    }


def run_baseline(
    pair: str,
    apply_regime_filter: bool = True,
) -> dict:
    """
    Build features, attach labels, apply baseline rule, evaluate.

    Args:
        pair: "WETH_USDC" or "AERO_WETH"
        apply_regime_filter: for AERO/WETH, drop rows where vol_15 < AERO_REGIME_THRESHOLD
                             (the mandatory regime gate from the spec)

    Returns:
        Dict of evaluation metrics.
    """
    df = build_features(pair)
    df = attach_labels(df, pair)

    if pair == "AERO_WETH" and apply_regime_filter:
        before = len(df)
        df = df.filter(pl.col("vol_15") >= AERO_REGIME_THRESHOLD)
        after = len(df)
        print(f"  AERO regime filter: {before:,} -> {after:,} rows  "
              f"({(before-after)/before*100:.1f}% filtered)")

    hurdle = WETH_FEE_HURDLE if pair == "WETH_USDC" else AERO_FEE_HURDLE
    df = _predict_baseline(df, hurdle)
    return _evaluate(df, pair)


def run_walk_forward_baseline(pair: str) -> list[dict]:
    """
    Run the baseline across all 4 walk-forward folds.
    Train window: 60 days. Validate window: 7 days. Step: 7 days.
    Baseline has no training step — just evaluates on each validation fold.

    Returns list of per-fold result dicts.
    """
    import datetime
    df = build_features(pair)
    df = attach_labels(df, pair)

    hurdle = WETH_FEE_HURDLE if pair == "WETH_USDC" else AERO_FEE_HURDLE
    regime = pair == "AERO_WETH"

    start = df["timestamp"].min().replace(tzinfo=None)

    folds = []
    for fold in range(4):
        val_start = start + datetime.timedelta(days=60 + fold * 7)
        val_end   = val_start + datetime.timedelta(days=7)

        val = df.filter(
            (pl.col("timestamp") >= pl.lit(val_start).dt.replace_time_zone("UTC")) &
            (pl.col("timestamp") <  pl.lit(val_end).dt.replace_time_zone("UTC"))
        )

        if regime:
            val = val.filter(pl.col("vol_15") >= AERO_REGIME_THRESHOLD)

        if len(val) == 0:
            continue

        val = _predict_baseline(val, hurdle)
        result = _evaluate(val, pair)
        result["fold"] = fold + 1
        result["val_start"] = str(val_start.date())
        result["val_end"]   = str(val_end.date())
        folds.append(result)

    return folds


def _print_results(results: dict | list[dict]) -> None:
    if isinstance(results, dict):
        results = [results]
    for r in results:
        fold_label = f"  Fold {r['fold']}  {r.get('val_start','')} -> {r.get('val_end','')}" if "fold" in r else f"  {r['pair']} (full dataset)"
        print(fold_label)
        print(f"    Candles: {r['n_candles']:,}  Trades: {r['n_trades']:,}  Trade rate: {r['trade_rate_pct']}%")
        print(f"    Precision  long={r['precision_long']:.3f}  short={r['precision_short']:.3f}")
        print(f"    Recall     long={r['recall_long']:.3f}   short={r['recall_short']:.3f}")
        print(f"    Accuracy:  {r['accuracy']:.3f}")
        print(f"    PnL gross: {r['pnl_gross_pct']:+.2f}%  fees: -{r['fee_cost_pct']:.2f}%  net: {r['pnl_net_pct']:+.2f}%")


if __name__ == "__main__":
    for pair in ["WETH_USDC", "AERO_WETH"]:
        print(f"\n{'='*60}")
        print(f" {pair} — Full Dataset Baseline")
        print(f"{'='*60}")
        r = run_baseline(pair)
        _print_results(r)

        print(f"\n{pair} — Walk-Forward Baseline (4 folds)")
        folds = run_walk_forward_baseline(pair)
        _print_results(folds)
