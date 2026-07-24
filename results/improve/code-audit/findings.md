# Adversarial code audit — findings

Auditor: code-audit. Files: fv.py, build_panel2.py, battery.py, evaluate.py, final_eval.py, paperbot.py, report.py.
Fixes (not applied to live scripts): `results/improve/code-audit/fixes.patch` (applies clean from repo root: `git apply --check` OK).

## CRITICAL

### C1. Crossed/phantom quotes contaminate the panel; the `spread <= 0.05` filter admits negative spreads — incumbent S1 edge is roughly halved and loses OOS significance
- **Where**: `scripts/dl_polymarket.py:59-63` (per-minute `last` of bid and ask taken *independently* over one-sided quote updates), `scripts/build_panel2.py:43` (`to_grid` ffills each side unboundedly), `scripts/battery.py:73` and `scripts/final_eval.py:82` (`spread <= 0.05` has no `spread >= 0` lower bound).
- **Mechanism**: the raw Telonex quote stream contains one-sided updates (ask NaN for long stretches). Minute bars then carry a fresh bid with a stale, tiny ask. Demonstration: `solana-above-30-on-june-7-2026` 2026-06-03 15:23 bar = bid 0.972x100 / ask **0.700x6** (raw file), fv=1.000 -> the backtest "buys" a near-certain winner at 0.70 for +28.5c/sh. That single phantom is one of the OOS trades.
- **Contamination**: 0.36-1.23% of panel rows are crossed (bid>ask), but they concentrate in the signal set because a phantom low ask *is* the fv-ask gap: **227/340 (67%) of S1 train signals** and 10/173 OOS signals are crossed at the signal bar (8 more at the entry bar).
- **Impact (re-run, frozen incumbent config thr=0.05, tte 3-8d, same dedup)**:

| S1 taker-YES | as published | crossed excluded (signal+entry) |
|---|---|---|
| TRAIN ev_ev / t | +8.56c, t=4.03, n=340 | **+6.76c, t=2.47, n=118** |
| OOS ev_ev / t | +4.35c, t=1.24, n=173 | **+2.40c, t=0.66, n=166** |

  OOS decomposition: the 11 crossed trades average **+19.6c/sh hedged** vs +1.84c for the 162 clean ones (event-weighted). More than half the claimed OOS edge is phantom quotes.
- **S2 maker-YES is robust**: same re-run with crossed books excluded and crossing limits disallowed: train +1.62 -> +1.80c (t 2.33 -> 2.51), OOS +1.67 -> +1.69c (bar-fill rule). The maker strategy survives; the taker strategy's OOS evidence does not.
- **Fix** (in patch): require `spread >= 0` in every signal filter; null next-bar entry quotes when the next bar is crossed (`prep`). Structural fix (recommended, not in patch to avoid rebuilding data): in `downsample`, ffill raw bid/ask *before* the minute resample so each bar is a consistent snapshot, drop crossed rows, and add a quote-age column in `to_grid` (cap ffill at ~1h).
- Note: for the surviving clean trade set, quote staleness is NOT an issue (median ask age at entry 0 min, p90 1 min) — the staleness pathology lives almost entirely inside the crossed rows.

### C2. paperbot maker/AM "tape" fill check uses the MIDPOINT series, not trades — phantom fills
- **Where**: `scripts/paperbot.py:334-345` (`traded_low_since`), used by `check_maker_fills` (line 363) and by the retroactive pre-expiry check in `resolve_positions` (line 306).
- **Evidence (live)**: `clob.polymarket.com/prices-history?...&fidelity=1` returned p=0.9985 while the book was 0.998/0.999 and last prints were 0.998/0.999 -> **p is (bid+ask)/2**, not a traded price. The docstring ("lowest traded price") is wrong.
- **Failure**: a resting YES bid at `bid+1c` is credited a fill whenever the *midpoint* dips to <= limit — e.g. best bid drops with zero trades (exactly the one-sided-book scenario of C1). Since maker fills are fee-free and priced at the limit, this systematically fabricates favorable fills -> paper P&L and the S2/AM live validation are inflated -> wrong go-live decision.
- **Fix** (in patch): query the real print tape `data-api.polymarket.com/trades?market=<conditionId>` and require an aggressive **SELL print on the YES token at/below the limit** inside the order's life window; `conditionId` is now captured from gamma and stored on positions; if the tape is unavailable, do not assume a fill.

## MAJOR

