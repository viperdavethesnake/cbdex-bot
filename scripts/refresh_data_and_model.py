"""
Weekly Data Refresh + Model Retraining

Orchestrates the full ingestion and retraining pipeline:
  1. Gas prices (90d) via eth_getBlockByNumber  — ~110 min
  2. WETH/USDC OHLCV (90d) via GeckoTerminal   — ~10 min
  3. AERO/WETH OHLCV (90d) via eth_getLogs     — ~45 min
  4. Walk-forward evaluation                    — ~5 min
  5. Train final model + save pkl               — ~2 min

Total: ~3 hours. Run weekly via systemd timer (cbdex-refresh.timer).

Usage:
    python scripts/refresh_data_and_model.py [--skip-gas] [--skip-weth] [--eval-only]

Flags:
    --skip-gas    Skip step 1 (gas pull) — use existing gas_prices_90d.parquet
    --skip-weth   Skip step 2 (WETH/USDC pull) — use existing WETH_USDC/final_90d.parquet
    --eval-only   Skip ingestion entirely, just re-evaluate + retrain on current data
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

import polars as pl
from web3 import Web3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/refresh.log"),
    ],
)
log = logging.getLogger(__name__)

GAS_PATH       = Path("data/base_mainnet/network/gas_prices_90d.parquet")
WETH_USDC_PATH = Path("data/base_mainnet/pairs/WETH_USDC/final_90d.parquet")
AERO_PATH      = Path("data/base_mainnet/pairs/AERO_WETH/final_90d.parquet")
MODEL_PATH     = Path("models/aero_weth_rf.pkl")

POOL_WETH_USDC = "0xb2cc224c1c9feE385f8ad6a55b4d94E92359DC59"


def step1_gas(w3: Web3) -> None:
    log.info("Step 1/5 — Gas prices (90d) via eth_getBlockByNumber")
    from ingestion.gas import get_base_fee_series

    chain_head  = w3.eth.block_number
    start_block = chain_head - (90 * 24 * 60 * 30)  # ≈ 3,888,000 blocks back

    log.info("  Chain head: %d  start block: %d", chain_head, start_block)
    t0 = time.time()
    gas_df = get_base_fee_series(start_block, chain_head, sample_every=30)
    GAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    gas_df.write_parquet(GAS_PATH)
    log.info("  Saved → %s  (%d rows, %.1f min)", GAS_PATH, len(gas_df), (time.time()-t0)/60)


def step2_weth_usdc() -> None:
    log.info("Step 2/5 — WETH/USDC OHLCV (90d) via GeckoTerminal")
    from ingestion.fast_path import fetch_ohlcv

    t0 = time.time()
    df = fetch_ohlcv(POOL_WETH_USDC, days=90)
    WETH_USDC_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(WETH_USDC_PATH)
    log.info("  Saved → %s  (%d rows, %.1f min)", WETH_USDC_PATH, len(df), (time.time()-t0)/60)
    log.info("  Range: %s → %s", df["timestamp"].min(), df["timestamp"].max())


def step3_aero_weth() -> None:
    log.info("Step 3/5 — AERO/WETH OHLCV (90d) via eth_getLogs")
    from ingestion.aero_weth_pipeline import main as aero_main

    t0 = time.time()
    aero_main()
    log.info("  Done  (%.1f min)", (time.time()-t0)/60)


def step4_evaluate() -> None:
    log.info("Step 4/5 — Walk-forward evaluation (AERO_WETH)")
    from strategies.model import run_model

    t0 = time.time()
    results = run_model("AERO_WETH", verbose=True)
    beats = sum(1 for r in results if r.get("beats_baseline"))
    log.info("  Folds: %d  beats baseline: %d/%d  (%.1f min)",
             len(results), beats, len(results), (time.time()-t0)/60)
    avg_net = sum(r["pnl_net_usd"] for r in results) / len(results) if results else 0
    log.info("  Avg net PnL/week: $%.2f", avg_net)


def step5_train_and_save() -> None:
    log.info("Step 5/5 — Train final model + save pkl")
    from strategies.model import train_final_model

    t0 = time.time()
    train_final_model("AERO_WETH", save_path=MODEL_PATH, verbose=True)
    log.info("  Done  (%.1f min)", (time.time()-t0)/60)


def main(args: argparse.Namespace) -> None:
    Path("logs").mkdir(exist_ok=True)
    log.info("=" * 60)
    log.info("cbdex data refresh started  %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    total_t0 = time.time()

    if not args.eval_only:
        w3 = Web3(Web3.HTTPProvider(os.environ["BASE_RPC_URL"]))

        if not args.skip_gas:
            step1_gas(w3)
        else:
            log.info("Step 1/5 — Skipped (--skip-gas)")

        if not args.skip_weth:
            step2_weth_usdc()
        else:
            log.info("Step 2/5 — Skipped (--skip-weth)")

        step3_aero_weth()

    step4_evaluate()
    step5_train_and_save()

    elapsed = (time.time() - total_t0) / 60
    log.info("=" * 60)
    log.info("Refresh complete  %.1f min total", elapsed)
    log.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh cbdex training data and retrain model")
    parser.add_argument("--skip-gas",  action="store_true", help="Skip gas data pull")
    parser.add_argument("--skip-weth", action="store_true", help="Skip WETH/USDC pull")
    parser.add_argument("--eval-only", action="store_true", help="Skip ingestion, only eval + retrain")
    main(parser.parse_args())
