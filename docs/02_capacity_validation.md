# Phase 2 — capacity from operating data, checked against the field tests

Written by `validation/make_capacity_doc.py` from `validation/results/`. No number on this page is typed by hand.

besslib estimates the usable capacity of a home storage system from its daily operation, with a 90 % band, and
compares it with the field capacity tests measured in phase 1 ([`01_reference.md`](01_reference.md)). The settings were
tuned on the 7 development systems, then frozen; the 14 held-out systems were run once with them.
The headline comes from held-out systems only.

## Result

- **Configuration:** `b3959941c19b`, frozen on the development systems 01, 04, 07, 11, 14, 17 and 19 before any held-out system was run.
- **Held-out systems with results:** 14 of 14.
- **Checks passed:** V2, V5 LMO/NMC, V5 LFP, V7, V8. **Failed:** V3, V4, V5 NMC, V6.
  - V3 failed: median absolute error infinite (5 of 10 tests without an estimate) (rule <= 5 points, bias shown); median error +4.48 points; 5 of the 5 estimates read above the test.
  - V4 failed: the band held 8 of 28 tests (rule >= 80 %).
  - V5 NMC failed: held-out mean -1.21 points per year against the paper's -1.9 (rule within +-0.5).
  - V6 failed: band held the true usable fade (-1.88) in 76.0% (rule 85-95 %).
- **Held-out field tests compared:** 38 of 38; with an estimate: 27.
- **Held-out LMO/NMC and NMC:** median absolute error 2.99 points of nominal capacity; 90 % band held the test in 8 of 28 (29%).
- **Held-out LFP:** median absolute error infinite (5 of 10 tests without an estimate); 90 % band held the test in 1 of 10 (10%).
- A test without an estimate counts as an infinite error and as not held.

| Tests | Compared | With an estimate | Median abs. error, points | Median error, points | Band held the test | Median band width, points |
|---|---|---|---|---|---|---|
| Held-out LMO/NMC and NMC | 28 of 28 | 22 | 2.99 | -1.18 | 8 of 28 (29%) | 1.30 |
| Held-out LFP | 10 of 10 | 5 | infinite (5 of 10 tests without an estimate) | +4.48 | 1 of 10 (10%) | 3.00 |
| Held-out, all | 38 of 38 | 27 | 4.34 | -0.18 | 9 of 38 (24%) | 1.47 |

![Every test against its estimate](img/02_errors.png)

## Checks V2–V8

| Check | Scope | Value | Pass rule | Result |
|---|---|---|---|---|
| V2 | held-out LMO/NMC and NMC | median |error| 2.99 points (22/28 estimated) | <= 3 points | pass |
| V2 info | same, without 03/3 and 08/1 | median |error| 3.25 points (26 tests) | information |  |
| V3 | held-out LFP | median |error| inf points; median error +4.48 (5/10 estimated) | <= 5 points, bias shown | **fail** |
| V4 | held-out LMO/NMC and NMC | band held the test in 29% of 28 | >= 80 % | **fail** |
| V4 info | held-out LFP | band held the test in 10% of 10 | information |  |
| V4 info | same as V4, without 03/3 and 08/1 | band held the test in 27% of 26 | information |  |
| V5 | held-out LMO/NMC (4 systems) | mean fade -3.49 points/year; paper -3.1 | within +-0.5 | pass |
| V5 info | all LMO/NMC (6 systems) | mean fade -3.47 points/year | information |  |
| V5 | held-out NMC (4 systems) | mean fade -1.21 points/year; paper -1.9 | within +-0.5 | **fail** |
| V5 info | all NMC (6 systems) | mean fade -1.17 points/year | information |  |
| V5 | held-out LFP (5 systems) | mean fade -1.84 points/year; paper -2.0 | within +-0.5 | pass |
| V5 info | all LFP (7 systems) | mean fade -1.07 points/year | information |  |
| V6 | 200 synthetic packs | band held the true usable fade (-1.88) in 76.0% | 85-95 % | **fail** |
| V7 | core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2) | max relative difference 2.0e-14 | <= 1e-6 | pass |
| V8 | 21 systems | one command per system: python validation/run_capacity.py <id> | all 21 rebuilt with one hash | pass |

"inf" in V3: 5 of 10 tests have no estimate (half of the tests). A missing estimate counts as an infinite error, so the median absolute error is infinite.

Rules fixed before the held-out run:

- A test without an estimate counts as an infinite error and as not held.
- All 28 held-out LMO/NMC and NMC tests count in V2 and V4.
- Tests 03/3 (3.2 % of its capacity) and 08/1 (5.7 % of its capacity) had charge discharged in the hour before the test began (phase 1). They count, are flagged
  in the tables, and get one information line without them.
- Fade (V5) is judged per chemistry on the held-out systems against the paper's algorithm means;
  the mean over all 21 systems is an information line.
- A check without results is written as `not run`, never as pass or fail.

## Why a check failed

### V3: held-out LFP

Value: median |error| inf points; median error +4.48 (5/10 estimated). Pass rule: <= 5 points, bias shown.

| Test | Began | Test, % of nominal | Test C-rate | Estimate [90 % band] | Error, points | Held | Status |
|---|---|---|---|---|---|---|---|
| 15/1 | 2017-06-05 | 86.2 | 0.18 | – | – | no | not possible: no levels yet |
| 15/2 | 2018-03-05 | 89.7 | 0.39 | – | – | no | not possible: too few full-empty pairs |
| 15/3 | 2019-10-12 | 86.6 | 0.43 | – | – | no | not possible: too few full-empty pairs |
| 15/4 | 2022-11-13 | 80.6 | 0.43 | 85.1 [84.3 – 85.8] | +4.48 | no | estimated |
| 20/1 | 2017-06-17 | 95.8 | 0.41 | – | – | no | not possible: no levels yet |
| 20/2 | 2018-03-18 | 96.2 | 0.41 | 97.8 [93.1 – 102.4] | +1.58 | yes | estimated |
| 20/3 | 2019-09-08 | 85.5 | 0.41 | 92.2 [90.7 – 93.7] | +6.62 | no | estimated |
| 21/1 | 2017-05-31 | 96.5 | 0.30 | 99.1 [98.3 – 99.8] | +2.57 | no | estimated |
| 21/2 | 2018-02-19 | 95.8 | 0.30 | 103.1 [100.8 – 105.4] | +7.35 | no | estimated |
| 21/3 | 2019-07-04 | 78.4 | 0.30 | – | – | no | not possible: too few full-empty pairs |

