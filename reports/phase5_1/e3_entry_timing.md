# Veyra Phase 5.1 — Experiment E3: Entry Timing / Confirmation

Experiment version: `e3-entry-timing-v1` | Frozen baseline: `phase5-baseline-v1`
Generated: 2026-09-02 | All trade metrics are NET of fees + slippage. Scores are never treated as probabilities.

---

## 1. Hypothesis

Delaying entry by **one additional confirmed bar** after a setup qualifies may
reduce poor entries / adverse excursion and improve forward expectancy,
**especially on 4H**.

## 2. Exact pre-registered change

One single change to the **entry timestamp**, nothing else:

> A setup that qualifies on bar X is filled at the open of bar X+1 in the
> baseline (next-open execution). E3 forces the fill to occur **one bar later**,
> at the open of bar X+2, provided the setup **is still qualified** after bar X+1.

Unchanged: setup detection, targets, stops, position lifetime, scoring weights,
score thresholds, confirmation rules during qualification, regime logic, setup
definitions, volatility parameters, timeframe, fees/slippage, overlap policy, and
the frozen baseline engine itself.

No sweep of confirmation lengths. Only the single one-bar delay is tested.

## 3. Existing entry-path audit

Traced from code, in order:

1. **Detection** — `SetupEngine.detect(snapshot)` (`src/veyra/strategy/setup_engine.py:94`)
   creates a `Setup` in state `DETECTED` from the snapshot for bar i (pipeline sees
   only `candles[:i+1]`).
2. **Qualification** — `SetupEngine.advance(setup, snapshot)` (`setup_engine.py:137`)
   promotes `DETECTED -> DEVELOPING -> QUALIFIED`, each step requiring momentum to
   continue supporting the side (`_confirms`, `setup_engine.py:183`). So reaching
   QUALIFIED already requires two consecutive momentum-confirmation evaluations.
   `advance` also applies age/regime expiry and structure invalidation first, so a
   QUALIFIED setup is dropped as soon as it expires or invalidates.
3. **Trigger / arming** — In the engine loop (`src/veyra/backtest/engine.py:189-247`):
   - step 1 fills entries armed on the previous bar,
   - step 3 detects new setups,
   - step 4 advances lifecycle,
   - step 7 arms `active_qualifying()` setups into `pending_entries`.
   A setup that becomes QUALIFIED on bar X is armed in step 7 of bar X.
4. **`Simulator.enter()`** — `simulator.py:104`. Validates the stop, drops a target
   that is not on the profit side of the fill, applies slippage/spread to the raw
   open, sets state to `TRIGGERED`, records `entry_bar`, and opens the position.
5. **Entry price** — `next_open` fill at `bar.open` (cost-adjusted).
6. **Next-open execution** — entry fills at the open of the bar after the decision
   bar (step 1 of engine loop), and the position is exposed to that same bar's range.
7. **Lifecycle transitions** — step 4 closes a position if its setup went terminal
   (INVALIDATED/EXPIRED/COMPLETED); step 5 checks stop/target; step 6 force-closes
   over-aged positions. Delaying the entry must not disturb these.

**What "one additional confirmation bar" means here.** A QUALIFIED setup is filled
at the open of the bar following qualification. The one-bar delay shifts that fill
to the open of the next bar again — so the entry wait is exactly one more confirmed
bar. This is implemented by intercepting the **first** `Simulator.enter()` attempt
per setup and letting the second attempt (next bar) proceed, provided the setup is
still qualified (the engine re-arms only `active_qualifying()` setups).

## 4. Implementation details

- Runtime override only; the frozen baseline and engine are never modified.
- `Simulator.enter` is patched (mirroring the E2 approach). A per-setup-key counter
  defers the first `enter()` call (returns `None`, no position opened, state stays
  `QUALIFIED`) and proceeds on the second call.
- Because `enter()` is called at most once per setup per qualifying bar, deferring
  the first call and re-entering after re-arm yields **exactly** one additional bar.
- If bar X+1's `advance()` expires or invalidates the setup, `active_qualifying()`
  no longer returns it, so it never enters (no trade). This is the intended
  "confirmation" side effect and does not alter expiry/invalidation accounting.
- Entry is prevented from happening twice: after a successful second `enter()`, the
  setup is `TRIGGERED` and excluded from `active_qualifying()`. Across all four
  datasets **E3 trade counts are strictly lower** than baseline, confirming no
  double-entry.

## 5. Look-ahead / leakage audit

- Chronological replay only; the engine fills entries at the bar open **before**
  analyzing that bar (engine step 1 precedes steps 2/3/4).
