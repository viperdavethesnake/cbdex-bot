"""
Phase 2 — Gas-Aware Backtest Simulator

Event-driven simulator for evaluating ML model signals against historical data.
All trades use a fixed position size (sub-$100) with real fee and gas costs.

Cost model (per trade, round-trip):
    WETH/USDC: pool fee 0.10% + gas ~$0.02
    AERO/WETH: pool fee 0.60% + gas ~$0.02

Slippage: price impact from $50 trade on $15-30M TVL pool is ~0.0003% —
negligible at this trade size, included as a floor.

Usage:
    from backtest.simulator import Simulator
    sim = Simulator("WETH_USDC", position_usd=50.0)
    results = sim.run(predictions_df)
"""

from dataclasses import dataclass, field
from typing import Literal

import polars as pl

# Pool fees (one-way), round-trip = 2x
POOL_FEE = {
    "WETH_USDC": 0.0005,   # 0.05% per swap
    "AERO_WETH": 0.0030,   # 0.30% per swap
}

# Estimated gas cost per trade on Base in USD (sub-cent, but real)
GAS_COST_USD = 0.02

# Minimum price impact floor (% of trade size / TVL, floored at 0.0001%)
MIN_PRICE_IMPACT = 0.000001


@dataclass
class Trade:
    timestamp: object
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    exit_price: float
    position_usd: float
    label: int            # actual outcome: 1=LONG, -1=SHORT, 0=HOLD
    pred_prob: float      # model confidence
    fee_usd: float        # round-trip pool fee
    gas_usd: float        # gas cost
    slippage_usd: float   # price impact
    pnl_gross_usd: float  # before costs
    pnl_net_usd: float    # after all costs
    correct: bool


@dataclass
class SimulationResult:
    pair: str
    n_candles: int
    n_trades: int
    n_correct: int
    capital_usd: float
    position_usd: float
    trades: list[Trade] = field(default_factory=list)

    @property
    def trade_rate_pct(self) -> float:
        return self.n_trades / self.n_candles * 100 if self.n_candles > 0 else 0.0

    @property
    def precision(self) -> float:
        return self.n_correct / self.n_trades if self.n_trades > 0 else 0.0

    @property
    def total_fee_usd(self) -> float:
        return sum(t.fee_usd for t in self.trades)

    @property
    def total_gas_usd(self) -> float:
        return sum(t.gas_usd for t in self.trades)

    @property
    def total_slippage_usd(self) -> float:
        return sum(t.slippage_usd for t in self.trades)

    @property
    def pnl_gross_usd(self) -> float:
        return sum(t.pnl_gross_usd for t in self.trades)

    @property
    def pnl_net_usd(self) -> float:
        return sum(t.pnl_net_usd for t in self.trades)

    @property
    def pnl_net_pct(self) -> float:
        return self.pnl_net_usd / self.capital_usd * 100 if self.capital_usd > 0 else 0.0

    @property
    def roi_annualised_pct(self) -> float:
        """Annualised ROI assuming results cover ~90 days."""
        return self.pnl_net_pct * (365 / 90)

    def summary(self) -> dict:
        return {
            "pair":             self.pair,
            "n_candles":        self.n_candles,
            "n_trades":         self.n_trades,
            "trade_rate_pct":   round(self.trade_rate_pct, 3),
            "precision":        round(self.precision, 4),
            "pnl_gross_usd":    round(self.pnl_gross_usd, 4),
            "total_fee_usd":    round(self.total_fee_usd, 4),
            "total_gas_usd":    round(self.total_gas_usd, 4),
            "total_slippage_usd": round(self.total_slippage_usd, 4),
            "pnl_net_usd":      round(self.pnl_net_usd, 4),
            "pnl_net_pct":      round(self.pnl_net_pct, 4),
            "roi_annualised_pct": round(self.roi_annualised_pct, 2),
            "capital_usd":      self.capital_usd,
            "position_usd":     self.position_usd,
        }