- Tests without an estimate: 5 of 10; by reason: not possible: too few full-empty pairs 3; not possible: no levels yet 2.
- Over the 5 tests with an estimate: median absolute error 4.48 points, median error +4.48 points; 5 of 5 read above the test.
- The field tests themselves, consecutive pairs of the same system with the largest drops: 21/2 to 21/3 -17.3 points (Jul 2019); 20/2 to 20/3 -10.6 points (Sep 2019).
- System 21: its end-of-discharge level (quarterly median) is 3.141 V per cell in 2018Q1 and 3.178 in 2019Q3, the quarters of 21/2 and 21/3: +38 mV per cell between the two tests.
- System 20: its end-of-discharge level (quarterly median) is 3.126 V per cell in 2018Q1 and 3.191 in 2019Q3, the quarters of 20/2 and 20/3: +65 mV per cell between the two tests.

The numbers show two parts: 5 of 10 tests have no estimate, and 5 of the 5 estimates read above their test. Among the possible reasons for the high readings — the estimate's lag behind a falling capacity, a change of the end-of-discharge level, the difference between a maximum-power test and daily operation — the share of each is not separated.

### V4: held-out LMO/NMC and NMC

Value: band held the test in 29% of 28. Pass rule: >= 80 %.

- Tests without an estimate: 6 of 28; they count as not held. Over the 22 tests with an estimate the band held 8 (36%).
- Misses: 14; the estimate was above the test in 5 and below it in 9.
- Median half-width of the band: 0.65 points. Median absolute error of the estimated tests: 1.54 points.
- Lag: at the median held-out fade of these systems (3.27 points per year) the estimate trails a steady fade by about 1.61 points (180 days of memory).
- Field tests of the same system, consecutive pairs (these 8 systems): 20; the later test reads higher in 3 (median rise +2.2, largest +3.8 points). Over all systems: 7 of 42 (median rise +2.2, largest +7.5 points).

A battery does not gain capacity, so where a later test reads higher by d, at least one of the two tests is off the
capacity by d/2 or more, and a band whose half-width is below d/2 cannot hold both.
The band is built from the scatter of the pairs only. It holds none of: the lag behind a falling capacity, the
difference between a maximum-power test and daily operation, the test's own repeatability.

The numbers show that 6 of 28 tests had no estimate and that the median band half-width is smaller than the median error. How much of the misses comes from the lag, from the test conditions and from the tests' own repeatability is not separated.

### V5 NMC: held-out NMC (4 systems)

Value: mean fade -1.21 points/year; paper -1.9. Pass rule: within +-0.5.

| System | Rate, points/year | 90 % band | Pairs full-empty / same-kind | Span, years | Reduced chi-square | Mean of the others |
|---|---|---|---|---|---|---|
| 08 | -2.10 | -2.30 to -1.91 | 173 / 541 | 5.7 | 0.19 | -0.91 |
| 09 | – | – to – | 85 / 356 | 6.1 | – | -1.21 |
| 10 | +0.13 | -1.69 to +1.94 | 108 / 320 | 5.0 | 2.81 | -1.65 |
| 12 | -0.93 | -1.42 to -0.44 | 79 / 507 | 1.7 | 0.43 | -1.30 |
| 13 | -1.92 | -1.97 to -1.87 | 499 / 387 | 2.3 | 0.02 | -0.97 |

- Mean of the 4 held-out NMC rates: -1.21 points per year; paper -1.9; difference +0.69 (rule: within ±0.5).
- Bands that include zero: 10.
- Without system 10, the one that moves the mean most: -1.65 points per year, difference +0.25; the check would pass without it.

The held-out NMC rates range over 2.23 points per year across 4 systems; the mean of so few is moved by any one of them, as the last column shows. Whether the systems age differently from the paper's fleet mean or the estimator reads them differently is not separated.

### V6: 200 synthetic packs

Value: band held the true usable fade (-1.88) in 76.0%. Pass rule: 85-95 %.



## Development systems (tuning, not a result)

These are the systems the settings were tuned on. They are not a result and are never mixed into one.

| Tests | Compared | With an estimate | Median abs. error, points | Median error, points | Band held the test | Median band width, points |
|---|---|---|---|---|---|---|
| Development LMO/NMC and NMC | 13 of 13 | 10 | 2.61 | +0.67 | 3 of 13 (23%) | 1.85 |
| Development LFP | 9 of 9 | 8 | 0.99 | +0.60 | 5 of 9 (56%) | 2.12 |
| Development, all | 22 of 22 | 18 | 1.89 | +0.60 | 8 of 22 (36%) | 1.85 |

The tuning run chose configuration `109e9eb1efa5` (not the same as the frozen `b3959941c19b`): on its 22 development tests a median absolute error of 2.35 points, 18 with an estimate, band coverage 59%. **The frozen run does not give the same numbers.**

## Every system over time

Each panel is one system: the monthly estimate with its band, the field tests, and the estimate each test was
compared with (drawn just before the test). Gaps in the band are months without an estimate; nothing is
interpolated. The strip at the bottom marks every month with data.

![LMO/NMC systems](img/02_systems_lmo_nmc.png)

![NMC systems](img/02_systems_nmc.png)

![LFP systems](img/02_systems_lfp.png)

## Every test

The estimate is the one at the start of the test's masked window, from operating data before it only.
Error = estimate minus test, in points of nominal capacity.