- The delayed fill at bar X+2 open is decided using candles up to and including bar
  X+1's close — all available at bar X+2's open. The confirmation bar X+1 is fully
  known before the fill. **No future candle is used.**
- The delay adds no new decision input; it only shifts the entry timestamp one bar.
  No transformation of the filter/rule is informed by VAL or OOS.
- IN / VAL / OOS separation is preserved. The single change is fixed (nothing is
  selected or tuned against VAL/OOS).

## 6. Baseline results (frozen baseline, net)

| Dataset | Split | Trades | Expect | AvgRet | PF | Win% | Exp% | Stop% | Tgt% | Hold | AvgMAE | AvgMFE | MAE/MFE | MaxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTC 1D | IN | 196 | +0.0073 | +0.0073 | 1.31 | 51.0 | 48.5 | 32.1 | 19.4 | 7.0 | 1699.98 | 1836.52 | 0.926 | −0.7882 |
| BTC 1D | VAL | 136 | +0.0083 | +0.0083 | 1.50 | 47.1 | 58.8 | 20.6 | 20.6 | 5.6 | 1475.87 | 1701.36 | 0.867 | −0.6480 |
| BTC 1D | OOS | 105 | +0.0149 | +0.0149 | 2.32 | 53.3 | 54.3 | 26.7 | 19.0 | 7.9 | 2359.55 | 4647.59 | 0.508 | −0.3837 |
| ETH 1D | IN | 237 | +0.0277 | +0.0277 | 2.24 | 55.7 | 46.4 | 28.3 | 25.3 | 5.8 | 114.26 | 151.05 | 0.756 | −0.7369 |
| ETH 1D | VAL | 95 | −0.0120 | −0.0120 | 0.47 | 40.0 | 49.5 | 32.6 | 17.9 | 4.5 | 97.22 | 53.36 | 1.822 | −0.7329 |
| ETH 1D | OOS | 70 | −0.0103 | −0.0103 | 0.69 | 41.4 | 32.9 | 41.4 | 25.7 | 7.6 | 197.01 | 206.83 | 0.953 | −0.8031 |
| BTC 4H | IN | 1879 | +0.0042 | +0.0042 | 1.34 | 44.9 | 54.3 | 31.4 | 14.3 | 6.3 | 535.23 | 704.12 | 0.760 | −0.9739 |
| BTC 4H | VAL | 591 | −0.0007 | −0.0007 | 0.93 | 39.6 | 45.3 | 35.0 | 19.6 | 5.2 | 657.47 | 825.41 | 0.797 | −0.8315 |
| BTC 4H | OOS | 665 | −0.0023 | −0.0023 | 0.80 | 37.6 | 52.8 | 31.9 | 15.3 | 7.5 | 1448.85 | 1815.05 | 0.798 | −0.9289 |
| ETH 4H | IN | 1646 | +0.0010 | +0.0010 | 1.07 | 44.4 | 47.5 | 31.7 | 20.8 | 5.4 | 31.38 | 38.19 | 0.822 | −0.9923 |
| ETH 4H | VAL | 600 | +0.0118 | +0.0118 | 2.20 | 45.8 | 52.3 | 31.0 | 16.7 | 7.5 | 37.55 | 75.78 | 0.495 | −0.8056 |
| ETH 4H | OOS | 647 | −0.0083 | −0.0083 | 0.55 | 36.6 | 45.0 | 36.0 | 19.0 | 7.1 | 79.79 | 66.81 | 1.194 | −0.9994 |

## 7. E3 results

### BTC 1D (VAL)
| Metric | Baseline | E3 | Δ |
|---|---|---|---|
| Trades | 136 | 125 | −11 |
| Expectancy | +0.0083 | +0.0112 | +0.0029 |
| Profit factor | 1.50 | 1.66 | — |
| Win rate | 47.1% | 46.4% | −0.7 |
| Expiration | 58.8% | 62.4% | +3.6 |
| Avg MAE | 1475.87 | 1583.06 | +107.2 |
| Avg MFE | 1701.36 | 1951.55 | +250.2 |
| MAE/MFE | 0.867 | 0.811 | −0.056 |
| MaxDD | −0.648 | −0.646 | +0.002 |

### ETH 1D (VAL)
| Metric | Baseline | E3 | Δ |
|---|---|---|---|
| Trades | 95 | 86 | −9 |
| Expectancy | −0.0120 | −0.0113 | +0.0007 |
| Profit factor | 0.47 | 0.47 | — |
| Win rate | 40.0% | 44.2% | +4.2 |
| Expiration | 49.5% | 50.0% | +0.5 |
| Avg MAE | 97.22 | 102.76 | +5.5 |
| Avg MFE | 53.36 | 59.51 | +6.1 |
| MAE/MFE | 1.822 | 1.727 | −0.095 |
| MaxDD | −0.733 | −0.677 | +0.056 |

