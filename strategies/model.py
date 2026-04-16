"""
Phase 2 — Random Forest Model

Multiclass classifier: LONG (1) / HOLD (0) / SHORT (-1)
Uses class_weight='balanced' to handle the heavy HOLD imbalance.
Probability threshold tuning: only trade when P(direction) > threshold.

Walk-forward evaluation:
    Train: 60 days | Validate: 7 days | Step: 7 days | Folds: 4

Threshold selection: tuned on training fold to maximise net PnL.
Applied to validation fold without refitting.

Usage:
    from strategies.model import run_model
    results = run_model("WETH_USDC")
"""

import datetime
import math

import polars as pl
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from research.features import (
    build_features,
    FEATURE_COLS_WETH,
    FEATURE_COLS_AERO,
    WETH_FEE_HURDLE,
    AERO_FEE_HURDLE,
    AERO_REGIME_THRESHOLD,
)
from research.labels import attach_labels
from backtest.simulator import Simulator, print_summary

# Walk-forward parameters
TRAIN_DAYS = 60
VAL_DAYS   = 7
N_FOLDS    = 4

# Probability thresholds to search over training fold
THRESHOLD_GRID = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]

# Random Forest hyperparameters (first pass — interpretable defaults)
RF_PARAMS = {
    "n_estimators":     300,
    "max_depth":        8,
    "min_samples_leaf": 50,   # prevents overfitting on rare LONG/SHORT candles
    "class_weight":     "balanced",
    "random_state":     42,
    "n_jobs":           -1,
}


def _apply_threshold(
    df: pl.DataFrame,
    prob_long: list[float],
    prob_short: list[float],
    threshold: float,
) -> pl.DataFrame:
    """Convert class probabilities to predictions using a confidence threshold."""
    preds = []
    for pl_, ps in zip(prob_long, prob_short):
        if pl_ >= threshold:
            preds.append(1)
        elif ps >= threshold:
            preds.append(-1)
        else:
            preds.append(0)
    return df.with_columns([
        pl.Series("pred",           preds),
        pl.Series("pred_prob_long",  prob_long),
        pl.Series("pred_prob_short", prob_short),
    ])


def _tune_threshold(
    train_df: pl.DataFrame,
    prob_long: list[float],
    prob_short: list[float],
    pair: str,
    position_usd: float,
) -> float:
    """Find threshold that maximises net PnL on the training fold."""
    sim = Simulator(pair, position_usd=position_usd)
    best_threshold = 0.50
    best_net = float("-inf")

    for t in THRESHOLD_GRID:
        df_t = _apply_threshold(train_df, prob_long, prob_short, t)
        result = sim.run(df_t)
        net = result.pnl_net_usd
        if net > best_net:
            best_net = net
            best_threshold = t

    return best_threshold


def run_model(
    pair: str,
    position_usd: float = 50.0,
    capital_usd: float  = 1000.0,
    verbose: bool       = True,
) -> list[dict]:
    """
    Run walk-forward Random Forest evaluation.

    Returns list of per-fold result dicts from Simulator.summary().
    """
    df = build_features(pair)
    df = attach_labels(df, pair)

    if pair == "AERO_WETH":
        before = len(df)
        df = df.filter(pl.col("vol_15") >= AERO_REGIME_THRESHOLD)
        if verbose:
            print(f"  AERO regime filter: {before:,} -> {len(df):,} rows")

    feature_cols = FEATURE_COLS_AERO if pair == "AERO_WETH" else FEATURE_COLS_WETH
    hurdle = WETH_FEE_HURDLE if pair == "WETH_USDC" else AERO_FEE_HURDLE

    start_ts = df["timestamp"].min()
    fold_results = []
    sim = Simulator(pair, position_usd=position_usd, capital_usd=capital_usd)

    for fold in range(N_FOLDS):
        # Walk-forward split
        val_start = start_ts + pl.duration(days=TRAIN_DAYS + fold * VAL_DAYS)
        val_end   = val_start + pl.duration(days=VAL_DAYS)
        train_end = val_start

        train_df = df.filter(pl.col("timestamp") < train_end)
        val_df   = df.filter(
            (pl.col("timestamp") >= val_start) &
            (pl.col("timestamp") <  val_end)
        )

        if len(train_df) < 500 or len(val_df) < 10:
            if verbose:
                print(f"  Fold {fold+1}: insufficient data, skipping")
            continue

        if verbose:
            val_s = str(val_start)[:10]
            val_e = str(val_end)[:10]
            print(f"\n  Fold {fold+1}  validate {val_s} -> {val_e}  "
                  f"(train: {len(train_df):,} rows, val: {len(val_df):,} rows)")

        # Prepare arrays
        X_train = train_df[feature_cols].to_numpy()
        y_train = train_df["label"].to_numpy()
        X_val   = val_df[feature_cols].to_numpy()

        # Fit model
        model = Pipeline([
            ("scaler", StandardScaler()),
            ("rf",     RandomForestClassifier(**RF_PARAMS)),
        ])
        model.fit(X_train, y_train)

        # Probabilities on training fold for threshold tuning
        classes = list(model.classes_)
        train_probs = model.predict_proba(X_train)
        idx_long  = classes.index(1)  if 1  in classes else None
        idx_short = classes.index(-1) if -1 in classes else None

        train_prob_long  = [float(p[idx_long])  if idx_long  is not None else 0.0 for p in train_probs]
        train_prob_short = [float(p[idx_short]) if idx_short is not None else 0.0 for p in train_probs]

        best_t = _tune_threshold(train_df, train_prob_long, train_prob_short, pair, position_usd)
        if verbose:
            print(f"    Best threshold (train): {best_t:.2f}")

        # Probabilities on validation fold
        val_probs = model.predict_proba(X_val)
        val_prob_long  = [float(p[idx_long])  if idx_long  is not None else 0.0 for p in val_probs]
        val_prob_short = [float(p[idx_short]) if idx_short is not None else 0.0 for p in val_probs]

        val_pred = _apply_threshold(val_df, val_prob_long, val_prob_short, best_t)
        result = sim.run(val_pred)

        # Feature importances
        rf = model.named_steps["rf"]
        importances = sorted(
            zip(feature_cols, rf.feature_importances_),
            key=lambda x: x[1], reverse=True
        )
        top5 = [(f, round(imp, 4)) for f, imp in importances[:5]]

        if verbose:
            print_summary(result, label=f"Fold {fold+1}")
            print(f"    Top features: {top5}")

        s = result.summary()
        s["fold"] = fold + 1
        s["val_start"] = str(val_start)[:10]
        s["val_end"]   = str(val_end)[:10]
        s["threshold"] = best_t
        s["top_features"] = top5
        fold_results.append(s)

    if verbose and fold_results:
        avg_net = sum(r["pnl_net_usd"] for r in fold_results) / len(fold_results)
        avg_prec = sum(r["precision"] for r in fold_results) / len(fold_results)
        avg_roi  = sum(r["roi_annualised_pct"] for r in fold_results) / len(fold_results)
        print(f"\n  {'='*50}")
        print(f"  {pair} Average across {len(fold_results)} folds:")
        print(f"    Net PnL: ${avg_net:+.2f}  Precision: {avg_prec:.3f}  Ann. ROI: {avg_roi:+.1f}%")

    return fold_results


if __name__ == "__main__":
    import sys

    pairs = sys.argv[1:] if len(sys.argv) > 1 else ["WETH_USDC", "AERO_WETH"]

    for pair in pairs:
        print(f"\n{'='*60}")
        print(f" {pair} — Random Forest Walk-Forward")
        print(f"{'='*60}")
        run_model(pair, verbose=True)