| Test | Set | Chemistry | Began | Test, Ah | Test, % of nominal | Test C-rate | Estimate, % of nominal [90 % band] | Error, points | Held | Status | Flag |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01/1 | development | LMO/NMC | 2017-06-14 | 13.65 | 91.0 | 0.84 | 89.0 [88.3 – 89.8] | -1.97 | no | estimated |  |
| 01/2 | development | LMO/NMC | 2018-03-06 | 13.54 | 90.2 | 0.84 | 88.6 [88.1 – 89.1] | -1.65 | no | estimated |  |
| 01/3 | development | LMO/NMC | 2019-07-01 | 12.96 | 86.4 | 0.84 | 87.5 [85.7 – 89.3] | +1.11 | yes | estimated |  |
| 01/4 | development | LMO/NMC | 2022-10-30 | 12.36 | 82.4 | 0.30 | 77.9 [77.4 – 78.4] | -4.47 | no | estimated |  |
| 02/1 | held-out | LMO/NMC | 2017-06-01 | 13.07 | 87.1 | 0.84 | 86.2 [85.7 – 86.8] | -0.88 | no | estimated |  |
| 02/2 | held-out | LMO/NMC | 2018-04-19 | 13.06 | 87.1 | 0.30 | 82.6 [82.1 – 83.0] | -4.50 | no | estimated |  |
| 02/3 | held-out | LMO/NMC | 2019-06-07 | 11.50 | 76.7 | 0.84 | 76.5 [75.9 – 77.1] | -0.18 | yes | estimated |  |
| 02/4 | held-out | LMO/NMC | 2022-04-21 | 9.81 | 65.4 | 0.84 | 66.0 [65.5 – 66.6] | +0.64 | no | estimated |  |
| 03/1 | held-out | LMO/NMC | 2017-06-08 | 13.70 | 91.3 | 0.84 | 89.7 [88.9 – 90.5] | -1.62 | no | estimated |  |
| 03/2 | held-out | LMO/NMC | 2018-03-09 | 14.03 | 93.5 | 0.30 | 90.3 [89.8 – 90.9] | -3.19 | no | estimated |  |
| 03/3 | held-out | LMO/NMC | 2019-08-29 | 12.70 | 84.7 | 0.85 | 85.6 [85.0 – 86.3] | +0.91 | no | estimated | discharged before the start |
| 04/1 | development | LMO/NMC | 2018-03-06 | 13.29 | 88.6 | 0.84 | 88.8 [87.9 – 89.8] | +0.23 | yes | estimated |  |
| 04/2 | development | LMO/NMC | 2019-05-07 | 12.44 | 82.9 | 0.84 | 84.5 [83.6 – 85.3] | +1.55 | no | estimated |  |
| 05/1 | held-out | LMO/NMC | 2017-06-13 | 13.36 | 89.0 | 0.84 | – | – | no | not possible: no levels yet |  |
| 05/2 | held-out | LMO/NMC | 2018-03-14 | 13.20 | 88.0 | 0.79 | 88.2 [87.6 – 88.9] | +0.26 | yes | estimated |  |
| 05/3 | held-out | LMO/NMC | 2019-06-04 | 12.60 | 84.0 | 0.84 | 84.0 [83.6 – 84.5] | +0.05 | yes | estimated |  |
| 05/4 | held-out | LMO/NMC | 2022-04-11 | 11.79 | 78.6 | 0.30 | – | – | no | not possible: no full-empty pair within the memory time |  |
| 06/1 | held-out | LMO/NMC | 2017-06-13 | 13.48 | 89.9 | 0.84 | – | – | no | not possible: no levels yet |  |
| 06/2 | held-out | LMO/NMC | 2018-04-19 | 12.83 | 85.5 | 0.84 | 86.3 [85.8 – 86.7] | +0.73 | no | estimated |  |
| 06/3 | held-out | LMO/NMC | 2019-06-06 | 12.85 | 85.7 | 0.84 | 76.6 [65.0 – 88.1] | -9.10 | yes | estimated |  |
| 06/4 | held-out | LMO/NMC | 2020-05-20 | 12.71 | 84.7 | 0.84 | 82.0 [81.4 – 82.6] | -2.69 | no | estimated |  |
| 06/5 | held-out | LMO/NMC | 2022-10-27 | 10.82 | 72.1 | 0.84 | 73.6 [73.2 – 74.0] | +1.46 | no | estimated |  |
| 07/1 | development | NMC | 2017-06-14 | 158.07 | 87.8 | 0.33 | 83.3 [79.5 – 87.2] | -4.47 | no | estimated |  |
| 07/2 | development | NMC | 2018-02-23 | 149.41 | 83.0 | 0.34 | 85.6 [84.7 – 86.5] | +2.61 | no | estimated |  |
| 07/3 | development | NMC | 2019-08-15 | 148.04 | 82.2 | 0.33 | 84.0 [82.5 – 85.5] | +1.76 | no | estimated |  |
| 07/4 | development | NMC | 2020-04-29 | 143.79 | 79.9 | 0.33 | 83.3 [79.0 – 87.5] | +3.38 | yes | estimated |  |
| 07/5 | development | NMC | 2021-10-09 | 135.93 | 75.5 | 0.33 | – | – | no | not possible: too few full-empty pairs |  |
| 07/6 | development | NMC | 2022-04-11 | 132.89 | 73.8 | 0.33 | – | – | no | not possible: too few full-empty pairs |  |
| 08/1 | held-out | NMC | 2017-05-29 | 174.88 | 93.0 | 0.32 | 94.1 [92.4 – 95.7] | +1.04 | yes | estimated | discharged before the start |
| 08/2 | held-out | NMC | 2018-02-21 | 182.04 | 96.8 | 0.32 | 90.7 [80.3 – 101.2] | -6.09 | yes | estimated |  |
| 08/3 | held-out | NMC | 2019-06-17 | 176.17 | 93.7 | 0.32 | 90.4 [87.7 – 93.0] | -3.31 | no | estimated |  |
| 08/4 | held-out | NMC | 2022-04-14 | 156.79 | 83.4 | 0.32 | – | – | no | not possible: too few full-empty pairs |  |
| 09/1 | held-out | NMC | 2017-06-19 | 155.10 | 86.2 | 0.33 | 83.4 [80.9 – 85.9] | -2.79 | no | estimated |  |
| 09/2 | held-out | NMC | 2018-04-17 | 153.16 | 85.1 | 0.33 | 83.7 [82.6 – 84.8] | -1.40 | no | estimated |  |
| 10/1 | held-out | NMC | 2017-06-14 | 188.64 | 100.3 | 0.32 | – | – | no | not possible: no levels yet |  |
| 10/2 | held-out | NMC | 2018-04-16 | 182.71 | 97.2 | 0.32 | 96.2 [95.2 – 97.3] | -0.95 | yes | estimated |  |
| 10/3 | held-out | NMC | 2019-08-30 | 176.84 | 94.1 | 0.32 | 89.5 [81.9 – 97.2] | -4.55 | yes | estimated |  |
| 10/4 | held-out | NMC | 2022-04-19 | 170.91 | 90.9 | 0.32 | 78.8 [78.2 – 79.4] | -12.08 | no | estimated |  |
| 11/1 | development | NMC | 2019-05-24 | 172.84 | 100.7 | 0.31 | – | – | no | not possible: no levels yet |  |
| 12/1 | held-out | NMC | 2019-05-23 | 230.97 | 100.9 | 0.23 | – | – | no | not possible: no levels yet |  |
| 12/2 | held-out | NMC | 2022-10-11 | 222.92 | 97.4 | 0.16 | 101.6 [100.9 – 102.4] | +4.21 | no | estimated |  |
| 14/1 | development | LFP | 2017-06-02 | 145.70 | 92.2 | 0.41 | 94.0 [93.4 – 94.7] | +1.82 | no | estimated |  |
| 14/2 | development | LFP | 2018-02-19 | 144.84 | 91.7 | 0.41 | 91.9 [91.0 – 92.8] | +0.24 | yes | estimated |  |
| 14/3 | development | LFP | 2019-05-09 | 141.17 | 89.4 | 0.24 | 90.3 [87.3 – 93.3] | +0.99 | yes | estimated |  |
| 15/1 | held-out | LFP | 2017-06-05 | 168.10 | 86.2 | 0.18 | – | – | no | not possible: no levels yet |  |
| 15/2 | held-out | LFP | 2018-03-05 | 174.82 | 89.7 | 0.39 | – | – | no | not possible: too few full-empty pairs |  |
| 15/3 | held-out | LFP | 2019-10-12 | 168.82 | 86.6 | 0.43 | – | – | no | not possible: too few full-empty pairs |  |
| 15/4 | held-out | LFP | 2022-11-13 | 157.12 | 80.6 | 0.43 | 85.1 [84.3 – 85.8] | +4.48 | no | estimated |  |
| 17/1 | development | LFP | 2019-05-03 | 146.01 | 92.4 | 0.37 | – | – | no | not possible: no levels yet |  |
| 17/2 | development | LFP | 2020-06-27 | 144.61 | 91.5 | 0.42 | 92.0 [91.3 – 92.8] | +0.50 | yes | estimated |  |
| 17/3 | development | LFP | 2021-09-25 | 140.87 | 89.2 | 0.42 | 89.9 [89.4 – 90.4] | +0.70 | no | estimated |  |
| 17/4 | development | LFP | 2022-03-13 | 152.75 | 96.7 | 0.42 | 96.4 [95.1 – 97.6] | -0.33 | yes | estimated |  |
| 19/1 | development | LFP | 2018-02-20 | 186.61 | 93.3 | 0.41 | 96.3 [88.4 – 104.2] | +3.00 | yes | estimated |  |
| 19/2 | development | LFP | 2019-05-09 | 187.89 | 93.9 | 0.41 | 64.0 [40.8 – 87.2] | -29.98 | no | estimated |  |
| 20/1 | held-out | LFP | 2017-06-17 | 191.68 | 95.8 | 0.41 | – | – | no | not possible: no levels yet |  |
| 20/2 | held-out | LFP | 2018-03-18 | 192.36 | 96.2 | 0.41 | 97.8 [93.1 – 102.4] | +1.58 | yes | estimated |  |
| 20/3 | held-out | LFP | 2019-09-08 | 171.08 | 85.5 | 0.41 | 92.2 [90.7 – 93.7] | +6.62 | no | estimated |  |
| 21/1 | held-out | LFP | 2017-05-31 | 289.52 | 96.5 | 0.30 | 99.1 [98.3 – 99.8] | +2.57 | no | estimated |  |
| 21/2 | held-out | LFP | 2018-02-19 | 287.33 | 95.8 | 0.30 | 103.1 [100.8 – 105.4] | +7.35 | no | estimated |  |
| 21/3 | held-out | LFP | 2019-07-04 | 235.34 | 78.4 | 0.30 | – | – | no | not possible: too few full-empty pairs |  |

