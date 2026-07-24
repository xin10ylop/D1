# Improvement Sprint Brief (shared context for all research agents)

## The incumbent strategy (what you are trying to beat)
Polymarket daily crypto strike ladders ("Will BTC/ETH be above $K on <date>?", resolve on the
Binance 1m candle labeled 12:00 ET) systematically underprice likely-to-win YES shares vs the
Deribit options-implied fair value (FV). Frozen incumbent config:
- S1 taker-YES: buy YES at next-bar ask when fv − ask > 0.05, 3 ≤ tte_d < 8. OOS hedged EV +4.35c/sh (event-weighted, n=173, t≈1.2).
- S2 maker-YES: rest bid at best_bid+0.01 when fv − (bid+0.01) > 0.025, tte < 8d. OOS tape-verified +4.26c/sh (t=2.75, fill rate 72%).
- Delta-hedged with short perp: delta_$ = phi(d2)/(sigma*sqrt(tau)) per share. UNHEDGED IS NEGATIVE OOS.
- NO-side, verticals(taker legs), calendar, hourly product, cross-asset lag: all tested dead (see results/results_table.csv).
- Fees: taker 0.07*p*(1-p) per share, maker 0. Redemption at settlement free.

## Non-negotiable evaluation discipline
- Split by EXPIRY date: train < 2026-05-16, test (OOS) >= 2026-05-16. TUNE ON TRAIN ONLY.
  You get ONE look at test, at the very end, with your single frozen best config. Report both.
- Execution honesty: signal on bar t ⇒ entry price from bar t+1 (columns n_bid/n_ask in panels).
- Metrics: hedged EV per share (subtract delta × realized log-return S_bin→S_T) is PRIMARY.
  Event-clustered t-stats (cluster by (asset, expiry date)). Also report raw EV, hit rate, n, n_events.
- Dedup: max one signal per market per 24h unless you explicitly study portfolios.
- Fees at today's schedule always. Slippage for taker at size: +0.6c/$500, +0.9c/$1k, +1.4c/$2k.

## Data on disk (all under /home/user/D1/data/)
- panel_{BTC,ETH,SOL,XRP}.parquet — the master 15-min panels. Columns: slug, K, T_pm (unix, resolution
  instant), ts (unix, 15-min grid), tte_d, bid, bid_sz, ask, ask_sz, fv, sigma, x (log-moneyness),
  S_idx (Deribit index), S_bin (Binance spot), extrap (1 = FV extrapolated below shortest Deribit expiry,
  i.e. expiry-day 08:00-16:00 UTC zone), result_id, label (1=YES won).
  Load via scripts/battery.py:prep(asset) which adds: n_bid/n_ask/n_ts (next bar), exp_dt, is_test,
  event, gap_yes, gap_no, delta (digital $-delta), ret_T (realized log-return to settlement), S_T.
- pm_bars/ — per (slug, day) 1-min BBO bars (ts, bid, bid_sz, ask, ask_sz, mid_min, mid_max, n).
- pm_trades/ — per (slug, day) tick trade prints: timestamp_us, price, size, side (side = aggressor
  direction on the YES token: 'buy' = taker bought YES, 'sell' = taker sold YES / bought NO).
- pm_books/ — 541 (slug, day) 25-level book snapshot files (event-driven, cols bid_price_0..24 etc.).
- deribit_opt_trades/ — {BTC,ETH,USDC}_{date}.parquet full option tape: timestamp(ms), instrument_name,
  price (coin or USDC), iv (trade IV, %), mark_price, index_price, direction, amount, contracts.
- smiles_{asset}.parquet — 15-min smile fits: exp_ts, grid_ts, S, a, b, c (sigma(x)=a+bx+cx², x vs index),
  n, nk, resid, xlo, xhi (clamp evaluation outside [xlo,xhi]).
- binance/{SYM}USDT_{1m,1h}.parquet — resolution source klines (open_time_us, open..close, volume, n_trades).
- deribit_fut/ — bars1h_* per future, bars1m_* perps, dvol_{BTC,ETH}.parquet, instruments_*.
- index1m_{asset}.parquet — per-minute Deribit index from the tape.
- results/trades/{taker,maker}_yes_oos.csv — incumbent OOS trade lists.
- Telonex API for anything missing: scripts/common.py has TLX_KEY; SDK `telonex` installed;
  channels: quotes/book_snapshot_25/trades/onchain_fills per (slug, outcome, day). Deribit history
  host serves option trades back to 2016 (see scripts/dl_deribit_trades.py pattern).

## Code you can reuse
scripts/fv.py (smile fit + digital pricing), scripts/battery.py (prep, clustered_stats, eval_side),
scripts/evaluate.py (fees, dedup, hedged EV), scripts/build_panel2.py (panel construction),
scripts/paperbot.py (the live bot — audit target).

## Known model weaknesses (attack these)
- FV extrap zone (expiry day after 08:00 UTC) uses flat-vol time scaling — no intraday seasonality.
- Smile is quadratic-in-x with ridge; wings clamped flat outside fitted strike range.
- FV at 72h horizon overpredicts "above" ~2-5pp in the bear-regime sample (risk-neutral drift wedge)
  — hedging neutralizes it, but a drift/seasonality-aware FV variant might improve entry selection.
- Static hedge at entry (no rebalance); perp funding P&L ignored entirely.
- Flat clip sizing; no gap-magnitude or Kelly sizing; cooldown blunt at 24h.
- Maker limit always bid+1c; no aggressive-maker (ask−1c) variant; no maker early-exit.

## Output conventions
Write everything to results/improve/<yourname>/ : a findings.md (concise, numbers-first) plus any
CSVs. Print the single most important table to stdout too. If you modify shared code, put variants
in NEW files (do not break scripts/ used by the live bot).