### M1. battery.py maker-NO selection sign is inverted — the "maker-NO dead" battery rows tested the wrong side
- **Where**: `scripts/battery.py:104`: `sel = m[(1 - m.ask + 0.01) - (1 - m.fv) > thr]`. That is `lim_no - NO_fair > thr`: it selects NO purchases *overpriced* by at least thr — guaranteed-negative-EV by construction. `final_eval.py:47` has the correct sign (`lim_no < (1-fv) - thr`).
- **Demonstration (train, thr=0.02, 8-bar horizon)**: as written: n=4965 fills, ev_ev **-2.26c, t=-3.76**; corrected sign: n=4523 fills, ev_ev **+1.19c, t=+1.92**.
- **Consequence**: `results/battery_maker_train_prelim.csv` maker-NO rows are meaningless. The frozen conclusion happens to survive because `final_eval` (correct sign) still failed its t>=2 gate (t=1.54 at thr=0.025), but corrected maker-NO at thr=0.02 is borderline (t=1.9) — worth a proper look, not "dead".
- **Fix** (in patch): corrected condition.

### M2. Hedge P&L uses delta x log-return; a real short perp is linear in S — overstates hedged EV by ~0.8-1.1c/sh
- **Where**: `scripts/paperbot.py:322` (`hedge_pnl = -delta*log(S_T/S_entry)*shares`), same convention in `battery.py:46/51` and `evaluate.py:82`.
- **Algebra**: short perp with $notional `delta*shares` earns `-delta*(S_T/S0 - 1)*shares`. The log form overstates P&L by `delta*(e^r - 1 - r) >= 0` for every realization (Jensen), i.e. it silently pockets the digital's gamma cost.
- **Impact (clean S1 trade sets)**: mean overstatement **+0.77c/sh (train), +1.06c/sh (OOS)**. Clean S1 OOS ev_ev drops from +1.84c (log) to **+0.65c** with the true linear hedge — combined with C1, the incumbent taker edge is statistically indistinguishable from zero OOS.
- **Fix** (in patch, paperbot only — must not change frozen research metrics silently): linear hedge P&L in `resolve_positions`. For research, either adopt linear hedging in the primary metric or rename it "log-alpha" — it is not a tradable P&L. (Perp funding is additionally ignored; known limitation.)

### M3. Stuck-position deadlock: unresolvable settlement candle locks capital forever, silently
- **Where**: `scripts/paperbot.py:316-319`. `binance_close_at` demands a candle whose open == `T_pm-60` exactly; on any persistent mismatch (bad endDate, API change, delisted symbol) it returns None and the position is re-queued forever. `deployed_capital` keeps counting it -> `avail` shrinks -> the bot silently stops entering, with no alert.
- **Fix** (in patch): loud `ERROR STUCK POSITION` log once a position is >6h past T_pm unresolved.

### M4. DST trust: T_pm is taken from gamma `endDate` with no 12:00-ET sanity check (Nov 1 2026 transition risk)
- **Where**: `scripts/paperbot.py:254` (and panel `build_panel2.py:76` via Telonex `end_date_us`).
- **Verified**: the historical universe is clean — all 12,277 markets have end_dt exactly 12:00 America/New_York (16:00Z summer / 17:00Z winter, correct through the Mar 8 2026 spring transition), and the backtest window (test = May 16-Jul 16) is entirely EDT, so **no in-sample DST bug exists**. The live bot, however, blindly trusts gamma endDate; a market listed around Nov 1 2026 with a stale 16:00Z endDate would resolve per rules at 17:00Z — the bot would fetch the wrong candle (1h early), misprice tau near expiry, and mis-settle P&L, all silently.
- **Fix** (in patch): skip (and log) any market whose endDate is not exactly 12:00 ET (`zoneinfo` check). This also guards M3's failure mode.

