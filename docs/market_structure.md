# Market Structure Audit — Polymarket Crypto Strikes vs Deribit Options

*Everything below verified from primary sources (market descriptions in Telonex metadata, Deribit API, official docs) on 2026-07-17. This is the foundation for all pricing; nothing downstream is valid if anything here is wrong.*

## 1. Polymarket crypto strike products

Three product generations, all binary "Will the price of X be above $K ...?" shares paying $1.00 if YES, $0 otherwise:

| Product | Slug pattern | Resolution rule (verbatim from market description) | Expiry cadence | Strikes | Coverage |
|---|---|---|---|---|---|
| **Daily ladder** | `{asset}-above-{K}-on-{month}-{day}[-{yyyy}]` | Binance **1-minute candle for X/USDT labeled 12:00 ET (noon)** on the date; its final **Close** must be **strictly higher** than K → YES | one per day, resolves ≈12:01 PM ET = **16:00 UTC (EDT) / 17:00 UTC (EST)** + 1 min | ~11–20 per day | BTC/ETH/SOL/XRP; tick data from 2025-10-11 |
| **Hourly ladder** | `...-{h}{am\|pm}-et` | Binance **1-hour candle that ENDS at the stated hour**; Close strictly higher than K → YES | every hour | 20 strikes, $200 spacing (BTC) | BTC/ETH from ~Oct 2025; SOL/XRP recent (~440 mkts) |
| **Multistrike 4h** (legacy) | `btc-multistrike-4h-{unix}-{K}` | Binance 1m candle at stated instant | 4-hourly | ~15 | Sep 2025 era, discontinued |

Key resolution facts:
- **Resolution source is Binance spot X/USDT candle Close — NOT an oracle, NOT Deribit, NOT an index.** Chainlink `crypto_prices` feed (Telonex channel) is used by *other* Polymarket crypto products (15-min up/down); the strike ladders explicitly cite Binance candles.
- Daily: the candle **labeled** 12:00 ET (spans 12:00:00.000–12:00:59.999 ET); its close is effectively the 12:01:00 ET price. Hourly: the 1h candle **ending** at the stated time (e.g. "3PM ET" = candle 2:00–3:00 PM ET close).
- "higher than" is strict: tie (close == K) → NO. Binance price precision applies (BTC 2 decimals).
- UMA optimistic oracle certifies; observed `settled_at - end_date` ≈ 14–17 min. Capital in winning shares is locked until settlement.
- Markets list ~7.7 days pre-expiry (daily product) → at any instant ~7 live daily expiries per asset + hourly ladder + longer monthlies (sparse).

Fees (polymarket docs, July 2026):
- **Maker: 0. Taker: fee = shares × feeRate × p × (1−p), feeRate = 0.07 for crypto category.** At p=0.5 → 1.75¢/share; p=0.9 → 0.63¢/share. Introduced 2026-01-05 on 15-min crypto series (explicitly to kill latency arbitrage vs Binance), now applies to crypto category per docs. **Fee regime changed mid-sample** — pre-Jan-2026 crypto was fee-free. Model current fees for EV; treat pre-fee period as signal-research only.
- No gas, no deposit/withdrawal fees, no winnings fee. Maker rebates redistribute taker fees to LPs on these series.
- Tick size / min order: verify empirically from book data (typically $0.01, finer near 0/1; min 5 shares).

CLOB mechanics: YES and NO are separate ERC-1155 tokens but one shared book: a YES bid at p is fillable against a NO bid at 1−p (CTF exchange nets them); Telonex provides both outcome partitions. Buying NO at q ≡ selling YES at 1−q. Early exit = sell shares back into the book any time pre-resolution.

## 2. Deribit (options benchmark venue)

