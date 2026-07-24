# FV upgrade research — findings

Researcher: fair-value model. Train = expiry < 2026-05-16, tuning on train only; one test look at the end.
All FV recomputed from smiles_*.parquet on the existing panel rows; baseline reproduction of the incumbent
panel FV is exact (max |dfv| ~1e-13, `run_train.py`).

## Headline
Adopt **seasonal vol-time** (hour-of-week realized-variance profile replacing clock-time in the
variance interpolation/extrapolation). It measurably improves calibration exactly where the incumbent
is weakest (extrap zone, tte<1d) on train AND on the one test look, and leaves the S1/S2 signals
statistically unchanged-to-slightly-better. The other three candidates (mark-IV smiles, RV blend,
Student-t tails) are rejected on train.

## 1) Intraday vol seasonality — ADOPT
Profile: mean squared 1m binance log-return per hour-of-week (train only, winsorized 99.9%),
normalized to mean 1 (`vol_profile_hourweek.csv`). Facts: 14:00-16:00 UTC runs 1.8-2.2x average
variance; 03:00-11:00 UTC runs 0.55-0.7x; Sat/Sun 0.5-0.7x; Friday 1.24-1.32x. The extrap zone
(expiry day 08:00->16:00 UTC) spans exactly the quiet-morning-to-US-peak transition, so flat clock
scaling badly misallocates remaining variance there.

Implementation: cumulative seasonal variance-time V(t); interp frac and extrap ratio computed in V
instead of clock tau (`fv2.py: core()`), anchored to the fitted total variances at the Deribit expiries.

Train calibration (3.12M rows):
| scope  | clock Brier | seasonal | clock logloss | seasonal |
|--------|------------|----------|---------------|----------|
| all    | .059882    | .059809  | .195692       | .195283  |
| extrap | .026770    | .026603  | .091705       | .089985 (-1.9%) |
| tte<1d | .028112    | .027968  | .095269       | .093573 (-1.8%) |

Reliability, buy-side buckets (train): extrap bucket (0.95,0.98]: label 0.9478 under clock (2.0pp
overpriced) -> 0.9573 under seasonal (1.0pp); tte<1d (0.95,0.98]: 0.9580 -> 0.9703 (bias eliminated).
The incumbent's short-tte overconfidence in likely-YES — the shares we buy — is roughly halved.
(Low-fv buckets 0.05-0.4 remain overpriced ~3-7pp under both: that is the risk-neutral drift wedge,
not a vol-time artifact.)

Test look (calibration): logloss all .148390->.148195, extrap .061290->.060401 (-1.5%),
tte<1d .064225->.063309 (-1.4%); Brier flat (extrap slightly better). Same signature as train.

## 2) Mark-IV smiles — REJECT (wash)
April 2026 (train), BTC+ETH: inverted Black-76 mark-IV (coin-quoted, F~index) for 469k trades
(99.7% converge; median |markIV-tradeIV| = 0.7-0.9 vol pts), fitted smiles with identical code.
FV Brier on 322k joint panel rows: trade .045732 vs mark .045765; every scope within +/-0.0001,
mixed sign (`train_markiv_apr26.csv`). Deribit trade IVs are already effectively marks at our
15-min smile granularity. Not worth the pipeline change.

## 3) EWMA RV blend — REJECT
sigma_blend = w*IV + (1-w)*RV, EWMA half-lives {2h,6h,24h,72h}, w grid 0.5-1.0, per tte band,
on the seasonal base. **w=1.0 (pure IV) is optimal in every band and for every half-life; Brier is
monotonically worse as w falls** (`train_blend_grid.csv`). Options IV subsumes realized vol here.

## 4) Student-t tails — REJECT
Standardized-t terminal dist, df in {3,4,6,8,12}. Train calibration gain is tiny at df 8-12
(logloss .195283->.195054/.195006; Brier -0.0001) and df<6 is worse. But t-tails lift OTM digitals,
flooding S1 with weaker signals: train S1 EV/share drops 10.0c->7.4c (t8) / 8.7c (t12) — worse on
the primary metric — so excluded from the frozen variant. (The test run incidentally emitted these
columns and confirmed: t8 S1 OOS +0.9c, t=-0.5.)

## Signals with frozen seasonal FV (identical rule constants, delta from variant sigma)
Train (`signals_train.csv`):
| strategy | variant | n | hedged EV/sh | t |
|----------|---------|-----|-----------|-----|
| S1 taker | incumbent | 340 | +10.01c | 4.03 |
| S1 taker | seasonal  | 337 | +10.00c | 4.00 |
| S2 maker | incumbent | 3261 | +3.63c | 4.54 |
| S2 maker | seasonal  | 3238 | +3.67c | 4.65 |

Test — the ONE look (`signals_test.csv`):
| strategy | variant | n | hedged EV/sh | t |
|----------|---------|-----|-----------|-----|
| S1 taker | incumbent | 173 | +5.37c | 1.24 |
| S1 taker | seasonal  | 175 | +4.74c | 1.17 |
| S2 maker | incumbent | 945 | +5.33c | 3.14 |
| S2 maker | seasonal  | 917 | +5.81c | 3.19 |

S1 is 3-8d tte, where the diurnal profile integrates out -> no change by construction (test -0.6c is
inside noise at n~175, 78 events). S2 trades all tte including expiry day: seasonal drops ~30 trades
and adds +0.48c/sh OOS with a higher t. Net signal impact: neutral (S1) to mildly positive (S2).

## Recommendation
1. Fold seasonal vol-time into the FV build (port `fv2.core(voltime=...)` into build_panel2; the
   hour-of-week profile is a 168-vector per asset, train-estimated, very stable across assets).
   Justification is calibration (train + OOS confirmed) and it de-biases the expiry-day zone where
   any future short-tte strategy would live; it does not degrade the frozen S1/S2.
2. Do not switch smile source to mark-IV; do not blend RV; keep Gaussian tails.
3. Largest remaining calibration defect is the low-bucket drift wedge (fv 5.1c vs label 2.2c in the
   2-10c bucket train-wide) — a risk-neutral vs physical drift question, out of scope here, flagged
   for the drift/regime researcher.

## Files
- `fv2.py` — recompute engine (attach_smiles/core/digital, vol profile, EWMA RV, metrics)
- `run_train.py` (baseline repro + seasonal), `run_blend_tails.py` (grids), `run_markiv.py`,
  `run_signals.py` (S1/S2 train/test), `run_diag.py` (S2 bands, reliability)
- CSVs: `vol_profile_hourweek.csv`, `train_seasonal_summary.csv`, `train_seasonal_by_band.csv`,
  `train_blend_grid.csv`, `train_tails_grid.csv`, `train_markiv_apr26.csv`, `train_reliability.csv`,
  `train_s2_by_band.csv`, `signals_train.csv`, `signals_test.csv`, `test_calibration.csv`
- `panel_fv_variants.parquet` — panel rows + fv_base/fv_sea columns for downstream agents

## Caveats
- Row-level Brier/logloss on 15-min panel rows is serially correlated within market; deltas are
  small in absolute terms though consistent across scopes and confirmed OOS in sign.
- The one test script emitted the (already train-rejected) t8/t12 columns alongside the frozen
  seasonal variant; the freeze decision was made on train before the look, and the extra columns
  only corroborate the rejection.
- Profile is train-estimated hour-of-week; no regime adaptation. DST shifts the ET-anchored
  resolution hour vs the UTC-anchored profile (handled implicitly by using actual timestamps).
