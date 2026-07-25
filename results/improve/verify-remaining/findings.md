# verify-remaining: adversarial verification of the two unverified claims

## CLAIM 1 (mechanism-extend) — PARTIALLY REFUTED
Confirmed: labels 1034/1034 independently re-derived (DST handled; k-suffix strike fix real);
no survivorship; no smile lookahead; exact reproduction of their outputs; skip-slot convergence
design valid; pooled asymmetry (price->FV 0.048 t=4.5 @1h) and bucket-level mispricing
(2024 .90-.97 bucket: vwap .929 < fv .938 < label .982, t=5.5) both reproduce.
REFUTED at magnitude: signal_ev references hedge at slot-t spot while entry is next-slot fill
VWAP -> gap>5c selects on -64bp pre-entry drops and credits the hedge +6.1c/sh (t=16.5) for them.
Corrected: 5c/0-8d +7.55c (t4.78) -> +1.71c (t1.80); S1-analog +6.79 -> +0.95c (t0.89);
2.5c +3.48 -> +0.34c (t0.13); quarters 6/7 positive -> ~3/8. "FV never chases PM" fails
within 2024 (asymmetry is a 2025 phenomenon). Old-era mispricing exists at ~+1-2c/sh, t<2.
Control: incumbent numbers NOT impugned (synchronous quotes; timing cost -0.2..-0.3c measured).

## CLAIM 2 (hedge-lab funding) — CONFIRMED, t-stats corrected
Sign convention verified empirically (99.8% sign agreement funding vs perp premium);
series matches independent API re-fetch exactly (max diff 0.0); per-share scaling reproduces
to <=0.0007c/sh; magnitude +0.05-0.11c/sh stands. t up to 7.9 was overlap-clustering artifact;
honest monthly clustering: t 1.2-3.2. Book ~+0.1c credit; cite honest t.