class Simulator:
    """
    Backtest simulator. Expects a predictions DataFrame with columns:
        timestamp, close, label, label_raw, pred, pred_prob_long, pred_prob_short
        (pred: 1=LONG, 0=HOLD, -1=SHORT)

    The next candle's close is used as the exit price (label_raw already captures this).
    """

    def __init__(
        self,
        pair: str,
        position_usd: float = 50.0,
        capital_usd: float = 1000.0,
    ):
        self.pair = pair
        self.position_usd = position_usd
        self.capital_usd = capital_usd
        self.pool_fee = POOL_FEE[pair]

    def _price_impact_usd(self, tvl_usd: float | None) -> float:
        """Estimate one-way price impact for position_usd trade given TVL."""
        if tvl_usd is None or tvl_usd <= 0:
            return self.position_usd * MIN_PRICE_IMPACT
        # For a vAMM x*y=k pool: price_impact ≈ trade_size / (2 * TVL)
        # For a CL pool at current tick: impact is lower, use same formula as floor
        impact_pct = self.position_usd / (2 * tvl_usd)
        return self.position_usd * max(impact_pct, MIN_PRICE_IMPACT)

    def run(self, df: pl.DataFrame) -> SimulationResult:
        """
        Run simulation over a predictions DataFrame.
        Assumes df is sorted by timestamp and contains label_raw (next candle return).
        """
        result = SimulationResult(
            pair=self.pair,
            n_candles=len(df),
            n_trades=0,
            n_correct=0,
            capital_usd=self.capital_usd,
            position_usd=self.position_usd,
        )

        rows = df.to_dicts()
        for row in rows:
            pred = row.get("pred", 0)
            if pred == 0:
                continue

            direction = "LONG" if pred == 1 else "SHORT"
            label = row["label"]
            label_raw = row["label_raw"]   # actual next-candle log return
            entry_price = row["close"]
            tvl_usd = row.get("tvl_usd")

            # Exit price implied by actual next-candle return
            import math
            exit_price = entry_price * math.exp(label_raw)

            # Gross PnL: directional return on position
            if direction == "LONG":
                pnl_gross = self.position_usd * (math.exp(label_raw) - 1)
            else:  # SHORT
                pnl_gross = self.position_usd * (1 - math.exp(label_raw))

            # Costs
            fee_usd      = self.position_usd * self.pool_fee * 2   # round-trip
            gas_usd      = GAS_COST_USD
            slippage_usd = self._price_impact_usd(tvl_usd) * 2     # entry + exit
            total_cost   = fee_usd + gas_usd + slippage_usd

            pnl_net = pnl_gross - total_cost
            correct = (pred == 1 and label == 1) or (pred == -1 and label == -1)

            trade = Trade(
                timestamp=row["timestamp"],
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                position_usd=self.position_usd,
                label=label,
                pred_prob=row.get("pred_prob_long" if pred == 1 else "pred_prob_short", 0.0),
                fee_usd=fee_usd,
                gas_usd=gas_usd,
                slippage_usd=slippage_usd,
                pnl_gross_usd=pnl_gross,
                pnl_net_usd=pnl_net,
                correct=correct,
            )
            result.trades.append(trade)
            result.n_trades += 1
            if correct:
                result.n_correct += 1

        return result


def print_summary(result: SimulationResult, label: str = "") -> None:
    s = result.summary()
    tag = f" [{label}]" if label else ""
    print(f"  {s['pair']}{tag}")
    print(f"    Candles: {s['n_candles']:,}  Trades: {s['n_trades']:,}  Rate: {s['trade_rate_pct']:.2f}%")
    print(f"    Precision: {s['precision']:.3f}")
    print(f"    PnL gross: ${s['pnl_gross_usd']:+.2f}  "
          f"fees: -${s['total_fee_usd']:.2f}  "
          f"gas: -${s['total_gas_usd']:.2f}  "
          f"slippage: -${s['total_slippage_usd']:.2f}")
    print(f"    PnL net: ${s['pnl_net_usd']:+.2f}  ({s['pnl_net_pct']:+.2f}% of ${s['capital_usd']:.0f} capital)")
    print(f"    Annualised ROI: {s['roi_annualised_pct']:+.1f}%")
