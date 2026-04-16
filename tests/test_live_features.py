"""
Unit tests for execution/live_features.py

Tests feature computation parity with research/features.py on synthetic data.
No network calls — fetch_ohlcv, fetch_gas, fetch_tvl, fetch_weth_usd are patched.

Run: python -m unittest tests.test_live_features
"""

import math
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import polars as pl

from research.features import FEATURE_COLS_AERO


def _synthetic_ohlcv(n: int = 70) -> pl.DataFrame:
    """Build n minutes of synthetic OHLCV with realistic price variation."""
    base_ts = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    prices = [1.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + 0.005 * (hash(len(prices)) % 3 - 1)))

    rows = []
    for i in range(n):
        close = prices[i]
        rows.append([
            int((base_ts + timedelta(minutes=i)).timestamp()),
            close * 0.998,  # open
            close * 1.002,  # high
            close * 0.997,  # low
            close,          # close
            1000.0 + i,     # volume_usd
        ])
    return pl.DataFrame(
        rows,
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


class TestLiveFeaturesParity(unittest.TestCase):
    """Verify that get_features() returns exactly FEATURE_COLS_AERO keys."""

    def _run_get_features(self, ohlcv_df: pl.DataFrame) -> dict | None:
        from execution.live_features import LiveFeaturePipeline

        pipeline = LiveFeaturePipeline.__new__(LiveFeaturePipeline)
        pipeline.pool             = "0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6"
        pipeline._last_candle_ts  = None
        pipeline.client           = MagicMock()
        pipeline.w3               = MagicMock()

        pipeline.fetch_ohlcv   = MagicMock(return_value=ohlcv_df)
        pipeline.fetch_gas     = MagicMock(return_value=0.05)
        pipeline.fetch_weth_usd = MagicMock(return_value=3000.0)
        pipeline.fetch_tvl     = MagicMock(return_value=10.0)

        return pipeline.get_features()

    def test_returns_dict_not_none(self):
        df = _synthetic_ohlcv(70)
        result = self._run_get_features(df)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)

    def test_all_feature_cols_present(self):
        """Every feature in FEATURE_COLS_AERO must appear in get_features output."""
        df = _synthetic_ohlcv(70)
        result = self._run_get_features(df)
        for col in FEATURE_COLS_AERO:
            self.assertIn(col, result, f"Missing feature: {col}")

    def test_close_included(self):
        """close is returned for price logging, even though not in FEATURE_COLS_AERO."""
        result = self._run_get_features(_synthetic_ohlcv(70))
        self.assertIn("close", result)

    def test_data_age_and_candle_ts_included(self):
        """Bookkeeping fields for the paper trader are present."""
        result = self._run_get_features(_synthetic_ohlcv(70))
        self.assertIn("data_age_min", result)
        self.assertIn("candle_ts", result)

    def test_no_nan_in_features(self):
        """All numeric feature values must be finite (no NaN / Inf)."""
        result = self._run_get_features(_synthetic_ohlcv(70))
        for col in FEATURE_COLS_AERO:
            val = result[col]
            self.assertTrue(math.isfinite(val), f"Feature {col} is not finite: {val}")

    def test_insufficient_candles_returns_none(self):
        """Fewer than MIN_CANDLES - 5 candles → return None."""
        df = _synthetic_ohlcv(58)  # MIN_CANDLES=65, threshold=60
        result = self._run_get_features(df)
        self.assertIsNone(result)

    def test_vol_15_is_positive(self):
        """Realized volatility must be positive on non-flat price series."""
        result = self._run_get_features(_synthetic_ohlcv(70))
        self.assertGreater(result["vol_15"], 0)

    def test_feature_count_matches_training(self):
        """Live pipeline must produce exactly the same number of features as training."""
        result = self._run_get_features(_synthetic_ohlcv(70))
        live_feature_keys = {k for k in result if k in set(FEATURE_COLS_AERO)}
        self.assertEqual(len(live_feature_keys), len(FEATURE_COLS_AERO))

    def test_ret_1_sign_follows_price(self):
        """ret_1 of the last row reflects the final price change direction."""
        df = _synthetic_ohlcv(70)
        result = self._run_get_features(df)
        last_close  = float(df["close"][-1])
        prev_close  = float(df["close"][-2])
        expected_ret1 = math.log(last_close / prev_close)
        self.assertAlmostEqual(result["ret_1"], expected_ret1, places=6)


class TestLiveFeaturesTvlFallback(unittest.TestCase):
    """tvl_norm falls back to 1.0 when TVL is unavailable."""

    def _run_no_tvl(self, ohlcv_df: pl.DataFrame) -> dict | None:
        from execution.live_features import LiveFeaturePipeline

        pipeline = LiveFeaturePipeline.__new__(LiveFeaturePipeline)
        pipeline.pool             = "0x7f670f78b17dec44d5ef68a48740b6f8849cc2e6"
        pipeline._last_candle_ts  = None
        pipeline.client           = MagicMock()
        pipeline.w3               = MagicMock()

        pipeline.fetch_ohlcv    = MagicMock(return_value=ohlcv_df)
        pipeline.fetch_gas      = MagicMock(return_value=0.05)
        pipeline.fetch_weth_usd = MagicMock(return_value=None)   # no WETH price
        pipeline.fetch_tvl      = MagicMock(return_value=None)    # no TVL

        return pipeline.get_features()

    def test_tvl_norm_is_1_when_unavailable(self):
        result = self._run_no_tvl(_synthetic_ohlcv(70))
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result["tvl_norm"], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
