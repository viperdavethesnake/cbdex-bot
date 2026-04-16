"""
Unit tests for backtest/simulator.py

Tests the core PnL math and cost model without any external dependencies.
Run: python -m unittest tests.test_simulator
"""

import math
import unittest
from datetime import datetime, timezone

import polars as pl

from backtest.simulator import Simulator, POOL_FEE, GAS_COST_USD


PAIR = "AERO_WETH"
POS  = 50.0
CAP  = 1000.0


def _make_df(preds: list[int], label_raws: list[float], labels: list[int]) -> pl.DataFrame:
    n  = len(preds)
    ts = [datetime(2026, 1, 1, i, 0, tzinfo=timezone.utc) for i in range(n)]
    return pl.DataFrame({
        "timestamp":       ts,
        "close":           [1.0] * n,
        "label":           labels,
        "label_raw":       label_raws,
        "pred":            preds,
        "pred_prob_long":  [0.8 if p == 1  else 0.0 for p in preds],
        "pred_prob_short": [0.8 if p == -1 else 0.0 for p in preds],
        "tvl_usd":         [None] * n,
    })


class TestSimulatorPnLMath(unittest.TestCase):

    def test_long_correct_gross_pnl(self):
        label_raw = 0.01
        df = _make_df([1], [label_raw], [1])
        sim = Simulator(PAIR, position_usd=POS, latency_bps=0)
        result = sim.run(df)

        self.assertEqual(result.n_trades, 1)
        self.assertEqual(result.n_correct, 1)
        expected = POS * (math.exp(label_raw) - 1)
        self.assertAlmostEqual(result.pnl_gross_usd, expected, places=9)

    def test_short_correct_gross_pnl(self):
        label_raw = -0.01
        df = _make_df([-1], [label_raw], [-1])
        sim = Simulator(PAIR, position_usd=POS, latency_bps=0)
        result = sim.run(df)

        self.assertEqual(result.n_trades, 1)
        self.assertEqual(result.n_correct, 1)
        expected = POS * (1 - math.exp(label_raw))
        self.assertAlmostEqual(result.pnl_gross_usd, expected, places=9)

    def test_long_wrong_direction(self):
        df = _make_df([1], [-0.01], [-1])
        sim = Simulator(PAIR, position_usd=POS, latency_bps=0)
        result = sim.run(df)

        self.assertEqual(result.n_correct, 0)
        self.assertLess(result.pnl_gross_usd, 0)

    def test_hold_generates_no_trade(self):
        df = _make_df([0, 0, 0], [0.01, -0.01, 0.005], [1, -1, 0])
        result = Simulator(PAIR, position_usd=POS).run(df)

        self.assertEqual(result.n_trades, 0)
        self.assertEqual(result.pnl_net_usd, 0.0)

    def test_fee_charged_round_trip(self):
        df = _make_df([1], [0.0], [0])
        sim = Simulator(PAIR, position_usd=POS, latency_bps=0)
        result = sim.run(df)

        expected_fee = POS * POOL_FEE[PAIR] * 2
        self.assertAlmostEqual(result.trades[0].fee_usd, expected_fee, places=9)

    def test_gas_cost_applied(self):
        df = _make_df([1], [0.0], [0])
        result = Simulator(PAIR, position_usd=POS, latency_bps=0).run(df)

        self.assertAlmostEqual(result.trades[0].gas_usd, GAS_COST_USD, places=9)

    def test_latency_cost(self):
        bps = 10.0
        df = _make_df([1], [0.0], [0])
        result = Simulator(PAIR, position_usd=POS, latency_bps=bps).run(df)

        expected = POS * (bps / 10000) * 2
        self.assertAlmostEqual(result.trades[0].latency_usd, expected, places=9)

    def test_zero_latency_bps(self):
        df = _make_df([1], [0.01], [1])
        result = Simulator(PAIR, position_usd=POS, latency_bps=0).run(df)
        self.assertEqual(result.trades[0].latency_usd, 0.0)

    def test_pnl_net_equals_gross_minus_all_costs(self):
        df = _make_df([1], [0.02], [1])
        result = Simulator(PAIR, position_usd=POS, latency_bps=10).run(df)

        t = result.trades[0]
        expected_net = t.pnl_gross_usd - t.fee_usd - t.gas_usd - t.slippage_usd - t.latency_usd
        self.assertAlmostEqual(t.pnl_net_usd, expected_net, places=9)

    def test_precision_calculation(self):
        df = _make_df([1, 1, 1], [0.01, 0.01, -0.01], [1, 1, -1])
        result = Simulator(PAIR, position_usd=POS, latency_bps=0).run(df)

        self.assertEqual(result.n_trades, 3)
        self.assertEqual(result.n_correct, 2)
        self.assertAlmostEqual(result.precision, 2 / 3, places=9)

    def test_multiple_trades_pnl_sum(self):
        """Net PnL across trades sums correctly (with latency=0 for predictability)."""
        raws = [0.01, -0.01]
        df = _make_df([1, -1], raws, [1, -1])
        result = Simulator(PAIR, position_usd=POS, latency_bps=0).run(df)

        self.assertEqual(result.n_trades, 2)
        self.assertEqual(result.n_correct, 2)
        expected_gross = (POS * (math.exp(raws[0]) - 1) +
                          POS * (1 - math.exp(raws[1])))
        self.assertAlmostEqual(result.pnl_gross_usd, expected_gross, places=9)

    def test_summary_contains_latency_key(self):
        df = _make_df([1], [0.01], [1])
        result = Simulator(PAIR, position_usd=POS).run(df)
        self.assertIn("total_latency_usd", result.summary())

    def test_summary_required_keys(self):
        df = _make_df([1], [0.01], [1])
        s = Simulator(PAIR, position_usd=POS).run(df).summary()
        for key in ("pair", "n_candles", "n_trades", "precision",
                    "pnl_gross_usd", "total_fee_usd", "total_gas_usd",
                    "total_slippage_usd", "total_latency_usd",
                    "pnl_net_usd", "pnl_net_pct", "roi_annualised_pct"):
            self.assertIn(key, s, f"Missing key: {key}")


if __name__ == "__main__":
    unittest.main()