### BTC 4H (VAL)
| Metric | Baseline | E3 | Δ |
|---|---|---|---|
| Trades | 591 | 559 | −32 |
| Expectancy | −0.0007 | −0.0013 | −0.0006 |
| Profit factor | 0.93 | 0.87 | — |
| Win rate | 39.6% | 39.9% | +0.3 |
| Expiration | 45.3% | 44.7% | −0.6 |
| Avg MAE | 657.47 | 646.06 | −11.4 |
| Avg MFE | 825.41 | 746.28 | −79.1 |
| MAE/MFE | 0.797 | 0.866 | +0.069 |
| MaxDD | −0.832 | −0.845 | −0.013 |

### ETH 4H (VAL) ← core decision window for the hypothesis
| Metric | Baseline | E3 | Δ |
|---|---|---|---|
| Trades | 600 | 572 | −28 |
| Expectancy | +0.0118 | +0.0118 | −0.0000 |
| Profit factor | 2.20 | 2.25 | — |
| Win rate | 45.8% | 49.7% | +3.8 |
| Expiration | 52.3% | 52.6% | +0.3 |
| Avg MAE | 37.55 | 37.28 | −0.26 |
| Avg MFE | 75.78 | 74.74 | −1.05 |
| MAE/MFE | 0.495 | 0.499 | +0.003 |
| MaxDD | −0.806 | −0.785 | +0.020 |

## 8. IN comparison

Across all four datasets, IN metrics change only marginally (BTC 1D 0.0073→0.0075;
ETH 1D 0.0277→0.0349; BTC 4H 0.0042→0.0046; ETH 4H 0.0010→0.0013), all with slightly
higher PF and win rate but **higher MFE and mixed MAE**. IN is descriptive, not a
decision basis here (nothing is tuned).

## 9. VAL comparison (primary decision window)

- **BTC 4H — deteriorates.** Expectancy −0.0007 → −0.0013; PF 0.93 → 0.87; MAE/MFE
  worsens 0.797 → 0.866; max drawdown worsens. The delay removed trades and did not
  improve what remained.
- **ETH 4H — flat on expectancy.** Expectancy unchanged at +0.0118; PF only
  trivially higher (2.20 → 2.25) driven by win rate; MAE reduction is negligible
  (−0.26 of ~37.5) and MFE fell too (−1.05). No reduction in adverse excursion, no
  improvement in forward expectancy.
- **BTC 1D — improves.** Expectancy +0.0083 → +0.0112, PF 1.50 → 1.66, MAE/MFE
  0.867 → 0.811, max DD improves. Genuine improvement here.
- **ETH 1D — marginally improves but stays negative.** −0.0120 → −0.0113,
  still negative; win rate rises but expectancy remains a loss.

**The central claim fails:** the delay does **not** reduce adverse excursion on 4H
and does **not** improve forward expectancy on the primary 4H window.

## 10. OOS reference

- BTC 1D 0.0149 → 0.0144 (PF 2.32 → 2.49), still positive.
- ETH 1D −0.0103 → −0.0063 (still negative).
- BTC 4H −0.0023 → −0.0024 (still negative).
- ETH 4H −0.0083 → −0.0089 (still negative, slightly worse).

OOS is reference-only and never used for the decision. On 4H it remains negative
under both regimes.

## 11. 4H-specific analysis

The hypothesis is explicitly aimed "especially on 4H." Results on 4H:

- **ETH 4H:** expectancy flat, PF essentially flat, MAE effectively unchanged
  (37.55 → 37.28), MFE effectively unchanged (75.78 → 74.74), MAE/MFE flat
  (0.495 → 0.499). Win rate +3.8 points but **not** from better entry timing — it is
  accompanied by a slightly lower MFE, i.e. the same-or-better win rate came from a
  no-better average trade and lower opportunity (−28 trades). This is the cosmetic
  pattern the pre-registration warns against.
- **BTC 4H:** the delay **damages** forward metrics — expectancy and PF fall, MAE/MFE
  worsens, max DD worsens.

No 4H evidence supports "reduces poor entries / adverse excursion." The MAE on 4H is
not meaningfully reduced relative to MFE and, on BTC 4H, MFE falls far more than MAE.

## 12. MAE/MFE analysis

- **BTC 1D:** MAE/MFE 0.867 → 0.811 — improved ratio, both excursions rise but MFE
  rises proportionally more → better trades on 1D.