### M5. check_maker_fills tape window is not capped at the order's life
- **Where**: `scripts/paperbot.py:363`: fill check window is `[last_fill_check, now]` with no upper bound. After a bot outage longer than the order life (maker 4h / AM 30m), prints that occurred *after* the order would have been cancelled are credited as fills. (`resolve_positions:306` caps correctly with `min(placed+life, T_pm)`; this path doesn't.)
- **Fix** (in patch): `end_ts = min(now, placed_ts + life)`.

## MINOR

- **m1. Maker fill rule counts an immediate cross as a fee-free maker fill.** `battery.py:113-117`, `final_eval.py:57-59`: when `bid+0.01 >= ask` (<=1c spread or crossed), posting the "maker" bid actually lifts the ask as a taker (fee 0.07*p*(1-p) <= 1.75c). Re-run with the guard shows the S2 result is insensitive (+1.7c OOS unchanged), but the rule is wrong; guard included in patch. paperbot's `maker_pending` has the same shape (limit can equal the ask when spread=1c -> next cycle "filled" at ask, fee-free).
- **m2. NO-side hit-rate reporting**: `battery.py:65` `clustered_stats` reports `label.mean()` as "hit" — for NO-side trades the win rate is `1-label`. Reporting only; EVs unaffected.
- **m3. Hedge timing mismatch**: delta and `S_bin`/`S_entry` are taken at the *signal* bar while entry/fill happens 15min (taker) to 4h (maker) later — the "hedge" leg books the signal->fill drift. Consistent between backtest and paperbot, so no relative bias, but live hedges executed at fill will not match the backtest metric definition.
- **m4. Unbounded ffill / 60-min grid extension** (`build_panel2.py:39,43`): quotes ffill across arbitrary intra-market gaps and 60min past the last bar with no age column. Currently immaterial for clean signals (median age 0min) because staleness concentrates in crossed rows (C1), but add a `quote_age` column and cap ffill when rebuilding.
- **m5. Depth realism**: 46% of clean OOS S1 entries had <100 shares at the last real ask (median 100sh ~ $30-50 notional). The +0.6c/$500 slippage schedule is optimistic at the stated $100 clip for half the fills. Capacity, not correctness.
- **m6. Block trades are included in the smile fit** (`fv.load_trades` loads `block_trade_id` but never filters); block prints can be far off-market. Low priority given robust reweighting.
- **m7. State hygiene** (`paperbot.py`): `last_entry` dict and journal grow unboundedly; `maker_expired` (never-filled) markets still consume the 24h cooldown. Cosmetic at current scale.

## Explicitly checked and found CORRECT
- **Smile no-lookahead**: `fit_smiles` uses trades strictly before each grid point (`searchsorted(..., side='left')`); panel `ts` and smile `grid_ts` are both exact 900s multiples, so `ts//900*900 == ts` — the smile used at bar t contains only pre-t trades.
- **Next-bar entry**: `n_ask/n_bid` via per-slug `shift(-1)` with 30-min gap guard — honest (modulo C1 phantom quotes).
- **Binance settlement alignment**: `S_T` = close of the candle *opening* at `T_pm-60` = candle labeled 12:00 ET; identical convention in `evaluate.load_panel:45`, panel `T_pm = end_dt+60`, and `paperbot.resolve_positions:316`. Binance 1m close indexed at open+60s in `build_panel2.py:64` is known at bar time — no lookahead.
- **S_idx cancellation**: `K_eff = K*S_idx/S_bin` and `F = S_idx*e^{c*tau}` make d2 and x independent of S_idx, so any index-timing imprecision cannot leak into FV.
- **NO-side algebra** in `eval_side` (price `1-n_bid`, payoff `1-label`, hedge `+delta*ret`) — signs correct; taker fee applied on entry only, redemption free — matches the fee brief.
- **maker_pending at expiry**: unfilled pendings are *cancelled*, never settled as phantom positions (`resolve_positions:301-313`); retro-fill window correctly capped at `min(placed+life, T_pm)`; `am_pending` conservatively cancels rather than fabricating a past taker fallback. `deployed_capital` counting pendings is conservative reservation, fine.
- **label convention**: `result_id==0` <=> outcome_0 wins, and `outcome_0 == 'Yes'` for all 12,277 universe rows.
- **Sizing numerics**: entry floors (`max(entry,0.02)`), `max(avail,0)`, MIN_POS gate — no div-by-zero or negative-share path; tiny-tau delta blowup self-limits through the margin term in `cap_share`.

## Corrected incumbent scoreboard (after C1 exclusions; log-hedge metric unchanged)
| | train ev_ev (t) | OOS ev_ev (t) |
|---|---|---|
| S1 taker-YES | +6.76c (2.47), n=118 | +2.40c (0.66), n=166 — **+0.65c with true linear hedge** |
| S2 maker-YES (bar rule) | +1.80c (2.51), n=3436 | +1.69c (1.17), n=883 |

Bottom line: **S2 (maker) survives the audit; S1 (taker) does not survive OOS once phantom crossed quotes and the log-hedge subsidy are removed.** The REPORT.md tape-verified S2 number (+4.26c) was produced by a separate (now-absent) script and should be regenerated with the crossed-quote filter before being cited.
