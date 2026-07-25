# Polymarket Crypto Strike Markets vs Options/Futures — Final Report

**Sample**: 2025-10-11 → 2026-07-16. 12,078 daily-ladder markets (BTC/ETH/SOL/XRP), 4.1M
panel observations at 15-min resolution; full Deribit option tape (~7M trades with exchange
IVs); Binance 1m/1h klines (the exact resolution source); 16.3k hourly-ladder market-days;
559 order-book days; on-chain fills for fee verification.
**Split (pre-registered)**: train = expiries before 2026-05-16; test = 2026-05-16 → 07-16
(9 weeks, entirely inside the current 7% fee regime). Thresholds frozen on train only.

---

## 0. TL;DR

One robust, out-of-sample-confirmed anomaly: **Polymarket systematically underprices
likely-to-win YES shares on daily crypto strike ladders relative to the Deribit
options-implied fair value, and the gap closes toward the options price.** It is a slow
(hours-persistent), one-directional mispricing concentrated in BTC (and ETH), at 1–8 days to
expiry, in the 0.30–0.75 price band. It is harvestable two ways (taker at wide gaps, maker at
moderate gaps), **must be run delta-hedged with a short perp leg** (the raw unhedged version
lost money in the May 2026 crash while the hedged version made +4¢/share), and supports a
$50–100k bankroll before clip limits dilute the edge. Everything else we tested — hourly
ladders, NO-side, verticals, calendar/term structure, cross-asset lead-lag, book-imbalance
alpha, "riskless" ladder arbs — is either dead after fees, an artifact of ghost quotes, or
statistically empty. Details and the full graveyard below.

## 1. Market structure (all verified from primary sources)

- **Products**: daily ladders (`bitcoin-above-62k-on-june-6-2026`, ~11–20 strikes, listed
  ~7.7 days pre-expiry), hourly ladders (20 strikes, $200 apart), legacy 4h multistrike.
- **Resolution**: Binance spot X/USDT candle close — daily: the 1-minute candle **labeled
  12:00 ET** (close ≈ 12:01 ET = 16:01/17:01 UTC); hourly: the 1h candle **ending** at the
  stated hour. Strictly-greater; tie → NO. We recomputed every resolved label from raw
  Binance klines: **3,043/3,043 daily BTC and 852/852 hourly matched the official outcome.**
- **Fees (verified against on-chain `taker_fee` fields)**: maker 0; taker = 0.07·p·(1−p)
  per share (1.75¢ at p=0.5). Introduced Jan 5 2026 on 15-min markets, extended to daily
  ladders between Apr 12–29 2026. All EV below uses today's fees.
- **Deribit**: 08:00 UTC expiries (dailies/weeklies/monthlies), settlement = 30-min index
  TWAP; options 3bp of underlying per side capped at 12.5% of premium; near-dated ATM option
  BBO spreads are 20–28% of premium (option-leg hedging is impractical; perp hedging costs
  ~3.5bp taker and is the right instrument).
- **Depth reality (measured from 25-level books at actual signal times)**: taker slippage
  past the touch — $500: ~0.5–1¢; $1,000: ~0.9–1.1¢; $2,000: ~1.4–1.7¢ (p90 5–6¢). Fill
  rates 94–99% within 25 levels. Maker fills verified against the trade tape: 72% fill rate
  within 4h at bid+1¢ (price improvement ⇒ front of queue), median 196 shares of through-volume
  per signal.

## 2. Pricing engine

PM YES share = cash-or-nothing digital on Binance USDT spot. Fair value from the Deribit
trade tape: per-expiry smiles σ(x) fit every 15 min from trades (recency-weighted, robust,
ridge-regularized, x = ln(K/F)), **total-variance interpolation in time to the exact PM
resolution instant**, strike translated onto the Deribit underlier via the measured
Binance/Deribit-index ratio, forward from perp + dated-futures carry, and the digital priced
as N(d2) − φ(d2)·√τ·(∂σ/∂x) (skew correction).

Validation: on 4,833 synthetic strike-days spanning the full window (no PM data), the model
is well calibrated at 24h; at 72h it overpredicts "above" by 2–5pp in this bear-market sample
— a real-world-drift / risk-neutral wedge. Consequence: **all headline results use
delta-hedged EV** (subtract digital-delta × realized underlying return), which removes this
wedge and all regime luck. Sanity anchor: unconditional hedged EV ≈ 0.00 by construction-free
test; and the FV had a lower Brier score than the PM mid **in every single month** of the
sample, on every asset.

