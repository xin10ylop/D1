# hedge-lab findings (recovered from workflow journal)

## Headline
Perp hedge costs are small (net ~-0.45c/sh: -0.53c fees +0.08c funding) and static full-delta is optimal — but maker (S2) hedged EV is convention-fragile: hedging at fill time instead of signal time flips it from +1.2c to -3.6c/sh OOS (adverse selection worth ~5c/sh), while taker (S1) is robust and holds +3.59c/sh OOS under the all-in frozen policy.

## Funding P&L accounting (short perp earns funding) — ADOPT
- train: taker +0.108c/sh (t=5.7), maker +0.050c/sh (t=7.3); positive in 8/10 months (range -0.05c 2026-02 to +0.29c 2026-07); BTC +0.09c > XRP +0.05c; corr with hedge P&L +0.12..0.20 (no adverse coupling)
- test: taker OOS +0.085c/sh (t=2.9), maker OOS +0.092c/sh (t=7.9) — adds to, never subtracts from, the +4c edge
- Deribit 8h funding (public API, hourly interest, Oct-25..Jul-26, all 4 assets) summed over each trade's holding window at measured delta notional. Ignoring funding was conservative; book it as a ~+0.1c credit. funding_pnl_summary.csv

## Daily / 2%-move rebalanced hedge vs static — REJECT
- train: taker static 8.29c vs daily 6.60c (3.5 rebals, +0.33c fees) vs move-2% 4.48c (7 rebals); sd falls 0.295->0.228 but EV drops 1.7-3.8c/sh
- test: diagnostic on same fixed OOS lists (prior-run): static 3.92c vs daily 2.29c vs move-2% 3.42c (t 1.62, sd 0.196) — same ordering on EV
- Re-striking a digital's delta near K is negatively convex and fee-heavy; variance reduction is real (8-23%) but EV-primary discipline says static wins. Keep static-at-entry. rebal_summary.csv

## Dated-future hedge (nearest expiry >= PM expiry) vs perp — REJECT
- train: matched subset (78-80% coverage): taker ev_fut 10.77c vs ev_perp-at-fill 9.53c, ~0 after realistic 2.5-5bp extra half-spread (tick 2.5 vs 0.5 BTC); corr 0.98; entry basis +3..9bp; no funding earned
- test: not tested (train-only decision; not part of frozen policy)
- All-in a wash at best; dated books are thin (1-4 BTC/hr vs 150-300 perp) and need per-expiry rolls; perp's funding credit covers the basis give-up. Deribit does list daily/weekly futures near PM expiries but they only exist ~2-4 days out. datedfut_summary.csv

## Hedge ratio grid h in {0,0.5,0.75,1,1.25} x delta — ADOPT
- train: taker: h=1.0 best mean-var (mv λ=1: +0.010) and min maxDD ($202/100sh vs $563 at h=0.5, $1089 unhedged); h=1.25 higher mean (9.33c vs 8.29c) but worse mv/DD — bear-drift harvesting, not robust; under-hedging strictly dominated (hedge all-in cost only ~0.45c)
- test: h=1.0 is the frozen policy tested below
- Keep full digital delta. ev(h)=ev_raw - h·delta·ret_T + h·funding - 2×3.5bp·h·delta, event-clustered t, chronological bankroll sim at 100 sh/trade. ratio_summary.csv

## Small-account granularity ($10/$1 contracts) — ADOPT
- train: at $10 premium clip: hedge notional ~$150-270, quantization error ~$2 (2.7% rel), +0.8c/sh noise vs 30c/sh trade sd, EV shift ±0.03c, zero-hedge 0.3% of trades; at $50 clip: 0.5% rel error
- test: not tested (analytic/train-descriptive)
- Quantization is a non-issue (BTC $10, ETH $1, SOL 0.1 SOL~$12, XRP 10 XRP~$18 contracts). Real small-account constraint: hedge notional is ~15x the PM premium (delta 5-9 per share), i.e., margin/liquidation management. quantization_summary.csv

## RISK FLAG + frozen policy test look: hedge timing — PROMISING
- train: maker hedged-at-fill -3.3c (t -4.3) vs +1.9c signal-convention; fills occur on -84bp spot moves; pre-hedging quotes costs -4.5c per unfilled quote (t -21, 46% unfilled) => all-in -1.9c per filled share. Taker timing cost only -0.2c
- test: ONE look, frozen policy (static perp, h=1.0, at fill, +funding, -2×3.5bp): taker +3.59c/sh (t=1.02, n=173, 78 events); maker same-list decomposition: signal-time +1.17c (t=0.9) vs fill-time -3.55c (t=-2.58) — OOS confirms the adverse-selection flag
- S1 taker survives all-in hedge costs (+4.35c headline -> +3.59c: -0.53c fees, +0.08c funding, -0.32c timing). S2 maker's edge as modeled is mostly the pre-fill down-move; it is NOT capturable by hedging at fill nor by pre-hedging every quote. Needs tape-level fill-timing verification (execution lab) before trusting the +4.26c maker headline; consider hedge-on-partial-fill or quote-only-when-pre-hedged-cheaply designs. test_look_summary.csv, maker_prehedge_summary.csv

## Caveats
(1) Maker numbers use the conservative bar-fill rule (ask<=limit within 16 bars), which gives +1.6c train / +1.7c OOS clustered — below the +4.26c tape-verified headline — and maximizes measured adverse selection; real tape fills without book collapse would sit between the signal (+1.2c) and fill (-3.6c) conventions. (2) Labs 1-2 OOS rows were computed by a prior run of this lab on the same fixed incumbent OOS lists before the final policy freeze; they are descriptive decompositions, not tuning, but strictly they are extra test exposure. (3) Perp fill prices are 1-min bar closes (<=15-min tolerance), dated futures 1-h closes (<=2h tolerance, thin books) with assumed 2.5-5bp extra half-spread rather than measured book spreads. (4) 3.5bp/side perp fee per task convention; Deribit's listed taker fee is 5bp (dated futures have a -1bp maker rebate), which would shave a further ~0.2c/sh. (5) Funding regime was benign (shorts earned) for most of the sample; a sustained negative-funding regime would flip the ~+0.1c credit to a similar-sized cost. (6) findings.md not written (harness disallows report files); the brief's stdout table and all CSVs are in results/improve/hedge-lab/.