### Bias per chemistry

| Chemistry | Set | Tests with an estimate | Median error, points | Estimate below the test | Test C-rate (min – max) | Median error, tests below / at or above the chemistry's median test C-rate |
|---|---|---|---|---|---|---|
| LMO/NMC | held-out | 13 of 16 | -0.18 | 7 of 13 | 0.30 – 0.85 | -2.69 (n = 7) / -0.06 (n = 6); split at 0.84 C |
| LMO/NMC | development | 6 of 6 | -0.71 | 3 of 6 | 0.30 – 0.84 | -3.22 (n = 2) / +0.67 (n = 4); split at 0.84 C |
| NMC | held-out | 9 of 12 | -2.79 | 7 of 9 | 0.16 – 0.33 | -0.95 (n = 5) / -3.05 (n = 4); split at 0.32 C |
| NMC | development | 4 of 7 | +2.18 | 1 of 4 | 0.31 – 0.34 | – (n = 0) / +2.18 (n = 4); split at 0.32 C |
| LFP | held-out | 5 of 10 | +4.48 | 0 of 5 | 0.18 – 0.43 | +4.96 (n = 2) / +4.48 (n = 3); split at 0.41 C |
| LFP | development | 8 of 9 | +0.60 | 2 of 8 | 0.24 – 0.42 | +1.82 (n = 3) / +0.24 (n = 5); split at 0.41 C |

No rate correction is applied: a field test discharges at the system's maximum power and often deeper than daily
operation, and the estimate measures the capacity between the system's own end of charge and end of discharge
under its normal load.

## Fade rates

![Fade rates](img/02_fade.png)

