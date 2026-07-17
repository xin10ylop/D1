"""Bankroll / capacity simulation for the final report.

Simulates compounding a strategy's trade stream with per-trade sizing capped by
(a) fraction-of-bankroll (fractional Kelly proxy) and (b) depth-based capacity
with the measured slippage curve applied to size.
"""
import numpy as np
import pandas as pd


def slip_curve(notional, a=0.004, b=0.9):
    """Median slippage ($/share) as a function of notional, fit to measured
    points (200:0.4c, 500:0.6c, 1000:0.9c, 2000:1.4c blended BTC/ETH)."""
    return a * (notional / 1000.0) ** b / 100 * 100  # returns in price units (dollars/share)


def simulate(trades, bankroll0=5000, f_bank=0.05, max_notional=2000,
             slip_pts=None, seed=0):
    """trades: DataFrame with columns [ts, entry, ev_share, won(0/1), payoff_share]
    where payoff_share = realized $ per share (label - entry - fee - slip applied later).
    Sequential compounding, one position at a time overlap-agnostic (conservative:
    assumes full capital cycle per trade duration)."""
    rng = np.random.default_rng(seed)
    bank = bankroll0
    path = []
    for r in trades.sort_values("ts").itertuples():
        notional = min(f_bank * bank, max_notional)
        slip = slip_curve(notional) if slip_pts is None else np.interp(
            notional, slip_pts[0], slip_pts[1])
        entry_eff = r.entry + slip
        shares = notional / entry_eff
        pnl = shares * (r.payoff_gross - slip)
        bank += pnl
        path.append(dict(ts=r.ts, bank=bank, notional=notional, pnl=pnl))
        if bank <= bankroll0 * 0.2:
            break
    return pd.DataFrame(path)


def drawdown(path):
    peak = path.bank.cummax()
    return float(((path.bank - peak) / peak).min())
