# Run State & Plan (keep updated — survives context resets)

Updated: 2026-07-17 ~20:30 UTC

## Where things stand
- All venue mechanics audited & verified (docs/market_structure.md). Labels recompute 100% (daily BTC 3043/3043, hourly BTC 852/852).
- Fee model verified from on-chain fills: taker = 0.07*p*(1-p), maker 0; dailies fee era began between 2026-04-12 and 2026-04-29. Model TODAY's fees for all EV claims.
- Deribit data COMPLETE: option tape Sep25'25–Jul17'26 (BTC/ETH/SOL/XRP), futures 1h+perp 1m, DVOL, instruments. Smiles fitted (smiles_{A}.parquet, 15-min grid).
- Binance klines COMPLETE (1m+1h, 4 pairs) — resolution source.
- PM daily-ladder download RUNNING (newest-first by expiry): ~30k/50k quote-days, ~24k/50k trade-days. Restart with `nohup python3 scripts/dl_polymarket.py 48 > logs/dl_pmN.log 2>&1 &` — idempotent. Container restarts kill it; always check `pgrep -f dl_polymarket`.
- Hourly sample ~complete (16.3k mkt-days, BTC+ETH, Jun27–Jul16). Hourly taker: REJECTED (all EV<=0 after fees). 
- Books: 386 signal-days fetched (pilot); fetch more for final OOS signal days via scripts/dl_books.py.

## Pre-registered evaluation design (do not bend)
- Split by EXPIRY date: train < 2026-05-16, test >= 2026-05-16 (test == current fee regime).
- Tune thresholds ONLY on train. Pilot peeks at Jun–Jul threshold EVs happened early (acknowledge in report; thresholds must be re-chosen on train alone).
- Metrics: hedged EV (subtract digital-delta × realized underlying log-return) is primary; raw EV secondary; per-event clustered t-stats; honest next-bar entry; taker fee 0.07·p(1−p); slippage curve from measured books ($500:+0.6c, $1000:+0.9c, $2000:+1.4c median — see results table EXEC row).
- FV known biases (from synthetic-strike audit): good at 24h; at 72h overprices "above" by ~2-5pp in this bear-regime window (risk-neutral vs realized drift). Hedged EV neutralizes; unhedged strategies must clear it.

## Pipeline commands
1. Panels: `cd scripts && python3 build_panel2.py BTC` (ETH/SOL/XRP same; ~1-4 min each).
2. Battery: `python3 run_all.py --battery-only` (thresholds/maker/calendar/weekend, train+test CSVs in results/).
3. Ladder scans: scripts/ladder.py (butterflies, density) — run per panel on train, then test.
4. Finalize winners: depth-adjusted EV via fill_model + books; bankroll sim (scripts/bankroll.py).

## Results so far (results/results_table.csv)
- H9 hourly taker: REJECTED. H11 monotonicity taker: REJECTED (ghost quotes/fees). H18 cross-asset lag: REJECTED (>=15min). H21 tape flow: tiny contrarian tilt, filter only. H4/H28 weekend/hour means: no effect.
- Pilot (Jun-Jul, NOT OOS-valid): FV better calibrated than PM on all 4 assets; PM converges to FV (1h slope -0.79); YES-side gap signal ~+6.6c hedged (BTC pilot) — must re-verify on proper split.
- Early-exit taker round trips: dead (fees ≈ gap). Modes that survive design: taker-entry+hold, maker-entry+hold.

## Remaining priorities
1. Finish daily downloads → rebuild 4 panels full-window → battery train → freeze thresholds → test OOS.
2. Butterflies/density + calendar + maker families on train → test.
3. Books for OOS signal days → depth-adjusted OOS EV at $500/1000/2000 → capacity.
4. Bankroll/compounding path; final report (task 7): ranked strategies incl. everything rejected.