Who moves to whom: regressing 1h-ahead changes, PM mid closes ~79% of a PM-vs-FV gap per
hour while FV moves ~3% — the options market leads, Polymarket follows.

## 3. The confirmed edge

**Signal**: gap = FV − PM executable price, on YES. Entry at the NEXT 15-min bar (no
same-bar execution), taker fee charged, hold to resolution (redemption is free), delta
hedged with short perp (10× margin assumed in capital).

| | S1: Taker-YES | S2: Maker-YES |
|---|---|---|
| Entry | buy YES at ask when gap > 5¢ | rest YES bid at best-bid+1¢ when FV−(bid+1¢) > 2.5¢ |
| TTE band | 3–8 days | 0–8 days |
| Train (Oct–May, event-clustered) | +8.6¢/share hedged, t = 4.0, n = 340 | +1.6¢ (conservative fill model), t = 2.3, n = 3,765 |
| **OOS (May 16–Jul 16)** | **+4.35¢/share hedged** (n = 173, 78 events, t = 1.2; +5.4¢ per-trade mean) | **+4.26¢/share hedged, t = 2.75** (tape-verified fills, n = 823 fills / 1,141 signals) |
| OOS depth-adjusted | +4.2¢ ($500), +3.8¢ ($1k), +3.0¢ ($2k clips) | clip = through-volume: median ~$100, mean ~$1.9k/signal |
| Robustness | EV unchanged entering 30–60 min late; survives 10% fee; positive 6/9 OOS weeks | EV stable train→test (+1.6→+1.7 conservative model); positive 9/10 OOS weeks |
| Asset mix OOS | BTC +6.3¢ (n=133) carries it; ETH +0.9¢; SOL −8¢ (n=7); XRP +15¢ (n=9) | BTC +6.2¢, ETH +2.1¢, SOL −2¢, XRP +0.4¢ |

**Run it hedged.** OOS raw (unhedged) EV was **negative** (−7 to −9¢/share) because the window
included the May crash: the strategy's alpha is real but its naked beta is long-crypto.
Short-perp hedge sized at φ(d2)/(σ√τ) dollars per share; rebalance daily is sufficient at
these horizons; perp costs ~3.5bp/side.

**Why it exists**: favorite-longshot bias (retail overpays tail YES / equivalently overpays
NO on favorites), no cross-margining (a 60¢ YES ties up 60¢ for a week for ≤40¢ upside),
capital lockup through UMA settlement, and post-fee thinner professional participation. The
gap persists for hours (no latency race), and the NO side offers no mirror-image edge —
tested and failed symmetrically.

**Bankroll simulation** (both strategies, actual OOS trade stream, full capital incl. hedge
margin, 80% max deployment, 4-day capital cycles): $5k → median $6.8k over the 9 OOS weeks
(+35%; bootstrap 10th–90th pct: +2% → +114%; P(loss) = 10%; maxDD ≈ −19%). $20k → +60%
(realized path). **Capacity: edge dilutes beyond ~$50–100k bankroll** ($2k taker clips ×
~19 signals/wk + ~$100–400 maker fills × ~90/wk ⇒ ~$50–70k/week deployable).

## 4. The graveyard (everything tested and ruled out)

