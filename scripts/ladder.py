"""Ladder-internal coherence tests: butterflies (H12), density mass (H14),
monotonicity with fees/maker economics (H11 revisit).

All computed per (asset, event_date, grid_ts) from the panel.
"""
import numpy as np
import pandas as pd


def ladder_frames(panel):
    """Yield (asset, event, ts, frame sorted by K) with >=4 strikes."""
    p = panel.dropna(subset=["bid", "ask"])
    for (event, ts), g in p.groupby(["event", "ts"]):
        if g.K.nunique() >= 4:
            yield event, ts, g.sort_values("K")


def butterfly_violations(panel, fee_fn):
    """Buy fly at K1<K2<K3 (equal spacing): +YES(K1) -2YES(K2) +YES(K3) must cost >= 0.
    Executable: cost = ask1 - 2*bid2 + ask3 (+ taker fees on all legs).
    Negative executable cost = riskless profit (payoff of fly >= 0 always)."""
    rows = []
    for event, ts, g in ladder_frames(panel):
        K = g.K.values
        for i in range(len(K) - 2):
            k1, k2, k3 = K[i], K[i + 1], K[i + 2]
            if not np.isclose(k2 - k1, k3 - k2):
                continue
            a1, b2, a3 = g.ask.values[i], g.bid.values[i + 1], g.ask.values[i + 2]
            # short the middle: sell YES(K2) x2 == buy NO(K2) x2 at (1-b2)
            cost = a1 - 2 * b2 + a3
            fees = fee_fn(a1) + 2 * fee_fn(1 - b2) + fee_fn(a3)
            if cost + fees < -0.001:
                sz = min(g.ask_sz.values[i], g.bid_sz.values[i + 1] / 2, g.ask_sz.values[i + 2])
                rows.append(dict(event=event, ts=ts, K2=k2, cost=cost, fees=fees,
                                 profit=-(cost + fees), size=sz))
    return pd.DataFrame(rows)


def density_mass(panel):
    """Implied P(bucket) from adjacent strikes; total mass sanity per ladder."""
    rows = []
    for event, ts, g in ladder_frames(panel):
        mid = ((g.bid + g.ask) / 2).values
        mass = mid[:-1] - mid[1:]          # P(K_i < S <= K_{i+1})
        rows.append(dict(event=event, ts=ts, n_neg=int((mass < -0.005).sum()),
                         worst=float(mass.min()), total_span=float(mid[0] - mid[-1])))
    return pd.DataFrame(rows)