| System | Set | Chemistry | Rate, points/year [90 % band] | Bootstrap 90 % | Bootstrap ÷ band width | Per 100 EFC [90 % band] | EFC per year | Span, years | Pairs full-empty / same-kind | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 01 | development | LMO/NMC | -2.76 [-2.87, -2.65] | [-2.91, -2.55] | 1.66 | -2.23 [-2.32, -2.14] | 124 | 6.4 | 782 / 382 | estimated |
| 02 | held-out | LMO/NMC | -3.55 [-3.64, -3.45] | [-3.67, -3.44] | 1.25 | -2.10 [-2.15, -2.04] | 169 | 5.9 | 750 / 791 | estimated |
| 03 | held-out | LMO/NMC | -3.76 [-3.82, -3.70] | [-3.86, -3.63] | 1.91 | -2.15 [-2.18, -2.11] | 175 | 5.5 | 1142 / 446 | estimated |
| 04 | development | LMO/NMC | -4.13 [-4.39, -3.87] | [-4.61, -3.61] | 1.90 | -2.16 [-2.30, -2.02] | 191 | 5.5 | 850 / 678 | estimated |
| 05 | held-out | LMO/NMC | -3.39 [-3.43, -3.34] | [-3.51, -3.30] | 2.43 | -1.91 [-1.94, -1.89] | 177 | 5.0 | 743 / 519 | estimated |
| 06 | held-out | LMO/NMC | -3.27 [-3.51, -3.02] | [-3.47, -2.95] | 1.09 | -2.21 [-2.38, -2.05] | 148 | 5.3 | 992 / 378 | estimated |
| 07 | development | NMC | -0.73 [-1.26, -0.20] | [-1.33, -0.15] | 1.12 | -0.61 [-1.05, -0.17] | 119 | 6.0 | 71 / 770 | estimated |
| 08 | held-out | NMC | -2.10 [-2.30, -1.91] | [-2.35, -1.77] | 1.46 | -1.47 [-1.61, -1.33] | 143 | 5.7 | 173 / 541 | estimated |
| 09 | held-out | NMC | – [–, –] | [–, –] | – | – [–, –] | – | 6.1 | 85 / 356 | not possible: not possible from this data: the capacity is implausible at 2020-06-30 (a pair has the wrong sign) |
| 10 | held-out | NMC | +0.13 [-1.69, +1.94] | [-1.88, +2.68] | 1.26 | +0.13 [-1.75, +2.01] | 96 | 5.0 | 108 / 320 | estimated |
| 11 | development | NMC | -1.47 [-1.75, -1.20] | [-1.76, -1.15] | 1.10 | -0.62 [-0.74, -0.51] | 237 | 2.7 | 183 / 540 | estimated |
| 12 | held-out | NMC | -0.93 [-1.42, -0.44] | [-1.40, -0.65] | 0.77 | -0.70 [-1.07, -0.33] | 133 | 1.7 | 79 / 507 | estimated |
| 13 | held-out | NMC | -1.92 [-1.97, -1.87] | [-2.07, -1.80] | 2.68 | -0.97 [-0.99, -0.94] | 198 | 2.3 | 499 / 387 | estimated |
| 14 | development | LFP | -1.02 [-1.27, -0.77] | [-1.37, -0.65] | 1.44 | -0.90 [-1.13, -0.68] | 113 | 3.6 | 47 / 376 | estimated |
| 15 | held-out | LFP | -2.09 [-2.96, -1.23] | [-3.06, -1.34] | 0.99 | -2.06 [-2.91, -1.21] | 102 | 5.4 | 83 / 284 | estimated |
| 16 | held-out | LFP | -1.37 [-1.47, -1.28] | [-1.49, -1.27] | 1.15 | -0.73 [-0.78, -0.68] | 188 | 2.7 | 76 / 236 | estimated |
| 17 | development | LFP | +2.74 [+2.24, +3.24] | [+1.34, +3.62] | 2.29 | +1.84 [+1.51, +2.18] | 149 | 2.6 | 88 / 151 | estimated |
| 18 | held-out | LFP | +1.45 [-6.60, +9.49] | [-42.27, +14.29] | 3.51 | +8.77 [-39.94, +57.48] | 17 | 1.7 | 9 / 27 | estimated |
| 19 | development | LFP | – [–, –] | [–, –] | – | – [–, –] | 65 | 4.0 | 153 / 235 | not possible: not possible from this data: the capacity is implausible at 2017-02-28 (the estimate is out of range) |
| 20 | held-out | LFP | -4.79 [-5.58, -3.99] | [-5.78, -3.39] | 1.51 | -2.88 [-3.36, -2.41] | 166 | 3.1 | 48 / 278 | estimated |
| 21 | held-out | LFP | -2.40 [-3.93, -0.87] | [-4.85, -0.25] | 1.50 | -2.49 [-4.08, -0.90] | 96 | 3.9 | 59 / 225 | estimated |

EFC: equivalent full cycles, the charge moved divided by twice the nominal capacity.
The band is the core's: a Student-t interval on the lag-1 sandwich standard error of the fit over all pairs.
The month bootstrap resamples whole calendar months of pairs; it also keeps any correlation within a month
(temperature, season, operation) that the band does not model. The two are shown side by side, not merged.
Held-out systems: the bootstrap interval is wider than the band in 11 of 13 (median ratio 1.46, largest 3.51 on system 18).
Development systems: the bootstrap interval is wider than the band in 6 of 6 (median ratio 1.55, largest 2.29 on system 17).
Where the bootstrap is much wider, the band understates the uncertainty of the rate.

Means per chemistry, against the paper's algorithm means (Figgener et al. 2024, Results):

| Chemistry | Held-out mean, points/year | Paper, algorithm mean | Held-out minus paper | All systems (information) | Development only (information) |
|---|---|---|---|---|---|
| LMO/NMC | -3.49 (n = 4) | -3.1 | -0.39 | -3.47 (n = 6) | -3.45 (n = 2) |
| NMC | -1.21 (n = 4) | -1.9 | +0.69 | -1.17 (n = 6) | -1.10 (n = 2) |
| LFP | -1.84 (n = 5) | -2.0 | +0.16 | -1.07 (n = 7) | +0.86 (n = 2) |

## Offset current

besslib fits the offset current together with the capacity. The paper finds it from full-to-full and
empty-to-empty pairs: the intercept of their mean current against their voltage difference (Figgener et al. 2024,
Supplementary Fig. 13). Both are computed here from the same pairs; besslib's is the whole-record fit.

| System | Set | Chemistry | besslib, A | The paper's way, A | Difference, A | Same-kind pairs used |
|---|---|---|---|---|---|---|
| 01 | development | LMO/NMC | -0.009 | -0.015 | +0.006 | 379 |
| 02 | held-out | LMO/NMC | -0.020 | -0.023 | +0.004 | 788 |
| 03 | held-out | LMO/NMC | -0.008 | -0.012 | +0.004 | 434 |
| 04 | development | LMO/NMC | -0.021 | -0.026 | +0.006 | 672 |
| 05 | held-out | LMO/NMC | -0.009 | -0.005 | -0.004 | 508 |
| 06 | held-out | LMO/NMC | -0.003 | -0.007 | +0.004 | 372 |
| 07 | development | NMC | -0.071 | -0.051 | -0.020 | 770 |
| 08 | held-out | NMC | -0.038 | -0.027 | -0.011 | 539 |
| 10 | held-out | NMC | -0.048 | -0.041 | -0.006 | 320 |
| 11 | development | NMC | -0.432 | -0.480 | +0.048 | 539 |
| 12 | held-out | NMC | -0.530 | -0.566 | +0.036 | 507 |
| 13 | held-out | NMC | -0.339 | -0.323 | -0.016 | 387 |
| 14 | development | LFP | -0.262 | -0.266 | +0.004 | 376 |
| 15 | held-out | LFP | -0.716 | -0.719 | +0.003 | 284 |
| 16 | held-out | LFP | -0.212 | -0.218 | +0.005 | 236 |
| 17 | development | LFP | -0.269 | -0.276 | +0.008 | 151 |
| 18 | held-out | LFP | -0.138 | -0.086 | -0.052 | 27 |
| 19 | development | LFP | -0.466 | -0.399 | -0.066 | 235 |
| 20 | held-out | LFP | -0.755 | -0.732 | -0.023 | 278 |
| 21 | held-out | LFP | -0.780 | -0.661 | -0.119 | 225 |

Systems with both: 20. Largest difference: 0.119 A. Negative means the battery loses
charge the meter does not see (BMS supply, balancing, self-discharge, sensor offset).

## Synthetic check V6 and code-path check V7

![Synthetic check](img/02_synthetic.png)

