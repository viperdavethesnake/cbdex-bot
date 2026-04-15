"""
Phase 5 — Audit Gate (TRD v1.5)

Compares Fast Path (GeckoTerminal) candles against Truth Path (The Graph)
candles for three stratified windows. Generates a pass/fail audit report.

Gate (v1.5 — Pearson correlation removed):
  - mae_pct           < 0.10%
  - volume_error_pct  < 1.00%
  - gap_count_dropped == 0

See TRD v1.5 Section 4 for rationale on correlation removal.

Usage:
    from ingestion.audit import calculate_window_metrics, evaluate_window, generate_report
"""

import json
from datetime import datetime, timezone

import polars as pl

THRESHOLDS = {
    "mae_pct":           ("<",  0.10),
    "volume_error_pct":  ("<",  1.0),
    "gap_count_dropped": ("==", 0),
}


def calculate_window_metrics(fast: pl.DataFrame, truth: pl.DataFrame) -> dict:
    """
    fast / truth: DataFrames with columns [timestamp, open, high, low, close, volume_usd]
    Returns a dict of raw metric values (no pass/fail yet).
    """
    joined = fast.join(truth, on="timestamp", suffix="_truth")

    mae_pct = (
        (joined["close"] - joined["close_truth"]).abs() / joined["close_truth"]
    ).mean() * 100

    vol_err = (
        abs(fast["volume_usd"].sum() - truth["volume_usd"].sum())
        / truth["volume_usd"].sum()
        * 100
    )

    # Zero-volume Truth Path candles (dust / internal contract calls with amountUSD=0
    # or floating-point near-zero ~1e-14) are excluded from gap_count_dropped.
    # GeckoTerminal correctly suppresses sub-cent candles; their absence is not a failure.
    truth_nonzero_ts = set(
        truth.filter(pl.col("volume_usd") >= 0.01)["timestamp"].to_list()
    )
    fast_ts  = set(fast["timestamp"].to_list())
    dropped  = len(truth_nonzero_ts - fast_ts)
    filled   = len(fast_ts - set(truth["timestamp"].to_list()))

    return {
        "mae_pct":           round(float(mae_pct), 4),
        "volume_error_pct":  round(float(vol_err), 4),
        "tvl_error_pct":     None,
        "gap_count_dropped": dropped,
        "gap_count_filled":  filled,
    }


def evaluate_window(metrics: dict) -> bool:
    for key, (op, threshold) in THRESHOLDS.items():
        value = metrics.get(key)
        if value is None:
            continue
        if op == ">"  and not (value > threshold):  return False
        if op == "<"  and not (value < threshold):  return False
        if op == "==" and not (value == threshold): return False
    return True


def generate_report(pair: str, windows_results: list[dict]) -> dict:
    report = {
        "pair":               pair,
        "audit_timestamp":    datetime.now(timezone.utc).isoformat(),
        "fast_path_source":   "GeckoTerminal",
        "tvl_source":         "truth_path_hourly_forward_filled",
        "truth_path_source":  "The Graph / Aerodrome Subgraph",
        "trd_version":        "v1.5",
        "windows":            windows_results,
        "overall_verdict":    "PASS" if all(w["pass"] for w in windows_results) else "FAIL",
    }
    path = f"data/base_mainnet/pairs/{pair}/audit_log.json"
    with open(path, "w") as f:
        json.dump(report, f, indent=2)
    return report