- **ETH 1D:** MAE/MFE 1.822 → 1.727 — improved ratio, still adverse-dominant.
- **BTC 4H:** MAE/MFE **worsens** 0.797 → 0.866. Avg MAE fell only 11.4 while avg MFE
  fell 79.1 — the delay cut favorable excursion much more than it cut adverse
  excursion. Adverse excursion was **not** reduced relative to favorable.
- **ETH 4H:** MAE/MFE flat 0.495 → 0.499. MAE and MFE both moved negligibly opposite
  directions, i.e. the delay removed opportunity without improving the excursion
  profile of surviving trades.

The hypothesis that one more confirmation bar reduces adverse excursion is **not
supported** on 4H.

## 13. Setup-family impact

### ETH 4H (IN+VAL)
| Family | BL n | BL Exp | BL PF | E3 n | E3 Exp | E3 PF |
|---|---|---|---|---|---|---|
| BREAKOUT | 674 | 0.0056 | 1.30 | 638 | 0.0047 | 1.25 |
| BREAKOUT_RETEST | 535 | 0.0058 | 1.51 | 518 | 0.0071 | 1.66 |
| PULLBACK | 531 | 0.0014 | 1.11 | 509 | 0.0010 | 1.07 |
| TREND_CONTINUATION | 506 | 0.0023 | 1.18 | 477 | 0.0032 | 1.26 |

Mixed: BREAKOUT and PULLBACK worsen slightly; BREAKOUT_RETEST and TREND improve a
little. No coherent family-level benefit; the aggregate expectancy is flat.

### BTC 4H (IN+VAL)
BREAKOUT 0.0119→0.0106 (PF 1.90→1.83) and TREND −0.0031→−0.0015 improve slightly;
PULLBACK −0.0024→−0.0016, RETEST flat. Still net-negative families under both.

### 1D (see §7/§9)
BTC 1D: BREAKOUT worsens (0.0082→0.0061), but BREAKOUT_RETEST improves strongly
(0.0031→0.0257, PF 1.28→3.07) and PULLBACK best. ETH 1D: PULLBACK improves
(0.0197→0.0282), TREND improves (0.0098→0.0158).

## 14. Opportunity cost / delayed setups

| Dataset | Delayed (first enter deferred) | IN+VAL trades baseline | E3 | Δ |
|---|---|---|---|---|
| BTC 1D | 437 | 332 | 311 | −21 |
| ETH 1D | 402 | 332 | 315 | −17 |
| BTC 4H | 3135 | 2470 | 2357 | −113 |
| ETH 4H | 2893 | 2246 | 2142 | −104 |

The delay requires setups to survive one extra bar; those that expire/invalidate in
that bar are dropped, reducing opportunity by ~5–10% (3135→2470 is baseline trades;
the 3135 delayed include blocked-after-confirmation). On 4H the removed opportunity
was not converted into better-quality surviving trades.

## 15. Verdict

**UNSUPPORTED (on the primary 4H claim); MIXED-to-worse elsewhere.**

Reasoning, conservatively:

- The hypothesis states the delay should reduce poor entries / adverse excursion
  **especially on 4H**. On 4H it does **not**:
  - **ETH 4H** forward expectancy is flat (0.0118 → 0.0118), PF essentially flat,
    MAE effectively unchanged, MAE/MFE flat. Win rate rises but MFE does not — the
    win-rate gain is cosmetic, not from improved entries.
  - **BTC 4H** deteriorates (expectancy and PF fall, MAE/MFE and max DD worsen).
- MAE is not reduced on 4H relative to MFE; on BTC 4H the delay cut MFE far more
  than MAE, the opposite of the intended effect.
- The delay does improve **BTC 1D** (expectancy +0.0029 to +0.0112, PF→1.66, better
  MAE/MFE and drawdown) and modestly helps the still-negative ETH 1D, but this
  does not rescue the 4H-specific claim.
- Wins impropriately counted: per the pre-registration, an increased win rate with
  no expectancy/PF improvement is **not** success — that is exactly the 4H pattern.

Overall the one-bar-confirmation change is **not** supported as a robustness
improvement. It leaves 4H at best flat (ETH) and worse (BTC) on forward expectancy.

---

**Explicit scoping:** E3 tests whether entry confirmation/timing improves robustness.
It does **not** prove that early entry is the causal explanation for prior losses.
No causal claim is drawn.

*Frozen baseline untouched. E3 ran exactly as defined (`e3-entry-timing-v1`).*
*Measurements only; not a profitability claim.*