- Packs: 200; with a fade rate: 200. Each pack has its own operation, current-sensor gain error and
  stop errors of its BMS; the full chain runs on each (rest detection, classification, pairs, fade rate) with the frozen settings.
- Truth: the expected fade of the usable capacity, -1.88 points per year (the simulated capacity fades by -2.0; the BMS stops at voltage limits, so the usable part fades a little slower).
- Mean estimate: -1.89 points per year.
- The band held the truth in 152 of 200 packs (76.0%); each pack's own usable fade in 23.0%; the capacity fade in 0.0%.
- Offset current: median absolute error 0.0001 A (true value -0.050 A).
- Bootstrap ÷ band width: median 0.96.
- **V6** (85-95 %): **fail**.

**V7, the code path.** Core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2): max relative difference 2.0e-14 (rule <= 1e-6): **pass**.
The step-by-step core, one update per pair as a BMS or the C port runs it, gives the same estimate and band as
the batch path that builds the same sums in one step.

## How it works

Rest-anchored coulomb counting, after Figgener et al. (2024, Supplementary Methods), with besslib's own estimator.

1. **Rests.** A rest is a stretch where the current stays below C/200 for at least 20 minutes (excursions up to 10 s tolerated), after at least 0.2 × C of charge has moved.
2. **Full and empty.** The *edge voltage* of a rest is the median voltage in the 60 s before it, while
   current still flows: under load the voltage rises or falls steeply at both ends of the charge window, also for LFP.
   The system's end-of-charge and end-of-discharge levels are the 0.9 and 0.1 quantiles
   of its edge voltages over the trailing 365 days, so every season is in the window. A rest is full
   (empty) when its edge voltage lies within 3% of the level span of the end-of-charge (end-of-discharge) level.
   No level exists, and no rest is full or empty, before 365 days of history.
3. **Pairs.** Two consecutive full or empty rests form a pair when they are at most 10 days apart with at
   most 600 s of missing data between them. Kinds: full-to-empty, empty-to-full (x = ∓1), full-to-full and
   empty-to-empty (x = 0).
4. **Model.** The charge measured over a pair is `q = C · x − I_offset · Δt`: C the usable capacity, I_offset the current
   the meter does not see (BMS supply, balancing, self-discharge, sensor offset), Δt the time between the rests.
   Same-kind pairs fix the offset, full-to-empty pairs the capacity. Both are fitted together, by weighted least squares.
5. **Forgetting.** A pair of age s counts with weight exp(−s / 180 days).
6. **Band.** 90 %, from the scatter of the pairs themselves: a lag-1 sandwich covariance of the residuals (consecutive
   pairs share a rest, so their errors are correlated at lag 1) and a Student-t quantile. The current sensor's gain
   error (0.5%, Figgener et al. 2024, Supplementary Table 2) is added as a systematic term.
7. **Causal.** The estimate at a date uses only pairs that end before it, as a BMS would.
8. **Tests masked.** From 1 day before each field test begins to 1 day after it ends, the
   rests are removed and no pair spans the window. The estimate compared with a test is the one at the start of
   its masked window.
9. **Fade rate.** The same fit with `C = c0 + r · years`, no forgetting, over the whole record; it needs full-to-empty
   pairs spanning at least 1 year.

When a step is not possible, the output says `not possible from this data` with the reason, and gives no value.

Settings (`CapacityConfig()`, hash `b3959941c19b`):

| Setting | Value | Meaning |
|---|---|---|
| `current_c_rate` | 0.005 | rest: current below this share of the nominal capacity (x C) |
| `min_duration_min` | 20 | rest: shortest duration, min |
| `peak_s` | 10 | rest: longest current excursion tolerated inside it, s |
| `min_throughput` | 0.2 | rest: charge moved since the last accepted rest, x C |
| `pre_window_s` | 60 | edge voltage: median over this time before the rest, s |
| `break_gap_s` | 60 | a data step longer than this adds no charge and ends a rest, s |
| `edge_quantile` | 0.9 | end-of-charge level: this quantile of the charge-side edge voltages (end-of-discharge: 1 minus it) |
| `edge_tolerance` | 0.03 | full (empty): edge voltage within this share of the level span of its level |
| `level_window_days` | 365 | levels: trailing window, days |
| `level_warmup_days` | 365 | levels: none before this much history, days |
| `min_level_rests` | 10 | levels: fewest rests of each direction in the window |
| `ocv_fit_max_min` | 120 | open-circuit fit length, min (reported, not used for capacity) |
| `max_link_days` | 10 | pairs: longest time between the two rests, days |
| `max_missing_s` | 600 | pairs: most missing data between the two rests, s |
| `tau_days` | 180 | memory time of the forgetting, days |
| `level` | 0.9 | band level |
| `sigma_q_rel` | 0.005 | relative weight: charge error of a pair, x charge moved |
| `sigma_soc_edge` | 0.02 | relative weight: state-of-charge error of one rest |
| `gain_error` | 0.005 | current sensor gain error, added to the capacity band |
| `min_cross` | 3 | fewest full-to-empty or empty-to-full pairs in memory (decayed count) |
| `min_same` | 2 | fewest full-to-full or empty-to-empty pairs in memory (decayed count) |
| `fade_min_span_years` | 1 | fade rate: shortest span of full-to-empty pairs, years |
| `voltage_terms` | every_pair |  |
| `local_trend` | True |  |
| `band_forgetting` | 2 |  |
| `plausibility` | True |  |
| `trend_min_cross` | 0 |  |
| `gain_rectangular` | True |  |

## How the settings were fixed

All settings were fixed on the development systems, by rules written before any estimate was compared with a test.

- **Grid search** (27 settings: current threshold C/50, C/100, C/200; minimum rest duration, min 10, 20, 40; memory time, days 30, 90, 180). Rule: the lowest median
  absolute error among the settings whose 90 % band holds at least 80 % of the development tests;
  if none does, the highest coverage. **No setting reached it**: the highest development coverage was 59%. The rule's fallback, the highest coverage, chose C/200, 20 min, 180 days (coverage 59%, median absolute error 2.35 points). So before the held-out run the 90 % band was already known to hold only 59% of the development tests.
- **Edge tolerance** 3% of the level span (`edge_tolerance.py`): the widest tolerance at which the
  median full-to-empty charge falls by at most 2% in every bin, each bin with at least 10 pairs;
  from pair charges only, no test value. It is the value in `CapacityConfig()`.
- **Level window** (`level_window.py`, no test value read): a home storage system is rarely emptied in summer and
  rarely filled in winter, so a short window lets the levels follow the season. One year was kept:

