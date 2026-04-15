"""
AERO/WETH Classic vAMM Pipeline — Phases 3–7 (TRD v1.5)

No Classic vAMM subgraph is available on The Graph. Uses eth_getLogs via the
public Base RPC (mainnet.base.org, 2000-block range limit) to pull Swap and
Sync events directly from the pool contract.

Event signatures (confirmed by keccak256):
  Swap: Swap(address,address,uint256,uint256,uint256,uint256) — both addrs indexed
  Sync: Sync(uint256,uint256) — no indexed params

Token ordering (pool 0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6):
  token0 = AERO (18 dec), token1 = WETH (18 dec)
  price (WETH per AERO) = amount1In / amount0Out  (buying AERO)
                         = amount1Out / amount0In  (selling AERO)
  volume_usd = weth_amount × weth_close_price  (from WETH/USDC final_90d.parquet)

TVL = 2 × reserve1 / 1e18 × weth_price  (vAMM symmetry: x*y=k, both sides equal value)
    sourced from Sync events, block timestamps approximated via gas_prices_90d.parquet

Usage:
    python ingestion/aero_weth_pipeline.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import polars as pl
from dotenv import load_dotenv

from ingestion.audit import calculate_window_metrics, evaluate_window

load_dotenv(dotenv_path=".env")

# ── Constants ──────────────────────────────────────────────────────────────────
POOL_ADDRESS = "0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6"
PUBLIC_RPC   = "https://mainnet.base.org"
SWAP_TOPIC   = "0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b"
SYNC_TOPIC   = "0xcf2aa50876cdfbb541206f89af0ee78d44a2abf8d328e37fa4917f982149848a"
CHUNK_SIZE   = 2000   # mainnet.base.org max block range per eth_getLogs call

CANDIDATE_PATH = "data/base_mainnet/pairs/AERO_WETH/candidate_90d.parquet"
WETH_USDC_PATH = "data/base_mainnet/pairs/WETH_USDC/final_90d.parquet"
GAS_PATH       = "data/base_mainnet/network/gas_prices_90d.parquet"
AUDIT_LOG_PATH = "data/base_mainnet/pairs/AERO_WETH/audit_log.json"
FINAL_PATH     = "data/base_mainnet/pairs/AERO_WETH/final_90d.parquet"


# ── RPC helpers ────────────────────────────────────────────────────────────────

def rpc_call(method: str, params: list):
    resp = httpx.post(
        PUBLIC_RPC,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        timeout=30,
    )
    r = resp.json()
    if "error" in r:
        raise RuntimeError(f"RPC error [{method}]: {r['error']}")
    return r["result"]


def fetch_logs(from_block: int, to_block: int, topic: str, label: str = "") -> list[dict]:
    """
    Fetch all event logs matching `topic` for [from_block, to_block],
    chunked at CHUNK_SIZE. Prints progress every 200 chunks.
    """
    all_logs = []
    chunk_start = from_block
    chunks_done = 0
    total_chunks = max(1, (to_block - from_block + CHUNK_SIZE) // CHUNK_SIZE)
    tag = f"[{label}] " if label else ""

    while chunk_start <= to_block:
        chunk_end = min(chunk_start + CHUNK_SIZE - 1, to_block)
        logs = rpc_call("eth_getLogs", [{
            "address":   POOL_ADDRESS,
            "fromBlock": hex(chunk_start),
            "toBlock":   hex(chunk_end),
            "topics":    [topic],
        }])
        all_logs.extend(logs)
        chunks_done += 1
        if chunks_done % 200 == 0:
            pct = chunks_done / total_chunks * 100
            print(f"  {tag}{chunks_done}/{total_chunks} chunks ({pct:.0f}%), "
                  f"{len(all_logs):,} logs so far", flush=True)
        chunk_start = chunk_end + 1

    return all_logs


def fetch_block_timestamps(block_numbers: list[int]) -> dict[int, int]:
    """
    Fetch actual Unix timestamps for a list of block numbers.
    50 ms sleep → 20 req/s, within Alchemy free tier.
    Returns {block_number: unix_timestamp}.
    """
    unique = sorted(set(block_numbers))
    result = {}
    for i, bn in enumerate(unique):
        block = rpc_call("eth_getBlockByNumber", [hex(bn), False])
        result[bn] = int(block["timestamp"], 16)
        time.sleep(0.05)
        if (i + 1) % 100 == 0:
            print(f"  Block timestamps: {i+1}/{len(unique)}", flush=True)
    return result


def ts_to_block(ts: int, gas_df: pl.DataFrame) -> int:
    """
    Map a Unix timestamp to a block number using gas data as a lookup table.
    For timestamps before the gas data range, extrapolates at ~0.5 blk/s (Base L2).
    """
    # gas timestamps as unix seconds (stored as datetime[us,UTC] → cast to Int64 µs → ÷ 1e6)
    gas = gas_df.sort("block_number").with_columns(
        (pl.col("timestamp").cast(pl.Int64) // 1_000_000).alias("unix_s")
    )
    before = gas.filter(pl.col("unix_s") <= ts)
    if not before.is_empty():
        return int(before["block_number"][-1])

    # Target is before gas data range — extrapolate backward at Base block rate
    first_block = int(gas["block_number"][0])
    first_unix  = int(gas["unix_s"][0])
    return max(0, first_block + int((ts - first_unix) * 0.5))  # ~0.5 blk/s


# ── Swap decoding & aggregation ────────────────────────────────────────────────

def decode_swap_logs(logs: list[dict]) -> list[dict]:
    """
    Decode Aerodrome Classic vAMM Swap event data.

    Swap data layout (4 × uint256 = 128 bytes = 256 hex chars):
      [0:64]    amount0In  — AERO in  (18 dec)
      [64:128]  amount1In  — WETH in  (18 dec)
      [128:192] amount0Out — AERO out (18 dec)
      [192:256] amount1Out — WETH out (18 dec)

    Price in WETH per AERO (both tokens 18 dec — no decimal scaling needed).
    """
    records = []
    for log in logs:
        hex_data = log["data"][2:]          # strip "0x"
        if len(hex_data) != 256:            # sanity check: 4 × 32 bytes
            continue

        a0in  = int(hex_data[0:64],   16)   # AERO in
        a1in  = int(hex_data[64:128], 16)   # WETH in
        a0out = int(hex_data[128:192], 16)  # AERO out
        a1out = int(hex_data[192:256], 16)  # WETH out

        if a0out > 0 and a1in > 0:          # WETH → AERO (buying AERO)
            price    = a1in  / a0out
            weth_raw = float(a1in)
        elif a0in > 0 and a1out > 0:        # AERO → WETH (selling AERO)
            price    = a1out / a0in
            weth_raw = float(a1out)
        else:
            continue                        # zero-amount event — skip

        records.append({
            "block_number": int(log["blockNumber"], 16),
            "price":        price,
            "weth_raw":     weth_raw,       # raw WETH amount in wei (18 dec)
        })
    return records


def aggregate_classic_swaps(
    records: list[dict],
    block_ts: dict[int, int],
    weth_usdc: pl.DataFrame,
) -> pl.DataFrame:
    """
    Aggregate decoded Swap records into 1-minute OHLCV candles.
    volume_usd = weth_amount × weth_close_price  (joined from WETH/USDC final_90d).
    """
    empty = pl.DataFrame(schema={
        "timestamp":  pl.Datetime("us", "UTC"),
        "open":       pl.Float64,
        "high":       pl.Float64,
        "low":        pl.Float64,
        "close":      pl.Float64,
        "volume_usd": pl.Float64,
    })
    if not records:
        return empty

    # Map block_number → unix timestamp
    ts_df = pl.DataFrame({
        "block_number": list(block_ts.keys()),
        "unix_ts":      [int(v) for v in block_ts.values()],
    })

    swaps_df = (
        pl.DataFrame(records)
        .join(ts_df, on="block_number", how="left")
        .with_columns([
            pl.from_epoch("unix_ts", time_unit="s")
              .dt.replace_time_zone("UTC")
              .alias("timestamp"),
            (pl.col("weth_raw") / 1e18).alias("weth_amount"),
        ])
        .sort("timestamp")
    )

    # Join_asof with WETH/USDC close price for volume_usd computation
    weth_price_df = (
        weth_usdc
        .select(["timestamp", "close"])
        .rename({"close": "weth_price"})
        .sort("timestamp")
    )
    swaps_df = (
        swaps_df
        .join_asof(weth_price_df, on="timestamp", strategy="backward")
        .with_columns(
            (pl.col("weth_amount") * pl.col("weth_price")).alias("volume_usd")
        )
    )

    return (
        swaps_df
        .with_columns(pl.col("timestamp").dt.truncate("1m").alias("bucket"))
        .group_by("bucket")
        .agg([
            pl.col("price").first().alias("open"),
            pl.col("price").max().alias("high"),
            pl.col("price").min().alias("low"),
            pl.col("price").last().alias("close"),
            pl.col("volume_usd").sum().alias("volume_usd"),
        ])
        .sort("bucket")
        .rename({"bucket": "timestamp"})
    )


# ── Phase 3: Stratified sampling ───────────────────────────────────────────────

def phase3(candidate: pl.DataFrame) -> dict:
    """
    Compute daily close-price variance. Select:
      Spike = max variance day
      Flat  = min variance day
      Mean  = day closest to median variance
    """
    daily_var = (
        candidate
        .with_columns(pl.col("timestamp").dt.date().alias("date"))
        .group_by("date")
        .agg(pl.col("close").var().alias("variance"))
        .sort("date")
        .drop_nulls()
    )

    rows = daily_var.to_dicts()
    variances = [r["variance"] for r in rows]
    dates     = [r["date"]     for r in rows]

    spike_idx = max(range(len(variances)), key=lambda i: variances[i])
    flat_idx  = min(range(len(variances)), key=lambda i: variances[i])
    med       = sorted(variances)[len(variances) // 2]
    mean_idx  = min(range(len(variances)), key=lambda i: abs(variances[i] - med))

    windows = {
        "Spike": {"date": dates[spike_idx], "variance": variances[spike_idx]},
        "Flat":  {"date": dates[flat_idx],  "variance": variances[flat_idx]},
        "Mean":  {"date": dates[mean_idx],  "variance": variances[mean_idx]},
    }

    print("\n── Phase 3: Stratified Sampling ──")
    for name, w in windows.items():
        print(f"  {name:5s}: {w['date']}  variance={w['variance']:.6e}")

    return windows


# ── Phase 4: Swap fetch per window ────────────────────────────────────────────

def phase4_window(
    label: str,
    date,
    gas_df: pl.DataFrame,
    weth_usdc: pl.DataFrame,
    candidate: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """
    Fetch and aggregate on-chain Swap events for one window day.
    Returns (fast_candles, truth_candles).
    """
    start_ts = int(datetime(date.year, date.month, date.day, tzinfo=timezone.utc).timestamp())
    end_ts   = start_ts + 86400 - 1  # 23:59:59 UTC

    block_start = ts_to_block(start_ts, gas_df)
    block_end   = ts_to_block(end_ts,   gas_df)

    print(f"\n── Phase 4 [{label}]: {date}  blocks {block_start:,}–{block_end:,} ──")

    swap_logs = fetch_logs(block_start, block_end, SWAP_TOPIC, label=label)
    print(f"  Raw swap logs:  {len(swap_logs):,}")

    records = decode_swap_logs(swap_logs)
    print(f"  Decoded swaps:  {len(records):,}")

    if not records:
        print(f"  [{label}] No swaps decoded — cannot build truth candles.")
        return pl.DataFrame(), pl.DataFrame()

    # Fetch actual block timestamps — gas_df is ±15s, crossing minute boundary is an error
    unique_blocks = list({r["block_number"] for r in records})
    print(f"  Fetching timestamps for {len(unique_blocks)} unique blocks...", flush=True)
    block_ts = fetch_block_timestamps(unique_blocks)

    truth = aggregate_classic_swaps(records, block_ts, weth_usdc)
    print(f"  Truth candles:  {len(truth):,}")

    if len(truth) > 0:
        p_min  = truth["close"].min()
        p_max  = truth["close"].max()
        n_null = truth["close"].null_count()
        print(f"  Price range:    {p_min:.6f} – {p_max:.6f} WETH/AERO  null_close={n_null}")

    # Extract fast-path candles for this window day
    day_start_dt = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)
    day_end_dt   = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=timezone.utc)
    fast = candidate.filter(
        (pl.col("timestamp") >= pl.lit(day_start_dt)) &
        (pl.col("timestamp") <= pl.lit(day_end_dt))
    )
    print(f"  Fast candles:   {len(fast):,}")

    return fast, truth


# ── Phase 5: Audit gate ────────────────────────────────────────────────────────

def build_audit_report(window_results: list[dict]) -> dict:
    """
    Build and save the audit report for AERO/WETH with correct source metadata.
    Overrides truth_path_source and tvl_source vs. the CL audit.py defaults.
    """
    overall = "PASS" if all(w.get("pass", False) for w in window_results) else "FAIL"
    report = {
        "pair":               "AERO_WETH",
        "audit_timestamp":    datetime.now(timezone.utc).isoformat(),
        "fast_path_source":   "GeckoTerminal",
        "tvl_source":         "on_chain_sync_hourly_forward_filled",
        "truth_path_source":  "eth_getLogs / mainnet.base.org",
        "trd_version":        "v1.5",
        "windows":            window_results,
        "overall_verdict":    overall,
    }
    os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
    with open(AUDIT_LOG_PATH, "w") as f:
        json.dump(report, f, indent=2)
    return report


# ── Phase 7: TVL + final_90d.parquet ─────────────────────────────────────────

def phase7(candidate: pl.DataFrame, gas_df: pl.DataFrame, weth_usdc: pl.DataFrame):
    """
    Pull full 90-day Sync events, compute hourly TVL, forward-fill to 1-minute,
    merge into candidate, write final_90d.parquet.

    Block timestamps for Sync events are approximated from gas_prices_90d.parquet
    (every 30 blocks, ±15s resolution) — sufficient for hourly TVL grouping.
    """
    block_start = int(gas_df["block_number"].min())
    block_end   = int(gas_df["block_number"].max())

    print(f"\n── Phase 7: Sync event pull  blocks {block_start:,}–{block_end:,} ──")
    print(f"  Estimated chunks: {(block_end - block_start) // CHUNK_SIZE:,}", flush=True)

    sync_logs = fetch_logs(block_start, block_end, SYNC_TOPIC, label="TVL")
    print(f"  Sync events total: {len(sync_logs):,}")

    # Decode Sync events (data = 2 × uint256 = 64 bytes = 128 hex chars)
    records = []
    for log in sync_logs:
        hex_data = log["data"][2:]
        if len(hex_data) != 128:
            continue
        reserve0 = int(hex_data[0:64],  16)   # AERO reserves (not used)
        reserve1 = int(hex_data[64:128], 16)   # WETH reserves
        records.append({
            "block_number": int(log["blockNumber"], 16),
            "reserve1_raw": float(reserve1),
        })

    if not records:
        print("  No Sync events decoded — writing final with null TVL.")
        candidate.write_parquet(FINAL_PATH)
        return

    sync_df = pl.DataFrame(records)

    # Map block numbers to approximate timestamps via gas_df (join_asof on block_number)
    gas_blocks = gas_df.sort("block_number").select(["block_number", "timestamp"])
    sync_with_ts = sync_df.sort("block_number").join_asof(
        gas_blocks, on="block_number", strategy="backward"
    )

    # Hourly WETH close price from WETH/USDC
    weth_hourly = (
        weth_usdc
        .with_columns(pl.col("timestamp").dt.truncate("1h").alias("hour"))
        .group_by("hour")
        .agg(pl.col("close").last().alias("weth_price"))
        .rename({"hour": "timestamp"})
        .sort("timestamp")
    )

    # Last Sync per hour → TVL = 2 × reserve1_weth × weth_price
    tvl_hourly = (
        sync_with_ts
        .with_columns(pl.col("timestamp").dt.truncate("1h").alias("hour"))
        .group_by("hour")
        .agg(pl.col("reserve1_raw").last())
        .rename({"hour": "timestamp"})
        .sort("timestamp")
        .join(weth_hourly, on="timestamp", how="left")
        .with_columns(
            (2.0 * pl.col("reserve1_raw") / 1e18 * pl.col("weth_price")).alias("tvl_usd")
        )
        .select(["timestamp", "tvl_usd"])
    )

    tvl_min = tvl_hourly["tvl_usd"].min()
    tvl_max = tvl_hourly["tvl_usd"].max()
    print(f"  Hourly TVL points: {len(tvl_hourly):,}")
    if tvl_min is not None:
        print(f"  TVL range: ${tvl_min:,.0f} – ${tvl_max:,.0f}")

    # Forward-fill to 1-minute by joining candidate timestamps against hourly TVL
    final_df = (
        candidate
        .drop("tvl_usd")            # candidate tvl_usd is all null (GeckoTerminal has no TVL)
        .sort("timestamp")
        .join_asof(
            tvl_hourly.sort("timestamp"),
            on="timestamp",
            strategy="backward",    # carry last known TVL forward
        )
    )

    final_df.write_parquet(FINAL_PATH)

    rows     = len(final_df)
    null_tvl = final_df["tvl_usd"].null_count()
    print(f"\n  Saved → {FINAL_PATH}")
    print(f"  Rows:    {rows:,}")
    print(f"  tvl_usd null count: {null_tvl}  "
          f"({'leading rows before first Sync' if null_tvl > 0 else 'clean'})")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Load data files
    candidate = pl.read_parquet(CANDIDATE_PATH)
    weth_usdc = pl.read_parquet(WETH_USDC_PATH)
    gas_df    = pl.read_parquet(GAS_PATH)

    print(f"Candidate: {len(candidate):,} rows  "
          f"{candidate['timestamp'].min()} → {candidate['timestamp'].max()}")
    print(f"WETH/USDC: {len(weth_usdc):,} rows  "
          f"{weth_usdc['timestamp'].min()} → {weth_usdc['timestamp'].max()}")
    print(f"Gas data:  {len(gas_df):,} rows  "
          f"blocks {gas_df['block_number'].min():,} – {gas_df['block_number'].max():,}")

    # ── Phase 3 ──────────────────────────────────────────────────────────────
    windows = phase3(candidate)

    # ── Phase 4 & 5 per window ───────────────────────────────────────────────
    window_results = []
    for label, w in windows.items():
        fast, truth = phase4_window(label, w["date"], gas_df, weth_usdc, candidate)

        if fast.is_empty() or truth.is_empty():
            result = {
                "label": label,
                "pass":  False,
                "error": "no_swap_data",
                "mae_pct": None,
                "volume_error_pct": None,
                "tvl_error_pct": None,
                "gap_count_dropped": None,
                "gap_count_filled": None,
            }
            window_results.append(result)
            print(f"\n── Phase 5 [{label}]: FAIL (no swap data) ──")
            continue

        metrics = calculate_window_metrics(fast, truth)
        passed  = evaluate_window(metrics)
        verdict = "PASS" if passed else "FAIL"

        result = {"label": label, "pass": passed, **metrics}
        window_results.append(result)

        print(f"\n── Phase 5 [{label}]: {verdict} ──")
        print(f"  MAE:         {metrics['mae_pct']:.4f}%   (< 0.10%)")
        print(f"  Volume err:  {metrics['volume_error_pct']:.4f}%   (< 1.00%)")
        print(f"  Dropped:     {metrics['gap_count_dropped']}         (== 0)")
        print(f"  Filled:      {metrics['gap_count_filled']}")

    # ── Phase 5: Save audit report ────────────────────────────────────────────
    print("\n── Phase 5: Audit Report ──")
    report = build_audit_report(window_results)
    overall = report["overall_verdict"]
    print(f"  Overall verdict: {overall}")
    print(f"  Saved → {AUDIT_LOG_PATH}")

    if overall != "PASS":
        print("\nAudit FAIL — see audit_log.json for per-window details.")
        sys.exit(1)

    # ── Phase 7: TVL + final_90d.parquet ─────────────────────────────────────
    phase7(candidate, gas_df, weth_usdc)

    print("\nDone.")


if __name__ == "__main__":
    main()
