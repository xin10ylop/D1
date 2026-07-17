# Hypothesis Register

Every hypothesis gets tested and logged in `results/results_table.csv` — wins AND losses.
Convention: "PM" = Polymarket daily/hourly strike ladder; "FV" = options-implied fair value
(digital price from Deribit surface, timing/underlier-aligned); "edge" = PM executable price vs FV
after fees on the executable side.

## A. Cross-venue level mispricing (the backbone)
- **H1**: PM YES is systematically rich for far-OTM strikes (lottery demand): PM_ask ≫ FV for low-prob YES. Sell side blocked (no shorting without inventory) ⇒ expressed as NO cheap: buy NO when PM_yes_bid ≫ FV.
- **H2**: PM YES is systematically cheap for high-prob (deep ITM) strikes (retail dislikes paying 97¢ to win $1; capital lockup) ⇒ buy YES when FV − PM_ask > threshold.
- **H3**: Mispricing magnitude grows with time-to-expiry (less attention, wider books early in market life).
- **H4**: Mispricing concentrates in off-hours (US night, weekends) when market-makers thin out.
- **H5**: PM lags Binance moves: after a ≥k·σ 15-min Binance move, PM strikes reprice with delay ⇒ FV-vs-PM gap predicts next PM move direction (post-fee, at depth).
- **H6**: ETH/SOL/XRP ladders are more mispriced than BTC (less MM attention on smaller assets).
- **H7**: Divergence direction is asymmetric by side of spot: strikes just-below-spot (likely YES) vs just-above (unlikely YES) show different bias signs (favorite-longshot).
- **H8**: PM prices ignore the vol smile: PM implied distribution is closer to lognormal/flat-vol than options RND ⇒ systematic sign pattern of PM−FV across moneyness that flips at predictable strikes.
- **H9**: Hourly ladders are more mispriced than dailies (retail-dominated, faster decay, MMs can't keep up) — but fee 7%·p(1−p) may eat it; test net.
- **H10**: Term structure disagreement: for same strike on consecutive PM expiries, PM's implied daily migration probabilities are incoherent vs options forward-vol ⇒ calendar spread on PM (buy cheap expiry, sell rich) captures convergence with less directional risk.

## B. PM-internal coherence (no options needed; near-riskless)
- **H11**: Monotonicity violations within a ladder (P(>K1) < P(>K2), K1<K2, executable after spread+fees) exist and are harvestable via verticals (buy YES K2-sell-equivalent... expressed as YES(K2) + NO(K1) bundle costing < $1 with certain $1 payout).
- **H12**: Butterfly negativity: implied density from adjacent strikes goes negative (convexity violation) — tradeable 3-leg.
- **H13**: Cross-expiry monotonicity: P(S_T2 > K) vs P(S_T1 > K) inconsistencies for T1<T2 beyond what any vol/drift model allows (e.g., European digitals must satisfy no-arb bounds via forward vol).
- **H14**: Sum-of-ladder coherence: total implied density mass far from 1 flags ladder-wide mispricing episodes; direction of correction predictable.

## C. Convergence & path (early exit)
- **H15**: PM−FV gaps mean-revert to FV (not the reverse): entering on |gap|>threshold and exiting at gap≈0 before expiry beats hold-to-settlement on EV per day of capital.
- **H16**: Gap half-life shrinks as expiry approaches ⇒ best risk-adjusted entries at intermediate TTE (1–4 days), not at listing or final hours.
- **H17**: Settlement drift: final 2h before 12:00 ET, PM prices converge to a step function slower than realized probability ⇒ end-game trades (buy near-certain YES at 97-98¢ with FV>99.5%) yield high annualized return; test capacity + tail risk explicitly.

## D. Cross-asset structure
- **H18**: BTC ladder repricing leads ETH/SOL/XRP ladders after common BTC-driven moves ⇒ trade the laggard's unadjusted strikes.
- **H19**: Relative-value: PM-implied distributions across assets violate cross-asset vol relationships priced by options (e.g., PM prices SOL tail ≈ BTC tail while options price 2× vol) — pairs of PM positions across assets.

## E. Microstructure / book & tape signals
- **H20**: Book imbalance (depth-weighted bid vs ask within 5¢ of mid) predicts next PM mid move ⇒ filter/improve entries of A-strategies.
- **H21**: Aggressor flow (signed taker volume, from trades side) is CONTRARIAN at strike level: retail market-buys YES on spikes; fading flow beats following it when gap-to-FV agrees.
- **H22**: Spread widening + depth pull precedes large repricings (MMs see Binance first); a "book fragility" state raises expected |ΔPM| ⇒ avoid resting quotes then, or take stale quotes fast.
- **H23**: Large resting walls act as magnets/barriers: price approaches wall, wall pulls, price gaps — detectable pattern, exploitable in maker placement.
- **H24**: Maker-only execution of A-strategies (resting inside the spread on the cheap side) captures most of the gross edge with zero taker fee — measure fill probability conditional on subsequent FV path (adverse selection cost).

## F. Hedged / combined structures
- **H25**: Delta-hedged PM position (PM digital + Deribit perp hedge sized to digital delta) cuts variance enough to run 3–5× position count at same risk ⇒ higher capacity on the same edge.
- **H26**: PM digital vs Deribit vertical (tight call spread replicating the digital) locks in the level gap as near-arb; net of both venues' fees and spread costs, is residual > 0?
- **H27**: Vol view routing: when PM ladder implies distribution wider/narrower than Deribit smile, trade the *pair* of PM strikes (straddle-of-digitals) rather than one leg — captures shape error, immune to level error.
- **H28**: Weekend vol discount: options markets price weekend vol lower (calendar-time decay while realized vol drops); PM retail prices flat ⇒ systematic PM-rich pattern into weekends for OTM strikes, PM-cheap Monday.

## G. Regime/event conditioning
- **H29**: Around US macro releases (CPI/FOMC 8:30/14:00 ET), options reprice event vol; PM ladders lag ⇒ pre-event PM cheapness in tails, post-event convergence trades.
- **H30**: Fee introduction (2026-01-05, crypto 15-min; later category-wide) structurally reduced latency-arb efficiency of PM quotes ⇒ post-fee gaps are wider and slower to close (more edge for maker strategies, less for takers). Quantify regime break.

Falsification standard: each H is tested on train (dates ≤ 2026-05-15), thresholds tuned there; survivors re-run untouched on test (2026-05-16 → 2026-07-16). Report: n, hit rate, EV/trade after fees at $500 and $2000 clip, drawdown, capacity.
