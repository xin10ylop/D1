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
