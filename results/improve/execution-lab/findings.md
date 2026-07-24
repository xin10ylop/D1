# Execution Lab — findings (order placement / exit vs tick-tape fill truth)

Code: `scripts/exec_lab.py` (tape store + fill truth), `exec_study1.py`, `exec_study245.py`,
`exec_study3.py` (train), `exec_test.py` (the single frozen test look). All new files;
live-bot scripts untouched.

**Fill-truth conventions** (YES token tape, `pm_trades/`): resting BID at L fills on the
first `sell` print <= L after order time; resting ASK at A fills on a `buy` print >= A.
Strict inequality (trade-through) required whenever our limit does not improve the book —
queue-join fills are never assumed. Signals on bar t, orders live from ts; taker
counterfactuals at next-bar quotes (incumbent convention). Missing tape days => signal
dropped (coverage 93-99%, reported per study). EVs are hedged (delta x log-ret from the
signal bar = pre-hedge at order placement), event-clustered t.

## Verdicts at a glance

| Policy | Train | Test (one look) | Verdict |
|---|---|---|---|
| **S1 aggressive maker** (bid at ask-1c, unfilled->cross @30m) | **+8.43c** vs taker +7.28c (n=253, t=3.52 vs 2.97) | **+4.34c** vs taker +3.77c (n=168, t=1.18 vs 1.04) | **ADOPT** |
| S2 ladder rung (bid+1c vs mid-1c vs ask-1c, 4h) | bid+1c 0.44c/sig ~ mid-1c 0.55c; ask-1c 0.20c | not tested (kept incumbent) | keep bid+1c |
| S2 requote: cancel@2h (beat rest-4h on train) | 0.63c/sig t=1.84 vs 0.44c t=1.12 | 0.95c t=1.61 vs rest-4h 1.37c t=1.99 — worse | **REJECT** — keep rest-4h |
| S2 requote: chase +1c @30m | 0.24c/sig — worse than resting | not tested | reject |
| Early exit (ask at max(fv,bid)+1c once bid>=fv) | S1: EV/$-day 0.055 vs hold 0.042 (EV/trade 5.1c vs 8.1c) | S1: 0.017 vs 0.020 EV/$-day, EV/trade 1.7c vs 4.0c — worse on both | **REJECT** — hold to settlement |
| Staleness filter (kill late fills) | 2h+ fills dead (-0.1c) | 2h-4h fills BEST (+6.6c, t=2.7) — sign flip | no stable pattern; don't condition |

## 1) Aggressive maker on S1 taker signals (gap>5c, 3<=tte<8) — ADOPT

Rest a YES bid at ask-0.01 (fee-free, near-marketable) instead of crossing; if no tape
fill within 30m, cross at the next bar's ask (taker+fee). Train (n=253 covered signals):

| policy | H | fill rate | ev_ev (hedged) | t |
|---|---|---|---|---|
| taker (incumbent) | - | 100% | +7.28c | 2.97 |
| am, unfilled=0 | 30m | 10.5% | +2.79c | 3.23 |
| am per fill | 30m | - | **+20.0c** | 4.54 |
| **am + taker fallback** | **30m** | 10.5% | **+8.43c** | **3.52** |
| am + taker fallback | 2h | 19.5% | +6.28c | 2.60 |
| am + taker fallback | 4h | 27.0% | +5.37c | 2.20 |

- Adverse selection is BENIGN here: the filled cohort's taker-counterfactual EV is
  +17.3c (they fill because price dips, and the signal is strong enough to survive the
  dip); the unfilled cohort's taker EV is +5.3c — you lose nothing by having rested.
  30m is the right horizon: longer waits decay the edge (fallback EV falls 8.4 -> 5.4c).
- Economics per fill ~ fee saved (~1.6c at these prices) + 1-2c price improvement +
  favorable timing. Per signal: **+1.15c/sh train, +0.57c/sh test** (test EVs are half
  train's across the board this period; incumbent-taker test t~1.0 matches the brief's
  OOS t~1.2). Structurally >= taker minus 30m timing risk, so adopted despite weak test t.

## 2) Limit ladder for S2 maker signals (train-only)

bid+1c and mid-1c are statistically indistinguishable per signal (0.44c vs 0.55c/sig,
fill 55.7% vs 51.6% @4h); ask-1c fills most (60.6%) but per-fill EV collapses to ~0
(pure adverse selection at the top of the book). Keep incumbent bid+1c.

## 4) Requote policy — cancel@2h looked better on train, FAILED the test look

Train: 2h+ fills were dead (staleness table), so cancel@2h dominated per signal (0.63c
vs 0.44c). Test: pattern inverted — 2h-4h fills were the best bucket (+6.6c, t=2.7) and
rest-4h beat cancel@2h (1.37c vs 0.95c per signal). The staleness "signal" is noise
across regimes. **Keep the incumbent rest-4h; do not condition on fill latency.**
(Chase+1c@30m also loses on train: 0.24c/sig.)

## 3) Maker early-exit / capital recycling — REJECTED

Once bid>=fv, rest an ask at max(fv,bid)+1c (requoted per bar; tape `buy`-print truth).
It reliably halves holding time (S1 4.6 -> 2.3d train, 4.7 -> 1.4d test; exit fill rate
77-94%) but gives up too much EV/trade: train S1 8.1 -> 5.1c (EV/$-day up 0.042 -> 0.055),
test S1 4.0 -> 1.7c (EV/$-day DOWN 0.020 -> 0.017). On S2 fills it's worse on test too
(-1.1c/$-day vs +0.6c). The exit sells exactly when the market has repriced to fair —
at +1c over fv you donate the remaining drift/convexity for a small time saving.
Hold to settlement (free redemption) stays optimal unless externally capital-starved.

## 5) Staleness — no exploitable pattern (see 4)

Train <5m: +2.8c, 5-30m: +0.4c, 30m-2h: +2.6c, 2h+: -0.1c; test roughly flat-to-rising
with latency (+3.0/+2.6/+2.9/+6.6c). Fill latency does not proxy adverse selection
consistently in this market.

## Bonus finding (train-only, for hedge-lab): PRE-HEDGE AT ORDER PLACEMENT

For S2 maker fills, hedging when the ORDER is posted vs when it FILLS changes hedged
EV/fill from +0.53c to -0.84c (train, n=3026): fills arrive on down-moves, and a hedge
placed at fill misses exactly that move (mean delta x dS signal->fill = -1.19c). Pre-hedge
cost on unfilled orders (short perp with no option leg, closed at cancel) is real but
smaller: -0.68c/unfilled (t=-5.9); net per-signal accounting still favors pre-hedging
(+0.36c vs -0.39c). The live bot should short the perp when the maker order goes up,
not on fill confirmation.

## Frozen recommendation

1. **S1: replace taker cross with aggressive maker at ask-1c + 30m taker fallback**
   (train +1.15c/sh, test +0.57c/sh over incumbent; never structurally worse).
2. **S2: unchanged** — bid+1c, rest 4h, no chase, no latency filter.
3. **No early exit** — hold to settlement.
4. **Hedge at order placement, not at fill** (train-only evidence; hand to hedge-lab).

Files: s1_aggressive_maker_{summary_,}{train,test}.csv, s2_ladder_requote_train.csv,
s2_frozen_test.csv, s2_staleness_{train,test}.csv, s2_fills_{train,test}.csv,
s3_earlyexit_{s1,s2,summary}_{train,test}.csv.
