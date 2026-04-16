"""
Paper Trading Runner (Phase 2)

Runs the AERO/WETH model in paper trading mode:
  - Fetches live features every minute
  - Generates a signal from the trained RF model
  - Logs the signal and hypothetical trade (no real money)
  - Tracks cumulative paper PnL vs actual market movement

Kill switch: create a .kill file in the working directory to halt.
Daily loss limit: configurable, halts for 24h if breached.

Usage:
    python execution/paper_trader.py
"""

import json
import logging
import math
import os
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import polars as pl
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

from execution.live_features import LiveFeaturePipeline
from research.features import FEATURE_COLS_AERO, AERO_REGIME_THRESHOLD, AERO_FEE_HURDLE

# ── Configuration ──────────────────────────────────────────────────────────────

POSITION_USD      = 50.0
CAPITAL_USD       = 1000.0
DAILY_LOSS_LIMIT  = 50.0    # halt if daily paper loss > $50
MODEL_PATH        = Path("models/aero_weth_rf.pkl")
LOG_PATH          = Path("logs/paper_trades.jsonl")
THRESHOLD         = 0.70
POOL_FEE_RT       = 0.006   # 0.30% * 2 (round-trip)
GAS_EST_USD       = 0.02

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler("logs/paper_trader.log"),
    ],
)
log = logging.getLogger(__name__)


# ── Model loading ──────────────────────────────────────────────────────────────

def load_model():
    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_PATH}. "
            "Run strategies/model.py with save_model=True first."
        )
    with open(MODEL_PATH, "rb") as f:
        return pickle.load(f)


# ── Paper trade logger ─────────────────────────────────────────────────────────

def log_trade(record: dict) -> None:
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── Daily loss tracker ─────────────────────────────────────────────────────────

class DailyLossTracker:
    def __init__(self, limit: float):
        self.limit      = limit
        self.day        = datetime.now(timezone.utc).date()
        self.daily_loss = 0.0

    def record(self, pnl: float) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day        = today
            self.daily_loss = 0.0
        if pnl < 0:
            self.daily_loss += abs(pnl)

    def is_halted(self) -> bool:
        if self.daily_loss >= self.limit:
            log.warning(
                f"Daily loss limit breached: ${self.daily_loss:.2f} >= ${self.limit:.2f}. "
                "Halting for remainder of day."
            )
            return True
        return False


# ── Main loop ──────────────────────────────────────────────────────────────────

