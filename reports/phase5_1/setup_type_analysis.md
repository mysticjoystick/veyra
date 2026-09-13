# Veyra Phase 5.1 — Setup Type Analysis

Frozen baseline, all four datasets pooled. Detected = setups seen by the engine; qualified = pass filters; triggered = produced a completed trade.

## Setup funnel

| setup type         | detected | qualified | triggered | → trades | conversion% |
| ------------------ | -------- | --------- | --------- | -------- | ----------- |
| TREND_CONTINUATION | 1868     | 1868      | 1541      | 1541     | 82.5%       |
| PULLBACK           | 1812     | 1812      | 1621      | 1621     | 89.5%       |
| BREAKOUT           | 2228     | 2228      | 2082      | 2082     | 93.4%       |
| BREAKOUT_RETEST    | 1805     | 1805      | 1623      | 1623     | 89.9%       |
| RANGE_REJECTION    | 0        | 0         | 0         | 0        | 0.0%        |

## Performance by setup type (completed trades, net)

| slice              | trades | setups* | wins | losses | expir. | avg ret | expectancy | PF   | win rate | hold(bars) |
| ------------------ | ------ | ------- | ---- | ------ | ------ | ------- | ---------- | ---- | -------- | ---------- |
| TREND_CONTINUATION | 1541   | 1868    | 731  | 810    | 802    | -0.0003 | -0.0003    | 0.98 | 0.474    | 5.0        |
| PULLBACK           | 1621   | 1812    | 809  | 812    | 912    | 0.0012  | 0.0012     | 1.09 | 0.499    | 6.0        |
| BREAKOUT           | 2082   | 2228    | 858  | 1224   | 1228   | 0.0052  | 0.0052     | 1.28 | 0.412    | 8.0        |
| BREAKOUT_RETEST    | 1623   | 1805    | 591  | 1032   | 497    | 0.0036  | 0.0036     | 1.33 | 0.364    | 5.5        |
| RANGE_REJECTION    | 0      | 0       | 0    | 0      | 0      | 0.0000  | —          | —    | 0.000    | —          |

**Note:** RANGE_REJECTION never produced trades across these datasets — see diagnosis.

## Setup type × asset

| setup              | BTC             | ETH            |
| ------------------ | --------------- | -------------- |
| TREND_CONTINUATION | -0.0020 (n=762) | 0.0014 (n=779) |
| PULLBACK           | -0.0007 (n=833) | 0.0032 (n=788) |
| BREAKOUT           | 0.0087 (n=1097) | 0.0012 (n=985) |
| BREAKOUT_RETEST    | 0.0030 (n=880)  | 0.0044 (n=743) |
| RANGE_REJECTION    | 0.0000 (n=0)    | 0.0000 (n=0)   |
## Setup type × timeframe

| setup              | 4H               | 1D             |
| ------------------ | ---------------- | -------------- |
| TREND_CONTINUATION | -0.0009 (n=1345) | 0.0042 (n=196) |
| PULLBACK           | -0.0010 (n=1419) | 0.0168 (n=202) |
| BREAKOUT           | 0.0044 (n=1751)  | 0.0094 (n=331) |
| BREAKOUT_RETEST    | 0.0029 (n=1513)  | 0.0139 (n=110) |
| RANGE_REJECTION    | 0.0000 (n=0)     | 0.0000 (n=0)   |
## Setup type × regime

| setup              | BULL             | BEAR            |
| ------------------ | ---------------- | --------------- |
| TREND_CONTINUATION | -0.0003 (n=1541) | 0.0000 (n=0)    |
| PULLBACK           | 0.0012 (n=1621)  | 0.0000 (n=0)    |
| BREAKOUT           | 0.0087 (n=1273)  | -0.0004 (n=809) |
| BREAKOUT_RETEST    | 0.0021 (n=797)   | 0.0052 (n=826)  |
| RANGE_REJECTION    | 0.0000 (n=0)     | 0.0000 (n=0)    |