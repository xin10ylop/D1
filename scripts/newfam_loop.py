"""Family 5: FREE INVENTORY LOOP — cycle inventory inside one market's life.

Policy per market (state machine on the 15-min panel grid, tape fill truth):
  FLAT: when fv - (bid+1c) > thr (S2 signal), rest YES bid at bid+1c for 4h.
  LONG: hold; when bid >= fv (converged), rest YES ask at max(fv, bid)+1c,
        requoted each bar while the condition holds, live to end of market.
  After a sell fill -> FLAT again; may re-buy on a fresh signal (no 24h cooldown
  inside the loop — the loop IS the study), each buy hedged from its bar.
  Residual inventory at settlement redeems at label.

Outputs: cycles per market, EV per cycle, total EV per market vs buy-and-hold
(single S2 fill, hold to settlement), and capital-time (EV per $-day).

Phase A (cheap, panel-only): count round-trip OPPORTUNITIES per market:
  sequences signal -> converged -> signal again.
Usage: python newfam_loop.py train|test
"""
import sys
import numpy as np
import pandas as pd
from newfam_common import load_all, Tape, clustered, OUT
from newfam_verticals import FastTape

sample = sys.argv[1] if len(sys.argv) > 1 else "train"
THR = 0.025          # S2 incumbent threshold
H_FILL = 4 * 3600
FROZEN = dict(thr=0.025)


def phase_a(m):
    """Count signal->converged->signal oscillations per market from bars."""
    rows = []
    for slug, g in m.groupby("slug"):
        g = g.sort_values("ts")
        sig = (g.fv - (g.bid + 0.01) > THR) & (g.tte_d < 8) & \
              (g.ask > 0.02) & (g.ask < 0.98) & (g.spread <= 0.05)
        conv = g.bid >= g.fv
        state, n_cyc, n_sig = 0, 0, 0
        for s, c in zip(sig.values, conv.values):
            if state == 0 and s:
                state = 1
                n_sig += 1
            elif state == 1 and c:
                state = 2
            elif state == 2 and s:
                n_cyc += 1
                state = 1
        if n_sig:
            rows.append(dict(slug=slug, asset=g.asset.iloc[0],
                             event=g.event.iloc[0], n_sig=n_sig, n_cycles=n_cyc))
    return pd.DataFrame(rows)


def simulate(m, tape, ft):
    """Full loop sim with tape fills. Returns per-market frame."""
    rows = []
    for slug, g in m.groupby("slug"):
        g = g.sort_values("ts").reset_index(drop=True)
        if not len(g):
            continue
        label = g.label.iloc[0]
        state = "flat"          # flat | resting | long | exiting
        buys, sells, hedge_pnl = [], [], 0.0
        order_t0 = order_lim = None
        entry_delta = None
        n_reentries = 0
        end_ts = g.ts.iloc[-1]
        if not tape.coverage(slug, g.ts.iloc[0], end_ts):
            continue
        for r in g.itertuples():
            if state == "resting":
                # did the resting bid fill before this bar?
                if ft.bid_fill(slug, order_t0, min(r.ts, order_t0 + H_FILL), order_lim):
                    buys.append((order_t0, order_lim))
                    entry_delta = order_delta
                    entry_S = order_S
                    state = "long"
                elif r.ts - order_t0 > H_FILL:
                    state = "flat"
            if state == "exiting":
                if ft.ask_fill(slug, exit_t0, r.ts, exit_lim):
                    sells.append((exit_t0, exit_lim))
                    # hedge P&L for the closed round trip: delta x ret over hold
                    hedge_pnl -= entry_delta * np.log(r.S_bin / entry_S)
                    state = "flat"
                    n_reentries += 1
                else:
                    # requote at current bar if condition still holds
                    if r.bid >= r.fv:
                        exit_t0, exit_lim = r.ts, max(r.fv, r.bid) + 0.01
            if state == "long" and r.bid >= r.fv:
                exit_t0, exit_lim = r.ts, max(r.fv, r.bid) + 0.01
                state = "exiting"
            if state == "flat":
                sigok = (r.fv - (r.bid + 0.01) > THR and r.tte_d < 8
                         and 0.02 < r.ask < 0.98 and r.spread <= 0.05)
                if sigok:
                    order_t0, order_lim = r.ts, r.bid + 0.01
                    order_delta, order_S = r.delta, r.S_bin
                    state = "resting"
        # settle residual inventory
        pnl = 0.0
        n_buys, n_sells = len(buys), len(sells)
        for _, px in buys:
            pnl -= px
        for _, px in sells:
            pnl += px
        if n_buys > n_sells:      # residual long redeems at label
            pnl += label * (n_buys - n_sells)
            # hedge open position to settlement
            hedge_pnl -= entry_delta * np.log(g.S_T.iloc[0] / entry_S)
        if n_buys == 0:
            continue
        # counterfactual: first buy held to settlement (incumbent S2 behavior)
        t0, px0 = buys[0]
        g0 = g[g.ts >= t0].iloc[0]
        ev_hold = (label - px0) - g0.delta * np.log(g.S_T.iloc[0] / g0.S_bin)
        rows.append(dict(slug=slug, asset=g.asset.iloc[0], event=g.event.iloc[0],
                         n_buys=n_buys, n_sells=n_sells, label=label,
                         ev_raw=pnl, ev_hedged=pnl + hedge_pnl,
                         ev_hold1=ev_hold,
                         ev_per_buy=pnl / n_buys,
                         evh_per_buy=(pnl + hedge_pnl) / n_buys))
    return pd.DataFrame(rows)


def main():
    allp = load_all()
    m = allp[allp.is_test] if sample == "test" else allp[~allp.is_test]
    pa = phase_a(m)
    print(f"[{sample}] markets with >=1 signal: {len(pa)}; "
          f"mean cycles/mkt {pa.n_cycles.mean():.2f}; "
          f"markets with >=1 re-op: {(pa.n_cycles > 0).mean():.3f}; "
          f"cycle dist:\n{pa.n_cycles.value_counts().sort_index().head(10)}")
    pa.to_csv(OUT / f"loop_phaseA_{sample}.csv", index=False)

    tape = Tape()
    ft = FastTape(tape)
    d = simulate(m, tape, ft)
    d.to_csv(OUT / f"loop_sim_{sample}.csv", index=False)
    multi = d[d.n_buys > 1]
    st = clustered(d, "ev_hedged")
    stb = clustered(d, "evh_per_buy")
    print(f"loop sim: markets traded {len(d)}, total buys {d.n_buys.sum()}, "
          f"sells {d.n_sells.sum()}, multi-buy mkts {len(multi)} "
          f"({len(multi)/max(len(d),1):.2%})")
    print("per-market hedged EV:", {k: round(v, 4) if isinstance(v, float) else v
                                    for k, v in st.items()})
    print("per-buy hedged EV:", {k: round(v, 4) if isinstance(v, float) else v
                                 for k, v in stb.items()})


if __name__ == "__main__":
    main()