def run_paper_trader() -> None:
    log.info("Paper trader starting  pair=AERO/WETH  capital=$%.0f  position=$%.0f",
             CAPITAL_USD, POSITION_USD)

    model    = load_model()
    pipeline = LiveFeaturePipeline()
    tracker  = DailyLossTracker(limit=DAILY_LOSS_LIMIT)
    classes  = list(model.classes_)
    idx_long  = classes.index(1)  if 1  in classes else None
    idx_short = classes.index(-1) if -1 in classes else None

    cumulative_pnl = 0.0
    open_position  = None   # {direction, entry_price, entry_candle_ts, entry_ts}

    while True:
        # Kill switch
        if Path(".kill").exists():
            log.info("Kill switch active. Shutting down.")
            break

        if tracker.is_halted():
            log.info("Daily loss limit active. Sleeping until midnight UTC.")
            now   = datetime.now(timezone.utc)
            secs  = (86400 - (now.hour * 3600 + now.minute * 60 + now.second)) + 60
            time.sleep(secs)
            continue

        try:
            features = pipeline.get_features()
        except Exception as e:
            log.warning(f"Feature fetch failed: {e}  retrying in 30s")
            time.sleep(30)
            continue

        if features is None:
            log.info("Insufficient data for features. Waiting 60s.")
            time.sleep(60)
            continue

        current_close  = features.get("close")
        current_candle = features.get("candle_ts")
        ts = datetime.now(timezone.utc).isoformat()

        # Close any open 1-bar position once the candle has actually advanced.
        # Guard against stale data: if candle_ts hasn't changed, the price hasn't
        # moved yet and we'd log a $0 close before the bar is complete.
        if (open_position is not None
                and current_close is not None
                and current_candle != open_position["entry_candle_ts"]):
            entry     = open_position["entry_price"]
            direction = open_position["direction"]
            label_raw = math.log(current_close / entry)
            if direction == "LONG":
                pnl_gross = POSITION_USD * (math.exp(label_raw) - 1)
            else:
                pnl_gross = POSITION_USD * (1 - math.exp(label_raw))
            fee_usd        = POSITION_USD * POOL_FEE_RT
            pnl_net        = pnl_gross - fee_usd - GAS_EST_USD
            cumulative_pnl += pnl_net
            tracker.record(pnl_net)
            log.info(
                "CLOSE %s  entry=%.6f  exit=%.6f  pnl_net=$%.4f  cumulative=$%.4f",
                direction, entry, current_close, pnl_net, cumulative_pnl,
            )
            log_trade({
                "timestamp":          ts,
                "event":              "close",
                "direction":          direction,
                "entry_price":        entry,
                "exit_price":         current_close,
                "label_raw":          round(label_raw, 6),
                "pnl_gross_usd":      round(pnl_gross, 4),
                "fee_usd":            round(fee_usd, 4),
                "gas_est_usd":        GAS_EST_USD,
                "pnl_net_usd":        round(pnl_net, 4),
                "cumulative_pnl_usd": round(cumulative_pnl, 4),
            })
            open_position = None

        # Stale data gate — don't open new positions on data older than 5 minutes
        data_age = features.get("data_age_min", 0)
        if data_age > 5:
            log.warning("Data stale (%.1fmin) — skipping signal", data_age)
            now  = time.time()
            wait = 60 - (now % 60) + 1
            time.sleep(wait)
            continue

        # Regime filter
        if features.get("vol_15", 0) < AERO_REGIME_THRESHOLD:
            log.info(
                "Regime filter: low volatility. HOLD.  "
                "vol_15=%.6f  data_age=%.1fmin",
                features.get("vol_15", 0),
                features.get("data_age_min", 0),
            )
            time.sleep(60)
            continue

        # Missing feature guard
        missing = [c for c in FEATURE_COLS_AERO if c not in features]
        if missing:
            log.warning("Missing features %s — skipping signal", missing)
            time.sleep(60)
            continue

        # Model prediction
        feat_vec = [[features[c] for c in FEATURE_COLS_AERO]]
        probs    = model.predict_proba(feat_vec)[0]
        p_long   = float(probs[idx_long])  if idx_long  is not None else 0.0
        p_short  = float(probs[idx_short]) if idx_short is not None else 0.0

        if p_long >= THRESHOLD:
            signal = "LONG"
        elif p_short >= THRESHOLD:
            signal = "SHORT"
        else:
            signal = "HOLD"

        rec = {
            "timestamp":          ts,
            "event":              "signal",
            "signal":             signal,
            "p_long":             round(p_long,  4),
            "p_short":            round(p_short, 4),
            "vol_15":             round(features.get("vol_15", 0), 6),
            "ret_1":              round(features.get("ret_1",  0), 6),
            "close":              current_close,
            "data_age_min":       features.get("data_age_min", None),
            "cumulative_pnl_usd": round(cumulative_pnl, 4),
        }

        if signal != "HOLD":
            open_position = {
                "direction":       signal,
                "entry_price":     current_close,
                "entry_candle_ts": current_candle,
                "entry_ts":        ts,
            }
            rec["position_usd"] = POSITION_USD
            rec["fee_usd"]      = round(POSITION_USD * POOL_FEE_RT, 4)
            rec["gas_est_usd"]  = GAS_EST_USD
            log.info(
                "SIGNAL %s  p_long=%.3f  p_short=%.3f  vol_15=%.4f",
                signal, p_long, p_short, features.get("vol_15", 0),
            )

        log_trade(rec)

        # Sleep until next candle close (~60s, aligned to minute boundary)
        now  = time.time()
        wait = 60 - (now % 60) + 1
        time.sleep(wait)


if __name__ == "__main__":
    Path("logs").mkdir(exist_ok=True)
    run_paper_trader()
