# New-families research — findings

(Recovered from agent return; scripts/grids/trades on disk in this directory.)

Discipline: train = expiry < 2026-05-16, tune on train only; ONE frozen test look per family;
event-clustered t; dedup 1 signal/market/24h. Methodology: audit-C1-clean quotes (spread>=0,
crossed next-bars nulled), audit-M2 LINEAR hedge, tape-print fills, funding sign per side.

## Scoreboard (hedged EV/share, event-clustered t; LINEAR hedge)

| family | frozen config | train ev_ev (t) [n/ev] | TEST look (t) [n/ev] | verdict |
|---|---|---|---|---|
| 1 maker verticals | best cell | +0.5c (0.86) all-in; both-fill mirage +10.3c | not spent | reject (train) |
| 2 settlement YES | — | +0.8c (0.54) [77/72] | not spent | reject (train) |
| 2b settlement NO | NO 90-97c, fv_sea<1% | +1.85c (2.24) [131/124] | -0.87c (-0.25) [28/28] | reject |
| 3 spike-fade | othr=5c both | +7.40c (3.53) [254/187]; fill +2.7c | +2.65c (0.81) [78/56]; fill -2.6c | reject |
| 4 YES-basket | — | basket <= best1 everywhere | not spent | reject (train) |
| 5 maker-NO | thr=3c, tte 1-3d | +3.32c (4.11) [1352/507]; fill +0.1c | +0.60c (0.30) [286/142]; fill -2.8c | reject |

Key mechanics:
- Verticals: legging risk kills it — 40-46% of signals strand a lone leg whose taker unwind
  bleeds the edge; the both-legs-filled subset (+10c) is ex-post selection.
- Settlement-zone: fv_sea still ~2pp overconfident in the 99-99.5 bucket where YES buys live;
  the NO mirror is a 35:1 lottery-short whose one OOS loss (1/28) erased two months of clips.
- Spike-fade: PM UNDER-reacts on average after >=1.5-sigma moves (lag, not overshoot); the
  bookable fill-time version is negative OOS.
- Basket: daily ladders offer only ~1.2-1.5 simultaneously-cheap strikes — nothing to diversify.
- Maker-NO (corrected sign): lives at 1-3d tte on train (t=4.11) but +0.60c t=0.30 OOS with
  fill-time negative and funding flipped to a cost. Watch-list.

Bottom line: no new family survives corrected evaluation. Incumbent S2 maker-YES remains the
only strategy with OOS support. Watch-list: maker-NO tte 1-3d; settlement-NO (tail inestimable
at current n).