- **Instruments**: BTC & ETH inverse (coin-settled) European options, contract = 1 coin; SOL/XRP/etc. linear **USDC-settled** options (`SOL_USDC-*`). Strikes ~unlimited near expiry. Expiries all at **08:00 UTC**: dailies (next ~3 days), weeklies (Fridays), monthlies (last Friday), quarterlies.
- **Settlement**: expiry settlement price = **30-minute TWAP of the Deribit index** (multi-exchange composite incl. Binance, Coinbase, Kraken, ...) before 08:00 UTC. So even a "matching" expiry differs from Polymarket's single-candle-close rule — different instant, different source, TWAP vs print.
- **Fees**: options 3.0 bp of underlying per contract, maker = taker, **capped at 12.5% of premium** (crucial for cheap OTM options); perp/futures taker 5bp→3.5bp (Aug 2026 schedule), maker 0→1.5bp. Options settlement fee 1.5bp (capped), waived for high tiers.
- **History**: `history.deribit.com` serves full expired-instrument list (BTC options to 2016; ETH to 2019; SOL_USDC from 2024-02; XRP_USDC as well) and the **full trade tape per currency** — every option trade carries `iv` (execution IV) **and** `mark_price` + `index_price` at trade time. ~13–17k BTC option trades/day. No historical order books or mark history via public API; TradingView bars are trade-carry-forward (stale for wings — do not use as marks).
- Perp + dated futures + DVOL hourly bars available full-window via TV chart endpoints (perps trade continuously so bars are fresh).

## 3. Pricing relationship (first principles)

A Polymarket YES share at strike K, expiry T is a **European cash-or-nothing digital call on Binance X/USDT** paying $1:
`V_yes(t) = DF(t,T) × P^Q(S_T > K)` with DF ≈ 1 (T ≤ 7d ⇒ ≤ ~10bp at 5% rates; hourly ≈ 0). NO share = DF × P^Q(S_T ≤ K).

From an options chain: risk-neutral P(S_T > K) = −∂C/∂K at K ⇒ with Black-76 on forward F and smile σ(K,T):
`P(S_T > K) = N(d2) − φ(d2)·√T·(∂σ/∂K)·F·√T…` — concretely we compute the digital as **N(d2) minus the skew correction** `φ(d2)·√T·F·(dσ/dK)|_K` (skew matters: BTC put skew makes raw N(d2) overstate P(above) for low strikes).

Translation layers (each one audited, each a potential fake-edge source):
1. **Time**: PM expiry 16:00/17:00 UTC vs Deribit 08:00 UTC ⇒ interpolate **total implied variance** in T at fixed moneyness between the two straddling Deribit expiries. Overnight/intraday vol seasonality is second-order at 1–7d horizons but non-zero for hourly markets (vol-time vs clock-time weighting tested explicitly).
2. **Underlier**: Binance USDT price vs Deribit USD composite index. Basis measured empirically (typically <10bp; USDT depeg regimes are the tail risk). Effective Deribit-strike for PM strike K: K × (Index/BinancePx) observed at t.
3. **Settlement style**: PM = single 1m-candle close; Deribit = 30-min TWAP. For fair-value comparison at t < T this only matters through variance-of-difference at expiry (small; quantified in validation).
4. **Measure**: options give risk-neutral probabilities; PM is also a traded risk-neutral price. No drift adjustment needed — both are prices of the same $1 Arrow-Debreu claim, differing only in collateral/fee/friction structure.

Why the two can diverge (hypothesis families): retail favorite-longshot bias, lottery demand for cheap YES, PM taker fee wedge (post Jan-2026), capital lockup + no leverage on PM vs portfolio-margined options, latency (PM slower to reprice after Binance moves), book thinness at off-spot strikes, weekend/overnight attention gaps, oracle/settlement risk premia.

## 4. Data inventory decisions

| Need | Source | Window |
|---|---|---|
| PM quotes (BBO every change), books (5/25/full), trades | Telonex API, per (market, outcome, day) parquet | 2025-10-11 → present |
| PM resolution labels | `result_id` in Telonex markets metadata + recomputed from Binance 1m klines (must agree) | full |
| Exact resolution feed | Binance official kline archive (data.binance.vision), 1m/1h X/USDT | full |
| Options surface | Deribit history trade tape (iv, mark, index per trade), BTC+ETH+SOL_USDC+XRP_USDC | 2025-10-01 → present |
| Forward curve | Deribit perp+dated futures TV bars; Binance spot | same |
| Vol regime | DVOL BTC/ETH hourly | same |
| PM oracle ticks (secondary) | Telonex `crypto_prices` | 2026-04-02 → present |
