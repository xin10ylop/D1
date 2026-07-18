# Polymarket Crypto Strike Markets vs Options/Futures — Final Report

*(Skeleton — numbers filled after full-sample OOS run. See docs/market_structure.md for the
audit, docs/hypotheses.md for the full register, results/results_table.csv for every test.)*

## 0. TL;DR

## 1. Market structure (audited from primary sources)
- Products, resolution mechanics, verified label reconstruction (100% agreement daily + hourly).
- Fee regime history (verified from on-chain fills) and its consequences.
- Execution reality: spreads, depth, slippage curve, maker queue mechanics.

## 2. Pricing relationship
- PM strike share = Binance-settled digital; FV from Deribit smile via total-variance
  interpolation to the exact PM expiry instant; skew correction; forward carry; underlier basis.
- FV calibration audit (synthetic strikes, no PM data): Brier by horizon; known drift wedge at 72h.

## 3. What was tested and ruled out (the graveyard)
| Hypothesis | Verdict | Evidence |
|---|---|---|
(hourly taker, monotonicity arb, digital butterflies [invalid], cross-asset lag, early-exit
round trips, weekend/hour means, tape flow standalone, ...)

## 4. Surviving strategies (ranked)
For each: signal, entry/exit, assets/strikes/expiries, train stats, OOS stats (hedged + raw EV,
hit, per-event t), depth-adjusted EV at $500/$1k/$2k, capacity, drawdown, compounded path.

### S1 ...
### S2 ...

## 5. Capacity & portfolio view
- Per-strategy capacity, overlap/correlation between strategies, combined bankroll simulation.

## 6. Honest limitations
- Pilot-peek acknowledgment; fee-era length (11 weeks OOS); regime dependence; UMA tail risks;
  borrow/interest on locked capital; PM ToS/geo constraints.