| # | Idea | Verdict |
|---|---|---|
| H9 | Hourly ladders, taker, FV from IV+realized-vol blend | **Dead.** All configs EV ≤ 0 after the 7% fee; the fee killed the latency game by design. |
| H11 | Ladder monotonicity "arbs" | Real on screen, fake in practice: median 0.1¢ (BTC) gross at 5–27 share ghost quotes in dead strikes; fee-negative. |
| H12 | Digital butterfly arbitrage | **Invalid concept** — caught in self-review: digital-space convexity is not a no-arb constraint (payoff +1/−1). |
| H10/H13 | Calendar/term-structure spreads vs options forward vol | Double taker fees + spread kill it (verticals variant: −2.4¢ both directions; calendar family same structure). |
| H15/16 | Early-exit convergence round trips (taker) | Entry+exit fees ≈ 3.5¢ ≥ typical gap. Hold-to-settlement dominates. |
| H18 | Cross-asset lead-lag (BTC → alts) at ≥15 min | Zero correlation with next-hour laggard repricing. |
| H19/H27 | Distribution-shape pairs (PM bucket mass vs options RND) | Both directions negative after two-leg fees. |
| H20/21 | Book imbalance / tape flow as standalone alpha | Tiny contrarian tilt (−0.1¢/hr per unit imbalance); below fee noise. Possible entry-timing garnish only. |
| H4/H28 | Weekend/hour-of-day mispricing patterns | Mean gaps flat across day/week; nothing tradable at fee scale. |
| H2-NO / maker-NO | NO-side of the same anomaly | Fails train gates in both execution modes; maker-NO is adversely selected (−2.5¢, t=−2.3). |
| — | Pre-fee latency arb (historical) | Existed (pre-Jan/Apr 2026), now structurally removed. Not a strategy, a history lesson. |

## 5. Honest limitations

1. **OOS is 9 weeks** (the entire life of the current fee regime). Taker-family OOS t ≈ 1.2–1.7
   alone; the case rests on train t=4.0 + OOS same-direction/similar-magnitude + tape-verified
   maker t=2.75 + mechanism (PM→FV convergence) + robustness attacks all passing.
2. Early pilot exploration peeked at Jun–Jul data before the split was frozen; thresholds were
   re-derived on train only, but the *family choice* was informed by the pilot. Mitigated, not
   eliminated.
3. SOL/XRP contribute little (SOL OOS negative on n=7) — treat as BTC/ETH strategy.
4. Hedge modeled at 10× perp margin without liquidation dynamics; a violent up-move squeezes
   the short hedge intraday (gap risk between rebalances).
5. UMA resolution risk, Polymarket ToS/geo eligibility, and USDC lockup through settlement
   (~15 min post-expiry) are not modeled.
6. Fee regime could change again (it did once mid-sample); EV scales roughly linearly in feeRate
   (survives to 10%).

## 6. Reproduction

`docs/market_structure.md` (audit) → `scripts/dl_*.py` (data) → `scripts/fv.py` +
`build_panel2.py` (fair values) → `scripts/final_eval.py` (train-tune → OOS) →
`results/results_table.csv` (every test, including failures) → this report.

---

# REVISION 2026-07-24 — Improvement-sprint re-audit (supersedes headline numbers above)

A 7-angle adversarial re-audit (results/improve/) materially revised this report:

## What got WEAKER
1. **S1 taker-YES is demoted to "unproven".** The bar builder took last-bid and last-ask
   independently over one-sided quote updates, creating phantom crossed books (fresh bid,
   stale tiny ask) that pass a `spread<=0.05` filter and concentrate exactly where fv-ask
   gaps are biggest: 67% of S1 train signals and 11/173 OOS trades were phantoms. Excluding
   them: train +6.76c (t=2.47), OOS +2.40c (t=0.66). Additionally the hedge P&L was computed
   as delta x log-return; a real short perp is linear in S, which removes another ~0.8-1.1c:
   **clean S1 OOS ~ +0.65c/sh, statistically zero.** (results/improve/code-audit)
2. **S2 maker hedge-timing risk.** Maker fills occur on ~-84bp down-moves; hedging at fill
   (the live reality) instead of at signal costs ~5c/sh vs the backtest convention under the
   conservative bar-fill model. The tape-verified +4.26c stands as an upper bound (real
   prints, unaffected), the conservative corrected bound is +1.7-1.8c (t~2.5 train), and
   fill-time-hedged conservative is negative. Live S2 expectation: **+1 to +4c/sh, watch the
   paper bands.** (results/improve/hedge-lab)

## What got STRONGER
3. **The mechanism is confirmed on 19 months of untouched pre-Oct-2025 data** (1,034 old
   markets, labels 100% re-verified): high-prob YES underpricing persists (incumbent-style
   signal +3.5..+7.6c/sh on old labels, event-t up to 4.8, positive 6/7 quarters), and
   skip-slot regressions prove the causal direction — PM converges to options FV (24h beta
   0.345), FV never chases PM. The edge only pays hedged — on old data too, raw EV ~0.
   (results/improve/mechanism-extend)