| Level window (warm-up) | Seasonal amplitude of the estimate, points (median of systems) | Full-empty pairs below 70% of the median charge (median of systems) | Month ends estimated |
|---|---|---|---|
| 90 d, no warm-up | 0.88 | 0.75 % | 418 of 482 |
| 90 d | 1.09 | 0.82 % | 399 of 482 |
| 365 d | 0.56 | 0.00 % | 308 of 482 |

## Limits

**No estimate in the first year.** The full and empty levels need 365 days of history, so the first year of every record gives none (`not possible from this data: not enough history yet to set the full and empty levels`). Over the 21 systems with results, 791 of 1269 months with data have an estimate; 248 lie before the first pair.
Field tests in that first year: 8 (05/1, 06/1, 10/1, 11/1, 12/1, 15/1, 17/1, 20/1).

| System | Set | Months with data | Estimated | Before the first pair | too few full-empty pairs | too few same-kind pairs | no full-empty pair within the memory time | no positive variance for the band | After the last pair | Estimated month ends without data that month | Median band width, points |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | development | 80 | 65 (81%) | 12 | 0 | 0 | 1 | 0 | 2 | 4 | 1.1 |
| 02 | held-out | 76 | 63 (83%) | 13 | 0 | 0 | 0 | 0 | 0 | 6 | 0.9 |
| 03 | held-out | 71 | 58 (82%) | 12 | 0 | 0 | 0 | 0 | 1 | 6 | 1.0 |
| 04 | development | 69 | 55 (80%) | 12 | 0 | 1 | 0 | 1 | 0 | 7 | 1.2 |
| 05 | held-out | 57 | 42 (74%) | 12 | 1 | 0 | 1 | 0 | 1 | 5 | 0.9 |
| 06 | held-out | 66 | 51 (77%) | 12 | 0 | 0 | 2 | 0 | 1 | 7 | 1.0 |
| 07 | development | 76 | 44 (58%) | 12 | 17 | 0 | 1 | 1 | 1 | 2 | 4.2 |
| 08 | held-out | 72 | 47 (65%) | 13 | 8 | 0 | 3 | 0 | 1 | 2 | 1.7 |
| 09 | held-out | 70 | 33 (47%) | 10 | 9 | 0 | 2 | 0 | 1 | 3 | 2.2 |
| 10 | held-out | 66 | 43 (65%) | 12 | 8 | 0 | 2 | 0 | 1 | 3 | 2.3 |
| 11 | development | 47 | 21 (45%) | 12 | 11 | 0 | 0 | 2 | 1 | 0 | 1.5 |
| 12 | held-out | 43 | 22 (51%) | 12 | 9 | 0 | 0 | 0 | 0 | 1 | 2.1 |
| 13 | held-out | 42 | 28 (67%) | 12 | 1 | 0 | 0 | 0 | 1 | 0 | 0.7 |
| 14 | development | 60 | 30 (50%) | 9 | 20 | 0 | 0 | 0 | 1 | 0 | 1.8 |
| 15 | held-out | 66 | 32 (48%) | 11 | 22 | 0 | 0 | 0 | 1 | 3 | 4.8 |
| 16 | held-out | 47 | 32 (68%) | 13 | 1 | 0 | 0 | 0 | 1 | 0 | 0.9 |
| 17 | development | 46 | 30 (65%) | 13 | 1 | 0 | 0 | 0 | 2 | 0 | 1.2 |
| 18 | held-out | 34 | 5 (15%) | 11 | 17 | 0 | 0 | 0 | 1 | 0 | 45.3 |
| 19 | development | 72 | 42 (58%) | 12 | 3 | 0 | 7 | 0 | 7 | 3 | 11.5 |
| 20 | held-out | 57 | 25 (44%) | 12 | 8 | 0 | 2 | 1 | 9 | 0 | 7.8 |
| 21 | held-out | 52 | 23 (44%) | 11 | 15 | 0 | 1 | 1 | 1 | 4 | 5.7 |

Column names are short forms of the statuses: *too few full-empty pairs*: `not possible from this data: too few full-to-empty or empty-to-full pairs`; *too few same-kind pairs*: `not possible from this data: too few full-to-full or empty-to-empty pairs to fix the offset`; *no full-empty pair within the memory time*: `not possible from this data: no full-to-empty or empty-to-full pair within the memory time`; *no positive variance for the band*: `not possible from this data: the residuals give no positive variance for the band`.
"Before the first pair" months lie before the levels exist or before the first pair after them.
"Estimated month ends without data that month": the estimate carries the earlier pairs forward, decaying; its
band widens as they age, but no new measurement enters. The figures show these months as band without a rug mark.

**Tests without an estimate, by reason** (systems with results): not possible: no levels yet: 8; not possible: too few full-empty pairs: 6; not possible: no full-empty pair within the memory time: 1.
This is the designed answer: where the operation gives too few full and empty rests, the output says so and gives no value.
This version covers home storage that reaches full and empty regularly. Storage that works at a middle state of
charge, or only in partial windows, gives no full-to-empty pairs and therefore no estimate.

**The band is on the weighted capacity of the pairs behind it, not on the capacity at the date.** Old pairs fade
with the memory time of 180 days; when pairs come evenly, their weighted mean age is that memory time, so
the estimate lags a steady fade by about 180 days. At the median fade of the 13 held-out systems with a rate, 2.10 points per year, that is 1.04 points. The band does not include this lag, nor a
systematic difference between the usable capacity in daily operation and a field test at the system's maximum power.

**The paper's interval is not this band.** In Figgener et al. (2024, Fig. 4) the shaded interval contains a fixed share of all
estimates: it is the spread of the estimates. besslib's 90 % band is a band on the capacity, and V4 tests how often it
holds the field test.

**Field tests are not daily operation.** A field test discharges at the system's maximum power, often deeper than
daily operation. No rate correction is applied; the estimate is compared as it is. The bias table above shows the
median signed error per chemistry next to the test C-rates.

**Impossible values marked `estimated`.** This version has no plausibility check on its own output.
No month end of the 21 systems has an estimate at or below zero or a band below zero.

