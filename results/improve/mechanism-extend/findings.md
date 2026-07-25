# mechanism-extend findings (recovered from workflow journal)

## Headline
The core mispricing existed throughout the untouched 2024-03 to 2025-10 era: PM underpriced high-prob YES vs options FV (labels verified 100%), the incumbent-style hedged signal earned +3.5 to +7.6c/sh on old labels (event-clustered t up to 4.8, positive in 6/7 quarters), and traded prices converged toward FV at 1-24h horizons while FV never chased price.

## Old-era universe + label verification (1,034 markets, 2024-03 to 2025-10) — ADOPT
- train: 1034/1034 labels match Binance 1m recomputation (was 93.2% before fixing noon-ET resolution instant and $118K strike parsing); 8,253 market-days of onchain fills downloaded with 0 errors; extension smiles fit for 2024-03-10..2025-09-24 without touching shared files
- test: not tested
- Infrastructure + verified dataset for any future pre-2025-10 study; the noon-ET/end_date_us metadata trap and k-suffix strike bug are load-bearing for anyone reusing polymarket_markets.parquet on old markets.

## (a) Calibration: FV vs traded fill prices (Brier) — PROMISING
- train: Old era: Brier FV .1508 vs PM .1506 (diff -.0002, clustered t=0.2) - parity overall; FV better in 2025Q3 (+.0014, t=1.7), worse in 2025Q1 (-.0102, t=-1.7, risk-neutral drift wedge)
- test: not tested
- FV is not globally better-calibrated in the old era; the edge lives in high-prob/wide-gap regions. The 72h drift wedge (known weakness) is confirmed out-of-era, reinforcing that hedging and gap-thresholding are what make the incumbent work.

## (a2+b) Incumbent signal validity on old labels — ADOPT
- train: High-prob buckets: 2024 fv .90-.97 bucket vwap .929 < fv .938 < label .982 (t=5.5 for fv>vwap in .97-1 bucket). Signal fv-vwap>5c, tte 0-8d: hedged EV +7.6c/sh (n=431, 173 events, t=4.8), +7.5c with conservative taker-buy-vwap entry; S1-analog (5c, 3-8d) +6.8c t=2.3; 2.5c threshold +3.5c t=2.0; positive 6/7 quarters; raw unhedged EV ~0-1c
- test: not tested (no look at the frozen 2026-05-16+ OOS set; this whole study is on pre-2025-10-11 expiries disjoint from it)
- The mispricing the incumbent trades is not a 2025Q4 artifact - it persists across 19 months of untouched history and only pays after delta-hedging, exactly matching the incumbent design. Mechanism validation, not executable P&L.

## (c) Convergence direction: PM -> FV, not FV -> PM — ADOPT
- train: Skip-slot regressions (shared-noise-free, event-clustered): price-to-FV beta .048 (t=4.5) at 1h, .148 (t=3.0) at 6h, .345 at 24h; FV-to-price ~0 at all horizons and within 2024, 2025H1, 2025H2 separately; |gap|>2.5c shrinks 0.8c/24h (t=6.5)
- test: not tested
- Directly validates the causal story: options FV leads, Polymarket follows. Naive same-slot regressions invert this due to shared measurement noise - the skip-slot design in analyze.py:convergence is the correct test.

## Caveats
Mechanism validation only, NOT fill-realistic P&L: no order book pre-2025-10, entries proxied by next-slot onchain fill VWAP (understates taker cost by ~half a spread); carry=0 in extension FV (<0.1% spot effect at <8d); sparse era (2024Q2-2025Q2) has only ~15-35 trades/quarter per config; 2025Q3 ladder era dominates counts; Brier parity means the edge is concentrated in high-prob/wide-gap regions, not a global FV superiority; no look at the frozen >=2026-05-16 OOS test set was taken.