4. **Funding is a credit, not a cost**: the short-perp hedge earned +0.08..+0.11c/sh
   (t up to 7.9), positive 8/10 months. Static full-delta beats rebalancing and dated-future
   hedging. (results/improve/hedge-lab)
5. **FV calibration improved**: hour-of-week seasonal vol-time halves the expiry-day
   overpricing of 95-98c shares (OOS-confirmed); mark-IV smiles, RV blends, and fat tails
   all rejected. (results/improve/fv-upgrade)
6. **Execution improved**: S1 entries now rest at ask-1c for 30min before crossing
   (structurally >= taker); maker-NO, previously "dead" via a sign bug, is actually
   borderline-positive (+1.19c, t=1.9) — watch-listed, not adopted. All entry-rule
   conditioning ideas (normalized gaps, filters) failed honestly and were rejected.

## Revised bottom line
- **Primary strategy: S2 maker-YES** (rest bid+1c at gap>2.5c, delta-hedged at fill, hold to
  settlement): conservative +1.2-1.8c/sh, tape-verified upper bound +4.3c, plus ~+0.1c funding.
- **S1 taker**: keep as aggressive-maker paper book only; its OOS evidence was mostly phantom
  quotes. Do not size it until the paper track record proves it.
- All live-bot bugs found (phantom pending resolution, midpoint-as-tape fills, stuck-position
  deadlock, DST guard, fill-window caps) are fixed and deployed.
- Expected returns at small size are correspondingly lower than the pre-audit projection:
  plan around the maker book's conservative band (+1-2c/sh on ~50c entries, ~90-130 fills/wk
  across BTC/ETH) until the paper record says otherwise.

## Canonical corrected scoreboard (final_eval re-run on audited code, 2026-07-25)
Crossed-quote exclusion + corrected filters; train-tune/OOS-freeze discipline identical:

| family | frozen train config | train ev_ev (t) | OOS ev_ev (t) | verdict |
|---|---|---|---|---|
| taker-YES | 5c gap, tte 1-3d | +7.26c (2.93), n=192 | **-1.12c (-0.29)**, n=113 | FAILED OOS — demoted |
| maker-YES | 2.5c gap at bid+1c, tte 0-8d | +1.73c (2.41), n=3,403 | **+2.67c (1.85)**, n=796, 208 events | SURVIVES (primary) |
| taker-NO | — | best t=0.54, fails gate | not evaluated | dead |
| maker-NO | — | best t=1.73, fails gate | not evaluated | watchlist |

(Conservative bar-fill model; tape-verified fills and the funding credit sit on top of the
maker number as upside. results/final_strategies.csv is the artifact.)

## Verification addendum (2026-07-25, results/improve/verify-remaining)
- The old-era (2024-25) mechanism validation was PARTIALLY REFUTED at magnitude: its +7.6c/sh
  headline was ~80% a hedge-timing accounting credit (signals select on pre-entry spot drops).
  Corrected old-era edge: **+1-2c/sh, t<2** — consistent with, not stronger than, the incumbent
  maker result. Labels, universe, no-lookahead, and the pooled PM->FV convergence direction all
  independently confirmed; the causal asymmetry is a 2025-era phenomenon (2024 is symmetric).
- The funding credit (+0.05-0.11c/sh to the short-perp hedge) was CONFIRMED exactly against an
  independent API re-fetch; its t-stats corrected for overlapping windows (honest t 1.2-3.2).

## New-families addendum (2026-07-25, results/improve/new-families)
Six additional families tested under fully corrected evaluation (phantom-clean quotes, linear
hedge, tape fills, one frozen OOS look each): maker-legged verticals (legging risk, reject on
train), settlement-zone YES (reject on train) and its NO mirror (train +1.85c t=2.2 but the
one OOS tail loss erased it), spike-fade (PM under-reacts, not overshoots; bookable version
negative OOS), event YES-baskets (no within-event diversification exists to harvest), and the
sign-corrected maker-NO (train t=4.1 at 1-3d tte but +0.6c t=0.3 OOS with fill-time negative).
**All rejected. S2 maker-YES remains the only strategy with out-of-sample support.**
Watch-list for when the OOS window doubles: maker-NO 1-3d, settlement-NO.