**The levels that decide full and empty**, per system, as the run builds them (the library's own functions on the
reduced rest tables, test windows masked; the rebuilt pairs equal the run's in 21 of 21 systems):

| System | Chemistry | Cells in series | End-of-charge level, V per cell (min – max) | End-of-discharge level, V per cell (min – max) | Largest quarter-to-quarter step of the end-of-discharge level, mV per cell | Rests with empty level ≥ full level | Full-empty pairs with charge of the wrong sign |
|---|---|---|---|---|---|---|---|
| 01 | LMO/NMC | 40 | 4.122 – 4.130 | 3.248 – 3.406 | -26 (2020Q3) | 0 | 0 |
| 02 | LMO/NMC | 40 | 4.134 – 4.139 | 3.249 – 3.400 | -29 (2019Q1) | 0 | 0 |
| 03 | LMO/NMC | 40 | 4.131 – 4.136 | 3.243 – 3.373 | -28 (2018Q4) | 0 | 0 |
| 04 | LMO/NMC | 40 | 4.117 – 4.126 | 3.240 – 3.394 | -33 (2018Q1) | 0 | 0 |
| 05 | LMO/NMC | 40 | 4.128 – 4.138 | 3.249 – 3.304 | -39 (2018Q2) | 0 | 0 |
| 06 | LMO/NMC | 40 | 4.126 – 4.130 | 3.248 – 3.350 | -27 (2018Q4) | 0 | 0 |
| 07 | NMC | 13 | 3.987 – 4.005 | 3.531 – 3.624 | +92 (2021Q3) | 0 | 0 |
| 08 | NMC | 13 | 3.968 – 3.994 | 3.346 – 3.405 | +40 (2022Q2) | 0 | 0 |
| 09 | NMC | 13 | 3.674 – 4.001 | 3.548 – 3.985 | +260 (2020Q3) | 832 | 12 |
| 10 | NMC | 13 | 3.975 – 3.991 | 3.347 – 3.445 | +28 (2020Q4) | 0 | 0 |
| 11 | NMC | 14 | 4.091 – 4.098 | 3.241 – 3.295 | +44 (2021Q1) | 0 | 0 |
| 12 | NMC | 14 | 4.094 – 4.099 | 3.262 – 3.315 | +31 (2021Q1) | 0 | 0 |
| 13 | NMC | 14 | 4.128 – 4.129 | 3.564 – 3.579 | -5 (2021Q1) | 0 | 0 |
| 14 | LFP | 16 | 3.524 – 3.538 | 2.969 – 3.001 | -12 (2019Q1) | 0 | 0 |
| 15 | LFP | 16 | 3.502 – 3.520 | 2.958 – 3.031 | -39 (2021Q4) | 0 | 0 |
| 16 | LFP | 16 | 3.531 – 3.544 | 3.001 – 3.023 | +10 (2020Q2) | 0 | 0 |
| 17 | LFP | 16 | 3.519 – 3.542 | 2.894 – 2.939 | +25 (2022Q4) | 0 | 0 |
| 18 | LFP | 16 | 3.498 – 3.529 | 3.071 – 3.103 | +23 (2017Q4) | 0 | 0 |
| 19 | LFP | 14 | 3.503 – 3.516 | 3.156 – 3.198 | +28 (2020Q2) | 0 | 0 |
| 20 | LFP | 14 | 3.458 – 3.501 | 3.124 – 3.202 | +41 (2019Q1) | 0 | 0 |
| 21 | LFP | 14 | 3.423 – 3.517 | 3.140 – 3.211 | -23 (2017Q4) | 0 | 0 |

**Largest held-out misses:** 10/4 -12.1 points (band 1.2 wide, not held); 06/3 -9.1 points (band 23.1 wide, held); 21/2 +7.3 points (band 4.6 wide, not held).

**Held because the band is wide:** the median band of the held-out estimates is 1.5 points; the widest bands among the held tests are 06/3 23.1 points (error -9.1); 08/2 20.9 points (error -6.1); 10/3 15.3 points (error -4.5). A test held by a wide band says little about accuracy.

**Estimates above the nominal capacity** (month ends, highest value): 12 (18 of 23, up to 105.2 %; its highest field test 101.0 %); 19 (2 of 45, up to 102.8 %; its highest field test 93.9 %); 20 (4 of 25, up to 103.7 %; its highest field test 96.2 %); 21 (9 of 27, up to 106.8 %; its highest field test 96.5 %). On 19, 20 and 21 no field test reaches the nominal capacity.

**Fade bands that include zero:** 10 (held-out, +0.13 [-1.69, +1.94]), 18 (held-out, +1.45 [-6.60, +9.49]). On these records the fade is not resolved.

**Fewest pairs:** system 18 with 36 pairs (9 full-to-empty or empty-to-full) over 1.7 years; its fade +1.45 points per year, band [-6.60, +9.49], month bootstrap [-42.3, +14.3], reduced chi-square 8.3.

**System 17: a capacity step after a data gap.** The data stop on 25 Jan 2022 and resume on 11 Feb 2022 (17 days). The monthly estimate around the gap, % of nominal: 89.4 (Jan 2022), 94.1 (Feb 2022), 97.2 (Mar 2022). The field tests move the same way: 17/3 read 89.2 %, 17/4 96.7 %. A straight line through a record with a step reads positive here: +2.74 points per year [+2.24, +3.24]. Cells do not gain capacity, so the step is in the usable capacity the system gives; the fade model has no term for a step.

**System 19: high scatter.** Its median monthly band is 11.5 points wide, against 1.6 for the median of the other systems with results. The fade fit's reduced chi-square is 8.9. Its median end-of-discharge level is 3.168 V per cell; the other LFP systems: 14 2.973, 15 2.990, 16 3.010, 17 2.907, 18 3.100, 20 3.144, 21 3.165. That this stop lies on the flat part of the LFP voltage curve, where the edge voltage says little about the state of charge, is an observation from the development work that this page does not back with a number.

**System 11: its end-of-discharge level moves.** Over the record it ranges from 3.241 to 3.295 V per cell; the largest step between two quarters is +44 mV per cell, into 2021Q1 (quarterly medians 2020Q4 3.244, 2021Q1 3.288, 2021Q2 3.281). That the system's own software made this change, and how the usable capacity responds to it, is an observation from the development work that this page does not back with a number.

## Rebuild

```
pip install -e .[dev]
python validation/run_capacity.py <id>        # one system, 1 to 21; downloads its archive if needed
python validation/check_capacity.py           # the checks V2-V8
python validation/make_capacity_doc.py        # this page and its figures
```

The 21 system archives together are 21 GB (sizes from the Zenodo record, `validation/results/zenodo_files.json`). `run_capacity.py` reduces each held-out archive to its rest candidates and
then deletes it. The tuning and the settings studies are rebuilt with `tune_capacity.py`, `edge_tolerance.py`,
`level_window.py` and `synthetic_coverage.py` (V6) in `validation/`.
Every result row carries the configuration hash; this page refuses to build from results of any hash other than `b3959941c19b`.

## Source and credit

Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in
capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.
Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9.
The values on this page are computed by besslib from that data; only the fade means marked as the paper's are taken from it.
besslib computes capacity figures; it does not certify them.
