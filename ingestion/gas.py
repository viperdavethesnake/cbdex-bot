"""
Phase 6 — Gas Data Pull (TRD v1.5)

Samples baseFeePerGas every 30 blocks (~1-minute resolution) over the past
90 days using eth_getBlockByNumber via Alchemy RPC. Stores results as Parquet.

Usage (module):
    from ingestion.gas import get_base_fee_series

Usage (script):
    python ingestion/gas.py
"""

import os
import sys
import time

import polars as pl
from dotenv import load_dotenv
from web3 import Web3

load_dotenv(dotenv_path=".env")

w3 = Web3(Web3.HTTPProvider(os.environ["BASE_RPC_URL"]))


def get_base_fee_series(start_block: int, end_block: int, sample_every: int = 30) -> pl.DataFrame:
    """
    Fetch baseFeePerGas for every `sample_every`-th block in [start_block, end_block).
    Returns a DataFrame with columns: block_number, timestamp (UTC), base_fee_gwei.
    Sleep 50ms between calls — 20 req/sec, well within Alchemy free tier.
    """
    records = []
    total = len(range(start_block, end_block, sample_every))
    report_every = 5_000  # print progress ~every 4 minutes

    for i, block_num in enumerate(range(start_block, end_block, sample_every)):
        block = w3.eth.get_block(block_num)
        records.append({
            "block_number": block_num,
            "timestamp":    block["timestamp"],
            "base_fee_gwei": block["baseFeePerGas"] / 1e9,
        })
        time.sleep(0.05)  # 20 req/sec — well within Alchemy free tier

        if (i + 1) % report_every == 0 or (i + 1) == total:
            pct = (i + 1) / total * 100
            print(
                f"  block {block_num:,} — {i + 1:,}/{total:,} samples ({pct:.1f}%)",
                flush=True,
            )

    return pl.DataFrame(records).with_columns(
        pl.from_epoch("timestamp", time_unit="s").dt.replace_time_zone("UTC")
    )


if __name__ == "__main__":
    OUT_PATH = "data/base_mainnet/network/gas_prices_90d.parquet"

    chain_head = w3.eth.block_number
    start_block = chain_head - (90 * 24 * 60 * 30)  # ≈ 3,888,000 blocks back

    print(f"Chain head:   {chain_head:,}")
    print(f"Start block:  {start_block:,}")
    print(f"Block range:  {chain_head - start_block:,} blocks")
    print(f"Total samples: ~{(chain_head - start_block) // 30:,}")
    print(f"Est. runtime:  ~{(chain_head - start_block) // 30 * 0.05 / 60:.0f} minutes")
    print("Starting pull...", flush=True)

    t0 = time.time()
    gas_df = get_base_fee_series(start_block, chain_head, sample_every=30)

    gas_df.write_parquet(OUT_PATH)
    elapsed = time.time() - t0
    print(f"\nSaved → {OUT_PATH}  ({elapsed/60:.1f} min elapsed)")

    # --- Validation ---
    gas_df = pl.read_parquet(OUT_PATH)
    assert gas_df.null_count()["base_fee_gwei"][0] == 0, "FAIL: null base_fee_gwei values found"

    rows       = len(gas_df)
    blk_min    = gas_df["block_number"].min()
    blk_max    = gas_df["block_number"].max()
    fee_min    = gas_df["base_fee_gwei"].min()
    fee_max    = gas_df["base_fee_gwei"].max()

    print(f"\n--- Validation PASS ---")
    print(f"Row count:    {rows:,}")
    print(f"Block range:  {blk_min:,} → {blk_max:,}")
    print(f"Base fee:     {fee_min:.4f} Gwei (min) — {fee_max:.4f} Gwei (max)")
