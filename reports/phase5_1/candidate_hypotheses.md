# Veyra Phase 5.1 — Candidate Hypotheses (next research phase)

These are hypotheses only. NOT implemented or optimised now. Each states the motivating observation, the rule change, its logical connection, and the falsifying evidence.

## H1 — Stronger regime filtering

- **Motivation:** regime slice shows performance concentrated in specific regimes (see regime_analysis).
- **Rule change:** restrict entries to regimes where pooled evidence is positive; drop entries from negative-regime slices.
- **Why connected:** if bulk losses concentrate in one regime label, filtering that label directly removes the losing trades without touching the edge elsewhere.
- **Falsified by:** a regime slice with a large n that is clearly negative would be removed — but if the negative regime still has a positive expectancy after cost (unlikely from diagnosis) it must NOT be filtered.
- **Alternative falsification:** if filtering the worst regime does not improve OOS, the filter is cosmetic.

## H2 — Different confirmation requirements (esp. weak-score setups)

- **Motivation:** score monotonicity is absent/anti-monotonic; forcing entry only at higher scores is one reading.
- **Rule change:** require a higher score threshold OR an additional volume/price confirmation before entry (as a RESEARCH gate, not a changed frozen baseline).
- **Why connected:** if higher buckets are not better, raising the threshold is not guaranteed; thus this is a test, not a fix.
- **Falsified by:** if restricting to high-score buckets (n large enough) yields no OOS edge, the score is not a useful prerequisute.

## H3 — Setup-family removal (RANGE_REJECTION / weakest family)

- **Motivation:** RANGE_REJECTION produced zero trades; other families may account for most losses.
- **Rule change:** evaluate the marginal contribution of each setup family by removing it (hold-out style, on IN/VAL segments — NOT OOS).
- **Why connected:** removing a loss-dominant family should raise pooled net return if the family is net-negative.
- **Falsified by:** if the family is net-positive or if removal lowers OOS performance, keep it.

## H4 — Revised entry logic (timing / adverse excursion)

- **Motivation:** MAE/MFE ratio near 1 suggests adverse travel matches favorable travel (poor timing).
- **Rule change:** test requiring confirmation of the direction immediately after trigger before committing (e.g. no entry on candle 0 adverse move).
- **Why connected:** reducing immediate adverse travel directly reduces MAE and thus expected stop hits.
- **Falsified by:** if MAE/MFE is clearly < 1 across most trades, timing is not the issue and this change would be unnecessary.

## H5 — Revised invalidation / stop placement

- **Motivation:** high expiration and stop-hit counts may reflect stops too tight or invalidation too late.
- **Rule change:** compare stop placement under a fixed holding-period assumption (research only).
- **Why connected:** changing stop distance changes win rate and MAE; a trade-off must be tested on IN/VAL, never chosen on OOS.
- **Falsified by:** if results are insensitive to stop distance, placement is not a first-order cause.

## H6 — Revised scoring structure (research only)

- **Motivation:** score does not rank quality (question 3).
- **Rule change:** rebuild a score in a future research phase that first demonstrates monotonic ranking on IN data before any deployment.
- **Why connected:** a score that does not rank cannot gate entries.
- **Falsified by:** if no score structure can rank IN buckets monotonically with adequate sample, abandon score-based gating.

**Not supported by current evidence:**
- ML-based prediction (rule 4) — unsupported by evidence and outside scope.
- Live/broker execution — outside scope (rule 5).
- Broad indicator proliferation (rule 6) — unsupported.
- Parameter optimisation (rule 7) — expressly out of phase for this diagnostic task.

Each hypothesis must be tested on IN/VAL segments and only ever confirmed on OOS with a full, fixed sample size reported.