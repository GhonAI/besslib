# Capacity from operating data, checked against the field tests

Written by `validation/make_capacity_doc.py` from `validation/results/`. No number on this page is typed by hand.

besslib estimates the capacity between two stated rest voltages of a home storage system from its daily operation, with a 90 % band, and
compares it with the field capacity tests measured again on the reference page ([`01_reference.md`](01_reference.md)). The settings were
tuned on the 7 development systems, then frozen. The 14 held-out systems were run at the settings of releases 0.1.0 and 0.2.0, then once at these ([what changed](06_version_0_3.md)).
The headline comes from held-out systems only.

## Result

- **Configuration:** `b30137b3f868`, frozen on the development systems 01, 04, 07, 11, 14, 17 and 19 and on synthetic packs before the held-out systems were run at it. They had been run before, at the settings of releases 0.1.0 (`109e9eb1efa5`) and 0.2.0 (`b3959941c19b`).
- **Held-out systems with results:** 14 of 14.
- **Checks passed:** V2, V5 LMO/NMC, V7, V8. **Failed:** V3, V4, V5 NMC, V5 LFP, V6.
  - V3 failed: median absolute error infinite (5 of 10 tests without an estimate) (rule <= 5 points, bias shown); median error +4.62 points; 5 of the 5 estimates read above the test.
  - V4 failed: the band held 9 of 28 tests (rule >= 80 %).
  - V5 NMC failed: held-out mean -1.15 points per year against the paper's -1.9 (rule within +-0.5).
  - V5 LFP failed: held-out mean -2.73 points per year against the paper's -2.0 (rule within +-0.5).
  - V6 failed: band held the true usable fade (-1.88) in 27.0% (rule 85-95 %).
- **Held-out field tests compared:** 38 of 38; with an estimate: 27.
- **Held-out LMO/NMC and NMC:** median absolute error 2.62 points of nominal capacity; 90 % band held the test in 9 of 28 (32%).
- **Held-out LFP:** median absolute error infinite (5 of 10 tests without an estimate); 90 % band held the test in 1 of 10 (10%).
- A test without an estimate counts as an infinite error and as not held.

| Tests | Compared | With an estimate | Median abs. error, points | Median error, points | Band held the test | Median band width, points |
|---|---|---|---|---|---|---|
| Held-out LMO/NMC and NMC | 28 of 28 | 22 | 2.62 | -1.02 | 9 of 28 (32%) | 1.40 |
| Held-out LFP | 10 of 10 | 5 | infinite (5 of 10 tests without an estimate) | +4.62 | 1 of 10 (10%) | 3.30 |
| Held-out, all | 38 of 38 | 27 | 4.09 | +0.28 | 10 of 38 (26%) | 1.66 |

![Every test against its estimate](img/02_errors.png)

## Checks V2–V8

| Check | Scope | Value | Pass rule | Result |
|---|---|---|---|---|
| V2 | held-out LMO/NMC and NMC | median \|error\| 2.62 points (22/28 estimated) | <= 3 points | pass |
| V2 info | same, without 03/3 and 08/1 | median \|error\| 2.98 points (26 tests) | information |  |
| V3 | held-out LFP | median \|error\| inf points; median error +4.62 (5/10 estimated) | <= 5 points, bias shown | **fail** |
| V4 | held-out LMO/NMC and NMC | band held the test in 32% of 28 | >= 80 % | **fail** |
| V4 info | held-out LFP | band held the test in 10% of 10 | information |  |
| V4 info | same as V4, without 03/3 and 08/1 | band held the test in 31% of 26 | information |  |
| V5 | held-out LMO/NMC (4 systems) | mean fade -3.44 points/year; paper -3.1; without one system -3.51 to -3.39 | within +-0.5 | pass |
| V5 info | all LMO/NMC (6 systems) | mean fade -3.36 points/year | information |  |
| V5 | held-out NMC (4 systems) | mean fade -1.15 points/year; paper -1.9; without one system -1.74 to -0.86 | within +-0.5 | **fail** |
| V5 info | all NMC (6 systems) | mean fade -1.13 points/year | information |  |
| V5 | held-out LFP (4 systems) | mean fade -2.73 points/year; paper -2.0; without one system -3.18 to -2.09 | within +-0.5 | **fail** |
| V5 info | all LFP (7 systems) | mean fade -1.48 points/year | information |  |
| V6 | 200 synthetic packs | band held the true usable fade (-1.88) in 27.0% | 85-95 % | **fail** |
| V7 | core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2) | max relative difference 2.0e-13 | <= 1e-6 | pass |
| V8 | 21 systems | one command per system: python validation/run_capacity.py &lt;id> | all 21 rebuilt with one hash | pass |

"inf" in V3: 5 of 10 tests have no estimate (half of the tests). A missing estimate counts as an infinite error, so the median absolute error is infinite.

Rules fixed before the first held-out run (release 0.1.0):

- A test without an estimate counts as an infinite error and as not held.
- All 28 held-out LMO/NMC and NMC tests count in V2 and V4.
- Tests 03/3 (3.2 % of its capacity) and 08/1 (5.7 % of its capacity) had charge discharged in the hour before the test began (reference page). They count, are flagged
  in the tables, and get one information line without them.
- Fade (V5) is judged per chemistry on the held-out systems against the paper's algorithm means;
  the mean over all 21 systems is an information line.
- A check without results is written as `not run`, never as pass or fail.

## Why a check failed

### V3: held-out LFP

Value: median |error| inf points; median error +4.62 (5/10 estimated). Pass rule: <= 5 points, bias shown.

| Test | Began | Test, % of nominal | Test C-rate | Estimate [90 % band] | Error, points | Held | Status |
|---|---|---|---|---|---|---|---|
| 15/1 | 2017-06-05 | 86.2 | 0.18 | – | – | no | not possible: no levels yet |
| 15/2 | 2018-03-05 | 89.7 | 0.39 | – | – | no | not possible: too few full-empty pairs |
| 15/3 | 2019-10-12 | 86.6 | 0.43 | – | – | no | not possible: too few full-empty pairs |
| 15/4 | 2022-11-13 | 80.6 | 0.43 | 85.2 [84.3 – 86.1] | +4.62 | no | estimated |
| 20/1 | 2017-06-17 | 95.8 | 0.41 | – | – | no | not possible: no levels yet |
| 20/2 | 2018-03-18 | 96.2 | 0.41 | 98.0 [94.2 – 101.8] | +1.83 | yes | estimated |
| 20/3 | 2019-09-08 | 85.5 | 0.41 | 93.2 [91.5 – 94.8] | +7.65 | no | estimated |
| 21/1 | 2017-05-31 | 96.5 | 0.30 | 98.9 [97.9 – 99.9] | +2.42 | no | estimated |
| 21/2 | 2018-02-19 | 95.8 | 0.30 | 103.8 [101.6 – 106.1] | +8.06 | no | estimated |
| 21/3 | 2019-07-04 | 78.4 | 0.30 | – | – | no | not possible: too few full-empty pairs |

- Tests without an estimate: 5 of 10; by reason: not possible: too few full-empty pairs 3; not possible: no levels yet 2.
- Over the 5 tests with an estimate: median absolute error 4.62 points, median error +4.62 points; 5 of 5 read above the test.
- The field tests themselves, consecutive pairs of the same system with the largest drops: 21/2 to 21/3 -17.3 points (Jul 2019); 20/2 to 20/3 -10.6 points (Sep 2019).
- System 21: its end-of-discharge level (quarterly median) is 3.141 V per cell in 2018Q1 and 3.178 in 2019Q3, the quarters of 21/2 and 21/3: +38 mV per cell between the two tests.
- System 20: its end-of-discharge level (quarterly median) is 3.126 V per cell in 2018Q1 and 3.191 in 2019Q3, the quarters of 20/2 and 20/3: +65 mV per cell between the two tests.

The numbers show two parts: 5 of 10 tests have no estimate, and 5 of the 5 estimates read above their test. Among the possible reasons for the high readings — the drift of the stops against the rest voltages, a change of the end-of-discharge level, the difference between a maximum-power test and daily operation — the share of each is not separated.

### V4: held-out LMO/NMC and NMC

Value: band held the test in 32% of 28. Pass rule: >= 80 %.

- Tests without an estimate: 6 of 28; they count as not held. Over the 22 tests with an estimate the band held 9 (41%).
- Misses: 13; the estimate was above the test in 5 and below it in 8.
- Median half-width of the band: 0.70 points. Median absolute error of the estimated tests: 1.61 points.
- Lag: the fit reads the value at the date. On synthetic packs it still reads +0.63 points above the charge between the stops (X3, [version 0.2 page](04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with)).
- Field tests of the same system, consecutive pairs (these 8 systems): 20; the later test reads higher in 3 (median rise +2.2, largest +3.8 points). Over all systems: 7 of 42 (median rise +2.2, largest +7.5 points).

A battery does not gain capacity, so where a later test reads higher by d, at least one of the two tests is off the
capacity by d/2 or more, and a band whose half-width is below d/2 cannot hold both.
The band is built from the scatter of the pairs only. It holds none of: the drift of the BMS stops against the rest
voltages it reads at, the difference between a maximum-power test and daily operation, the test's own repeatability.

The numbers show that 6 of 28 tests had no estimate and that the median band half-width is smaller than the median error. How much of the misses comes from the drift of the stops, from the test conditions and from the tests' own repeatability is not separated.

### V5 NMC: held-out NMC (4 systems)

Value: mean fade -1.15 points/year; paper -1.9; without one system -1.74 to -0.86. Pass rule: within +-0.5.

| System | Rate, points/year | 90 % band | Pairs full-empty / same-kind | Span, years | Reduced chi-square | Mean of the others |
|---|---|---|---|---|---|---|
| 08 | -2.05 | -2.24 to -1.86 | 173 / 541 | 5.7 | 0.19 | -0.86 |
| 09 | – | – to – | 85 / 356 | 6.1 | – | -1.15 |
| 10 | +0.60 | -1.69 to +2.90 | 108 / 320 | 5.0 | 2.72 | -1.74 |
| 12 | -1.26 | -1.79 to -0.73 | 79 / 507 | 1.7 | 0.43 | -1.12 |
| 13 | -1.92 | -1.97 to -1.86 | 499 / 387 | 2.3 | 0.03 | -0.90 |

- Mean of the 4 held-out NMC rates: -1.15 points per year; paper -1.9; difference +0.75 (rule: within ±0.5).
- Bands that include zero: 10.
- Without system 10, the one that moves the mean most: -1.74 points per year, difference +0.16; the check would pass without it.

The held-out NMC rates range over 2.65 points per year across 4 systems; the mean of so few is moved by any one of them, as the last column shows. Whether the systems age differently from the paper's fleet mean or the estimator reads them differently is not separated.

### V5 LFP: held-out LFP (4 systems)

Value: mean fade -2.73 points/year; paper -2.0; without one system -3.18 to -2.09. Pass rule: within +-0.5.

| System | Rate, points/year | 90 % band | Pairs full-empty / same-kind | Span, years | Reduced chi-square | Mean of the others |
|---|---|---|---|---|---|---|
| 15 | -2.11 | -2.94 to -1.28 | 83 / 284 | 5.4 | 2.42 | -2.93 |
| 16 | -1.37 | -1.49 to -1.26 | 76 / 236 | 2.7 | 0.01 | -3.18 |
| 18 | -2.80 | -15.39 to +9.78 | 9 / 27 | 1.7 | 5.16 | -2.70 |
| 20 | -4.63 | -5.39 to -3.86 | 48 / 278 | 3.1 | 4.09 | -2.09 |
| 21 | – | – to – | 59 / 225 | 3.9 | 3.08 | -2.73 |

- Mean of the 4 held-out LFP rates: -2.73 points per year; paper -2.0; difference -0.73 (rule: within ±0.5).
- Bands that include zero: 18.
- Without system 20, the one that moves the mean most: -2.09 points per year, difference -0.09; the check would pass without it.

The held-out LFP rates range over 3.25 points per year across 4 systems; the mean of so few is moved by any one of them, as the last column shows. Whether the systems age differently from the paper's fleet mean or the estimator reads them differently is not separated.

### V6: 200 synthetic packs

Value: band held the true usable fade (-1.88) in 27.0%. Pass rule: 85-95 %.

- Truth: the expected fade of the charge between the BMS stops, -1.88 points per year. Mean estimate -1.90.
- Held: 54 of 200 (27.0%). Against the fade of the capacity between the rest voltages the fit reads at (mean -1.90; chosen after the first results, information): 169 of 200 (84.5%).

The two truths differ because the fit reads the capacity at rest voltages, not at the BMS stops ([version 0.2 page](04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with)).


## Development systems (tuning, not a result)

These are the systems the settings were tuned on. They are not a result and are never mixed into one.

| Tests | Compared | With an estimate | Median abs. error, points | Median error, points | Band held the test | Median band width, points |
|---|---|---|---|---|---|---|
| Development LMO/NMC and NMC | 13 of 13 | 10 | 2.50 | +0.40 | 2 of 13 (15%) | 1.56 |
| Development LFP | 9 of 9 | 8 | 1.18 | +0.60 | 5 of 9 (56%) | 2.10 |
| Development, all | 22 of 22 | 18 | 1.83 | +0.60 | 7 of 22 (32%) | 1.63 |

The rest search and the memory time were chosen in version 0.1 (`109e9eb1efa5`) and kept. Version 0.2 tuned one setting on these 22 tests, the trend gate (`validation/tune_trend.py`; it chose `b3959941c19b`, the 0.2 configuration). Version 0.3 tuned one more, the gate of the quadratic voltage terms (`validation/tune_quadratic.py`). It chose `b30137b3f868`, the frozen configuration: median absolute error 1.83 points, 18 with an estimate, band coverage 32%. The frozen run gives the same numbers.

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
| 01/1 | development | LMO/NMC | 2017-06-14 | 13.65 | 91.0 | 0.84 | 89.1 [88.3 – 89.8] | -1.93 | no | estimated |  |
| 01/2 | development | LMO/NMC | 2018-03-06 | 13.54 | 90.2 | 0.84 | 88.9 [88.5 – 89.4] | -1.32 | no | estimated |  |
| 01/3 | development | LMO/NMC | 2019-07-01 | 12.96 | 86.4 | 0.84 | 87.6 [85.8 – 89.4] | +1.17 | yes | estimated |  |
| 01/4 | development | LMO/NMC | 2022-10-30 | 12.36 | 82.4 | 0.30 | 78.4 [77.9 – 78.8] | -3.99 | no | estimated |  |
| 02/1 | held-out | LMO/NMC | 2017-06-01 | 13.07 | 87.1 | 0.84 | 86.3 [85.7 – 86.8] | -0.84 | no | estimated |  |
| 02/2 | held-out | LMO/NMC | 2018-04-19 | 13.06 | 87.1 | 0.30 | 82.9 [82.5 – 83.4] | -4.15 | no | estimated |  |
| 02/3 | held-out | LMO/NMC | 2019-06-07 | 11.50 | 76.7 | 0.84 | 77.1 [76.4 – 77.8] | +0.43 | yes | estimated |  |
| 02/4 | held-out | LMO/NMC | 2022-04-21 | 9.81 | 65.4 | 0.84 | 66.3 [65.6 – 67.0] | +0.90 | no | estimated |  |
| 03/1 | held-out | LMO/NMC | 2017-06-08 | 13.70 | 91.3 | 0.84 | 89.9 [88.9 – 90.8] | -1.47 | no | estimated |  |
| 03/2 | held-out | LMO/NMC | 2018-03-09 | 14.03 | 93.5 | 0.30 | 90.9 [90.4 – 91.4] | -2.62 | no | estimated |  |
| 03/3 | held-out | LMO/NMC | 2019-08-29 | 12.70 | 84.7 | 0.85 | 86.1 [85.4 – 86.8] | +1.43 | no | estimated | discharged before the start |
| 04/1 | development | LMO/NMC | 2018-03-06 | 13.29 | 88.6 | 0.84 | 88.2 [87.2 – 89.2] | -0.38 | yes | estimated |  |
| 04/2 | development | LMO/NMC | 2019-05-07 | 12.44 | 82.9 | 0.84 | 84.4 [83.6 – 85.1] | +1.42 | no | estimated |  |
| 05/1 | held-out | LMO/NMC | 2017-06-13 | 13.36 | 89.0 | 0.84 | – | – | no | not possible: no levels yet |  |
| 05/2 | held-out | LMO/NMC | 2018-03-14 | 13.20 | 88.0 | 0.79 | 88.6 [87.8 – 89.4] | +0.61 | yes | estimated |  |
| 05/3 | held-out | LMO/NMC | 2019-06-04 | 12.60 | 84.0 | 0.84 | 84.3 [83.8 – 84.7] | +0.28 | yes | estimated |  |
| 05/4 | held-out | LMO/NMC | 2022-04-11 | 11.79 | 78.6 | 0.30 | – | – | no | not possible: no full-empty pair within the memory time |  |
| 06/1 | held-out | LMO/NMC | 2017-06-13 | 13.48 | 89.9 | 0.84 | – | – | no | not possible: no levels yet |  |
| 06/2 | held-out | LMO/NMC | 2018-04-19 | 12.83 | 85.5 | 0.84 | 86.5 [86.1 – 87.0] | +1.00 | no | estimated |  |
| 06/3 | held-out | LMO/NMC | 2019-06-06 | 12.85 | 85.7 | 0.84 | 76.7 [65.4 – 88.0] | -8.97 | yes | estimated |  |
| 06/4 | held-out | LMO/NMC | 2020-05-20 | 12.71 | 84.7 | 0.84 | 82.1 [81.5 – 82.6] | -2.62 | no | estimated |  |
| 06/5 | held-out | LMO/NMC | 2022-10-27 | 10.82 | 72.1 | 0.84 | 73.9 [73.5 – 74.2] | +1.75 | no | estimated |  |
| 07/1 | development | NMC | 2017-06-14 | 158.07 | 87.8 | 0.33 | 86.5 [85.8 – 87.3] | -1.31 | no | estimated |  |
| 07/2 | development | NMC | 2018-02-23 | 149.41 | 83.0 | 0.34 | 85.9 [85.3 – 86.5] | +2.89 | no | estimated |  |
| 07/3 | development | NMC | 2019-08-15 | 148.04 | 82.2 | 0.33 | 84.7 [83.5 – 86.0] | +2.50 | no | estimated |  |
| 07/4 | development | NMC | 2020-04-29 | 143.79 | 79.9 | 0.33 | 82.7 [80.1 – 85.3] | +2.80 | no | estimated |  |
| 07/5 | development | NMC | 2021-10-09 | 135.93 | 75.5 | 0.33 | – | – | no | not possible: too few full-empty pairs |  |
| 07/6 | development | NMC | 2022-04-11 | 132.89 | 73.8 | 0.33 | – | – | no | not possible: too few full-empty pairs |  |
| 08/1 | held-out | NMC | 2017-05-29 | 174.88 | 93.0 | 0.32 | 93.5 [91.1 – 95.8] | +0.45 | yes | estimated | discharged before the start |
| 08/2 | held-out | NMC | 2018-02-21 | 182.04 | 96.8 | 0.32 | 89.9 [78.3 – 101.4] | -6.96 | yes | estimated |  |
| 08/3 | held-out | NMC | 2019-06-17 | 176.17 | 93.7 | 0.32 | 90.4 [87.7 – 93.0] | -3.33 | no | estimated |  |
| 08/4 | held-out | NMC | 2022-04-14 | 156.79 | 83.4 | 0.32 | – | – | no | not possible: too few full-empty pairs |  |
| 09/1 | held-out | NMC | 2017-06-19 | 155.10 | 86.2 | 0.33 | 85.1 [82.9 – 87.2] | -1.11 | yes | estimated |  |
| 09/2 | held-out | NMC | 2018-04-17 | 153.16 | 85.1 | 0.33 | 84.2 [82.8 – 85.5] | -0.93 | yes | estimated |  |
| 10/1 | held-out | NMC | 2017-06-14 | 188.64 | 100.3 | 0.32 | – | – | no | not possible: no levels yet |  |
| 10/2 | held-out | NMC | 2018-04-16 | 182.71 | 97.2 | 0.32 | 95.2 [94.6 – 95.8] | -2.01 | no | estimated |  |
| 10/3 | held-out | NMC | 2019-08-30 | 176.84 | 94.1 | 0.32 | 88.7 [78.6 – 98.7] | -5.40 | yes | estimated |  |
| 10/4 | held-out | NMC | 2022-04-19 | 170.91 | 90.9 | 0.32 | 78.6 [78.1 – 79.2] | -12.27 | no | estimated |  |
| 11/1 | development | NMC | 2019-05-24 | 172.84 | 100.7 | 0.31 | – | – | no | not possible: no levels yet |  |
| 12/1 | held-out | NMC | 2019-05-23 | 230.97 | 100.9 | 0.23 | – | – | no | not possible: no levels yet |  |
| 12/2 | held-out | NMC | 2022-10-11 | 222.92 | 97.4 | 0.16 | 101.5 [100.7 – 102.2] | +4.03 | no | estimated |  |
| 14/1 | development | LFP | 2017-06-02 | 145.70 | 92.2 | 0.41 | 94.0 [93.3 – 94.6] | +1.74 | no | estimated |  |
| 14/2 | development | LFP | 2018-02-19 | 144.84 | 91.7 | 0.41 | 92.1 [91.2 – 92.9] | +0.40 | yes | estimated |  |
| 14/3 | development | LFP | 2019-05-09 | 141.17 | 89.4 | 0.24 | 90.5 [86.8 – 94.3] | +1.18 | yes | estimated |  |
| 15/1 | held-out | LFP | 2017-06-05 | 168.10 | 86.2 | 0.18 | – | – | no | not possible: no levels yet |  |
| 15/2 | held-out | LFP | 2018-03-05 | 174.82 | 89.7 | 0.39 | – | – | no | not possible: too few full-empty pairs |  |
| 15/3 | held-out | LFP | 2019-10-12 | 168.82 | 86.6 | 0.43 | – | – | no | not possible: too few full-empty pairs |  |
| 15/4 | held-out | LFP | 2022-11-13 | 157.12 | 80.6 | 0.43 | 85.2 [84.3 – 86.1] | +4.62 | no | estimated |  |
| 17/1 | development | LFP | 2019-05-03 | 146.01 | 92.4 | 0.37 | – | – | no | not possible: no levels yet |  |
| 17/2 | development | LFP | 2020-06-27 | 144.61 | 91.5 | 0.42 | 91.8 [91.0 – 92.6] | +0.29 | yes | estimated |  |
| 17/3 | development | LFP | 2021-09-25 | 140.87 | 89.2 | 0.42 | 90.0 [89.4 – 90.5] | +0.80 | no | estimated |  |
| 17/4 | development | LFP | 2022-03-13 | 152.75 | 96.7 | 0.42 | 96.4 [95.1 – 97.6] | -0.30 | yes | estimated |  |
| 19/1 | development | LFP | 2018-02-20 | 186.61 | 93.3 | 0.41 | 98.2 [89.9 – 106.5] | +4.89 | yes | estimated |  |
| 19/2 | development | LFP | 2019-05-09 | 187.89 | 93.9 | 0.41 | 64.3 [40.1 – 88.5] | -29.65 | no | estimated |  |
| 20/1 | held-out | LFP | 2017-06-17 | 191.68 | 95.8 | 0.41 | – | – | no | not possible: no levels yet |  |
| 20/2 | held-out | LFP | 2018-03-18 | 192.36 | 96.2 | 0.41 | 98.0 [94.2 – 101.8] | +1.83 | yes | estimated |  |
| 20/3 | held-out | LFP | 2019-09-08 | 171.08 | 85.5 | 0.41 | 93.2 [91.5 – 94.8] | +7.65 | no | estimated |  |
| 21/1 | held-out | LFP | 2017-05-31 | 289.52 | 96.5 | 0.30 | 98.9 [97.9 – 99.9] | +2.42 | no | estimated |  |
| 21/2 | held-out | LFP | 2018-02-19 | 287.33 | 95.8 | 0.30 | 103.8 [101.6 – 106.1] | +8.06 | no | estimated |  |
| 21/3 | held-out | LFP | 2019-07-04 | 235.34 | 78.4 | 0.30 | – | – | no | not possible: too few full-empty pairs |  |

### Bias per chemistry

| Chemistry | Set | Tests with an estimate | Median error, points | Estimate below the test | Test C-rate (min – max) | Median error, tests below / at or above the chemistry's median test C-rate |
|---|---|---|---|---|---|---|
| LMO/NMC | held-out | 13 of 16 | +0.28 | 6 of 13 | 0.30 – 0.85 | -2.62 (n = 7) / +0.36 (n = 6); split at 0.84 C |
| LMO/NMC | development | 6 of 6 | -0.85 | 4 of 6 | 0.30 – 0.84 | -2.96 (n = 2) / +0.40 (n = 4); split at 0.84 C |
| NMC | held-out | 9 of 12 | -2.01 | 7 of 9 | 0.16 – 0.33 | -2.01 (n = 5) / -2.22 (n = 4); split at 0.32 C |
| NMC | development | 4 of 7 | +2.65 | 1 of 4 | 0.31 – 0.34 | – (n = 0) / +2.65 (n = 4); split at 0.32 C |
| LFP | held-out | 5 of 10 | +4.62 | 0 of 5 | 0.18 – 0.43 | +5.24 (n = 2) / +4.62 (n = 3); split at 0.41 C |
| LFP | development | 8 of 9 | +0.60 | 2 of 8 | 0.24 – 0.42 | +1.74 (n = 3) / +0.29 (n = 5); split at 0.41 C |

No rate correction is applied: a field test discharges at the system's maximum power and often deeper than daily
operation, and the estimate measures the capacity between two stated rest voltages under normal load, not the charge
between the system's own stops that a field test runs to.

## Fade rates

![Fade rates](img/02_fade.png)

| System | Set | Chemistry | Rate, points/year [90 % band] | Bootstrap 90 % | Bootstrap ÷ band width | Per 100 EFC [90 % band] | EFC per year | Span, years | Pairs full-empty / same-kind | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| 01 | development | LMO/NMC | -2.67 [-2.78, -2.56] | [-2.81, -2.44] | 1.69 | -2.16 [-2.25, -2.07] | 124 | 6.4 | 782 / 382 | estimated |
| 02 | held-out | LMO/NMC | -3.55 [-3.64, -3.45] | [-3.67, -3.44] | 1.21 | -2.10 [-2.16, -2.04] | 169 | 5.9 | 750 / 791 | estimated |
| 03 | held-out | LMO/NMC | -3.57 [-3.63, -3.52] | [-3.64, -3.50] | 1.25 | -2.04 [-2.08, -2.01] | 175 | 5.5 | 1142 / 446 | estimated |
| 04 | development | LMO/NMC | -3.73 [-3.93, -3.52] | [-4.02, -3.36] | 1.58 | -1.95 [-2.06, -1.84] | 191 | 5.5 | 850 / 678 | estimated |
| 05 | held-out | LMO/NMC | -3.41 [-3.45, -3.38] | [-3.52, -3.32] | 2.93 | -1.93 [-1.95, -1.91] | 177 | 5.0 | 743 / 519 | estimated |
| 06 | held-out | LMO/NMC | -3.21 [-3.46, -2.97] | [-3.39, -2.94] | 0.93 | -2.18 [-2.34, -2.01] | 148 | 5.3 | 992 / 378 | estimated |
| 07 | development | NMC | -0.75 [-1.32, -0.18] | [-1.43, -0.22] | 1.06 | -0.63 [-1.11, -0.15] | 119 | 6.0 | 71 / 770 | estimated |
| 08 | held-out | NMC | -2.05 [-2.24, -1.86] | [-2.30, -1.74] | 1.48 | -1.43 [-1.57, -1.30] | 143 | 5.7 | 173 / 541 | estimated |
| 09 | held-out | NMC | – [–, –] | [–, –] | – | – [–, –] | – | 6.1 | 85 / 356 | not possible: the capacity is implausible at 2020-06-30 (charges end above the full level) |
| 10 | held-out | NMC | +0.60 [-1.69, +2.90] | [-1.99, +3.60] | 1.22 | +0.63 [-1.76, +3.01] | 96 | 5.0 | 108 / 320 | estimated |
| 11 | development | NMC | -1.38 [-1.68, -1.08] | [-1.71, -1.03] | 1.13 | -0.58 [-0.71, -0.46] | 237 | 2.7 | 183 / 540 | estimated |
| 12 | held-out | NMC | -1.26 [-1.79, -0.73] | [-1.73, -0.80] | 0.87 | -0.95 [-1.35, -0.55] | 133 | 1.7 | 79 / 507 | estimated |
| 13 | held-out | NMC | -1.92 [-1.97, -1.86] | [-2.07, -1.80] | 2.57 | -0.97 [-1.00, -0.94] | 198 | 2.3 | 499 / 387 | estimated |
| 14 | development | LFP | -1.06 [-1.30, -0.83] | [-1.40, -0.72] | 1.42 | -0.95 [-1.16, -0.73] | 113 | 3.6 | 47 / 376 | estimated |
| 15 | held-out | LFP | -2.11 [-2.94, -1.28] | [-3.05, -1.32] | 1.04 | -2.07 [-2.88, -1.25] | 102 | 5.4 | 83 / 284 | estimated |
| 16 | held-out | LFP | -1.37 [-1.49, -1.26] | [-1.50, -1.25] | 1.07 | -0.73 [-0.79, -0.67] | 188 | 2.7 | 76 / 236 | estimated |
| 17 | development | LFP | +2.64 [+2.16, +3.12] | [+1.41, +3.52] | 2.20 | +1.78 [+1.45, +2.10] | 149 | 2.6 | 88 / 151 | estimated |
| 18 | held-out | LFP | -2.80 [-15.39, +9.78] | [-41.27, +14.21] | 2.20 | -16.98 [-93.15, +59.20] | 17 | 1.7 | 9 / 27 | estimated |
| 19 | development | LFP | -1.00 [-2.63, +0.63] | [-3.09, +2.51] | 1.72 | -1.53 [-4.03, +0.97] | 65 | 4.0 | 153 / 235 | estimated |
| 20 | held-out | LFP | -4.63 [-5.39, -3.86] | [-5.58, -3.06] | 1.65 | -2.79 [-3.25, -2.33] | 166 | 3.1 | 48 / 278 | estimated |
| 21 | held-out | LFP | – [–, –] | [–, –] | – | – [–, –] | 96 | 3.9 | 59 / 225 | not possible: the capacity is implausible at 2020-04-30 (charges end above the full level) |

EFC: equivalent full cycles, the charge moved divided by twice the nominal capacity.
The band is the core's: a Student-t interval on the lag-1 sandwich standard error of the fit over all pairs.
The month bootstrap resamples whole calendar months of pairs; it also keeps any correlation within a month
(temperature, season, operation) that the band does not model. The two are shown side by side, not merged.
Held-out systems: the bootstrap interval is wider than the band in 10 of 12 (median ratio 1.23, largest 2.93 on system 05).
Development systems: the bootstrap interval is wider than the band in 7 of 7 (median ratio 1.58, largest 2.20 on system 17).
Where the bootstrap is much wider, the band understates the uncertainty of the rate.

Means per chemistry, against the paper's algorithm means (Figgener et al. 2024, Results):

| Chemistry | Held-out mean, points/year | Paper, algorithm mean | Held-out minus paper | All systems (information) | Development only (information) |
|---|---|---|---|---|---|
| LMO/NMC | -3.44 (n = 4) | -3.1 | -0.34 | -3.36 (n = 6) | -3.20 (n = 2) |
| NMC | -1.15 (n = 4) | -1.9 | +0.75 | -1.13 (n = 6) | -1.07 (n = 2) |
| LFP | -2.73 (n = 4) | -2.0 | -0.73 | -1.48 (n = 7) | +0.19 (n = 3) |

## Offset current

besslib fits the offset current together with the capacity. The paper finds it from full-to-full and
empty-to-empty pairs: the intercept of their mean current against their voltage difference (Figgener et al. 2024,
Supplementary Fig. 13). Both are computed here from the same pairs; besslib's is the whole-record fit.

| System | Set | Chemistry | besslib, A | The paper's way, A | Difference, A | Same-kind pairs used |
|---|---|---|---|---|---|---|
| 01 | development | LMO/NMC | -0.009 | -0.015 | +0.006 | 378 |
| 02 | held-out | LMO/NMC | -0.020 | -0.025 | +0.005 | 790 |
| 03 | held-out | LMO/NMC | -0.008 | -0.011 | +0.004 | 440 |
| 04 | development | LMO/NMC | -0.020 | -0.027 | +0.007 | 675 |
| 05 | held-out | LMO/NMC | -0.009 | -0.005 | -0.004 | 514 |
| 06 | held-out | LMO/NMC | -0.004 | -0.006 | +0.002 | 371 |
| 07 | development | NMC | -0.071 | -0.050 | -0.021 | 770 |
| 08 | held-out | NMC | -0.038 | -0.025 | -0.013 | 541 |
| 10 | held-out | NMC | -0.049 | -0.040 | -0.010 | 320 |
| 11 | development | NMC | -0.432 | -0.485 | +0.053 | 540 |
| 12 | held-out | NMC | -0.530 | -0.565 | +0.035 | 507 |
| 13 | held-out | NMC | -0.340 | -0.323 | -0.017 | 387 |
| 14 | development | LFP | -0.263 | -0.266 | +0.003 | 376 |
| 15 | held-out | LFP | -0.717 | -0.724 | +0.007 | 284 |
| 16 | held-out | LFP | -0.212 | -0.217 | +0.005 | 236 |
| 17 | development | LFP | -0.269 | -0.276 | +0.007 | 151 |
| 18 | held-out | LFP | -0.165 | -0.086 | -0.079 | 27 |
| 19 | development | LFP | -0.465 | -0.401 | -0.065 | 235 |
| 20 | held-out | LFP | -0.754 | -0.730 | -0.024 | 278 |
| 21 | held-out | LFP | -0.778 | -0.660 | -0.118 | 225 |

Systems with both: 20. Largest difference: 0.118 A. Negative means the battery loses
charge the meter does not see (BMS supply, balancing, self-discharge, sensor offset).

## Synthetic check V6 and code-path check V7

![Synthetic check](img/02_synthetic.png)

- Packs: 200; with a fade rate: 200. Each pack has its own operation, current-sensor gain error and
  stop errors of its BMS; the full chain runs on each (rest detection, classification, pairs, fade rate) with the frozen settings.
- Truth: the expected fade of the usable capacity, -1.88 points per year (the simulated capacity fades by -2.0; the BMS stops at voltage limits, so the usable part fades a little slower).
- Mean estimate: -1.90 points per year.
- The band held the truth in 54 of 200 packs (27.0%); each pack's own usable fade in 17.0%; the capacity fade in 0.0%.
- Offset current: median absolute error 0.0001 A (true value -0.050 A).
- Bootstrap ÷ band width: median 0.98.
- **V6** (85-95 %): **fail**.

**V7, the code path.** Core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2): max relative difference 2.0e-13 (rule <= 1e-6): **pass**.
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
4. **Model.** The charge measured over a pair is `q = C · x − I_offset · Δt + γF · ΔuF + γE · ΔuE`: C the usable
   capacity, I_offset the current the meter does not see (BMS supply, balancing, self-discharge, sensor offset), Δt the
   time between the rests. Each end of a pair adds its own kind's relaxed rest voltage minus a reference, because two
   stops of one kind are not at the same state of charge; γF and γE (Ah per volt near full and near empty) turn that
   voltage into charge. A voltage term is left out while its kind has fewer than 2 same-kind pairs in
   memory (`min_same`). The capacity is read at the forgetting-weighted mean full and empty rest voltages of the memory
   time; the record gives both. All are fitted together by weighted least squares.
5. **Forgetting.** A pair of age s counts with weight exp(−s / 180 days).
6. **Trend.** The fit with forgetting also carries a linear trend, `C = c0 + r · years`. The value is read at the date,
   so a steady fade does not lag (`trend_min_cross` = 0: always on).
7. **Band.** 90 %, from the scatter of the pairs themselves: a lag-1 sandwich covariance of the residuals (consecutive
   pairs share a rest, so their errors are correlated at lag 1) and a Student-t quantile. The forgetting weight enters
   the band's sums squared, as for a weighted fit. The current sensor's gain error (0.5%, Figgener et al.
   2024, Supplementary Table 2) is read as a rectangular half-width, 0.5%/√3 (JCGM 100:2008, 4.3.7), and
   added as a systematic term.
8. **Causal.** The estimate at a date uses only pairs that end before it, as a BMS would.
9. **Tests masked.** From 1 day before each field test begins to 1 day after it ends, the
   rests are removed and no pair spans the window. The estimate compared with a test is the one at the start of
   its masked window.
10. **Fade rate.** The same fit without forgetting, over the whole record: the fade of the capacity between fixed rest
   voltages, the record's mean. It needs full-to-empty pairs spanning at least 1 year.
11. **Plausibility.** An estimate is `not possible from this data` when its levels have crossed, its value or band
   reaches zero, or its memory holds a full-to-empty or empty-to-full pair of the wrong sign. These rules were found on
   held-out system 09.

When a step is not possible, the output says `not possible from this data` with the reason, and gives no value.

Settings (`CapacityConfig()`, hash `b30137b3f868`):

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
| `voltage_terms` | every_pair_quadratic | relaxed rest-voltage terms: `every_pair` (each end its own kind's voltage minus a reference); `none` as in 0.1 |
| `local_trend` | True | the fit with forgetting carries a linear trend and reads the value at the date |
| `band_forgetting` | 2 | how often the forgetting weight enters the band's sums (2: weighted least squares; 1: as in 0.1) |
| `plausibility` | True | crossed levels, a value or band at or below zero, or a wrong-sign pair give `not possible` |
| `trend_min_cross` | 0 | fewest full-to-empty or empty-to-full pairs in memory for the trend (decayed count); tuned on the development systems |
| `gain_rectangular` | True | the gain error is read as a rectangular half-width (÷ √3) |
| `quad_min_rests` | 2 |  |
| `rest_time` | reading |  |
| `above_full_min_rests` | 5 |  |
| `above_full_share` | 0.1 |  |
| `limits_share` | 0.25 |  |

## How the settings were fixed

The settings of release 0.1.0 were fixed on the development systems, by rules written before any estimate was compared with a test. What version 0.2 changed, and where each change was found: [version 0.2 page](04_version_0_2.md); version 0.3: [version 0.3 page](06_version_0_3.md).

- **Grid search** (27 settings: current threshold C/50, C/100, C/200; minimum rest duration, min 10, 20, 40; memory time, days 30, 90, 180). Rule: the lowest median
  absolute error among the settings whose 90 % band holds at least 80 % of the development tests;
  if none does, the highest coverage. **No setting reached it**: the highest development coverage was 59%. The rule's fallback, the highest coverage, chose C/200, 20 min, 180 days (coverage 59%, median absolute error 2.35 points). So before the first held-out run the 90 % band was already known to hold only 59% of the development tests.
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

**No estimate in the first year.** The full and empty levels need 365 days of history, so the first year of every record gives none (`not possible from this data: not enough history yet to set the full and empty levels`). Over the 21 systems with results, 788 of 1269 months with data have an estimate; 248 lie before the first pair.
Field tests in that first year: 8 (05/1, 06/1, 10/1, 11/1, 12/1, 15/1, 17/1, 20/1).

| System | Set | Months with data | Estimated | Before the first pair | too few full-empty pairs | too few same-kind pairs | no full-empty pair within the memory time | no positive variance for the band | After the last pair | Estimated month ends without data that month | Median band width, points |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 01 | development | 80 | 65 (81%) | 12 | 0 | 0 | 1 | 0 | 2 | 4 | 1.0 |
| 02 | held-out | 76 | 63 (83%) | 13 | 0 | 0 | 0 | 0 | 0 | 6 | 1.0 |
| 03 | held-out | 71 | 56 (79%) | 12 | 0 | 0 | 0 | 2 | 1 | 6 | 0.9 |
| 04 | development | 69 | 55 (80%) | 12 | 0 | 1 | 0 | 1 | 0 | 7 | 1.1 |
| 05 | held-out | 57 | 42 (74%) | 12 | 1 | 0 | 1 | 0 | 1 | 5 | 0.9 |
| 06 | held-out | 66 | 51 (77%) | 12 | 0 | 0 | 2 | 0 | 1 | 7 | 0.9 |
| 07 | development | 76 | 43 (57%) | 12 | 17 | 0 | 1 | 2 | 1 | 2 | 2.5 |
| 08 | held-out | 72 | 47 (65%) | 13 | 8 | 0 | 3 | 0 | 1 | 2 | 1.8 |
| 09 | held-out | 70 | 33 (47%) | 10 | 9 | 0 | 2 | 0 | 1 | 3 | 2.1 |
| 10 | held-out | 66 | 43 (65%) | 12 | 8 | 0 | 2 | 0 | 1 | 3 | 2.5 |
| 11 | development | 47 | 21 (45%) | 12 | 11 | 0 | 0 | 2 | 1 | 0 | 1.9 |
| 12 | held-out | 43 | 22 (51%) | 12 | 9 | 0 | 0 | 0 | 0 | 1 | 2.0 |
| 13 | held-out | 42 | 28 (67%) | 12 | 1 | 0 | 0 | 0 | 1 | 0 | 0.7 |
| 14 | development | 60 | 30 (50%) | 9 | 20 | 0 | 0 | 0 | 1 | 0 | 1.7 |
| 15 | held-out | 66 | 32 (48%) | 11 | 22 | 0 | 0 | 0 | 1 | 3 | 5.7 |
| 16 | held-out | 47 | 32 (68%) | 13 | 1 | 0 | 0 | 0 | 1 | 0 | 1.0 |
| 17 | development | 46 | 30 (65%) | 13 | 1 | 0 | 0 | 0 | 2 | 0 | 1.3 |
| 18 | held-out | 34 | 5 (15%) | 11 | 17 | 0 | 0 | 0 | 1 | 0 | 36.1 |
| 19 | development | 72 | 41 (57%) | 12 | 3 | 0 | 7 | 2 | 7 | 3 | 11.8 |
| 20 | held-out | 57 | 25 (44%) | 12 | 8 | 0 | 2 | 1 | 9 | 0 | 7.7 |
| 21 | held-out | 52 | 24 (46%) | 11 | 13 | 0 | 1 | 0 | 1 | 4 | 6.2 |

Column names are short forms of the statuses: *too few full-empty pairs*: `not possible from this data: too few full-to-empty or empty-to-full pairs`; *too few same-kind pairs*: `not possible from this data: too few full-to-full or empty-to-empty pairs to fix the offset`; *no full-empty pair within the memory time*: `not possible from this data: no full-to-empty or empty-to-full pair within the memory time`; *no positive variance for the band*: `not possible from this data: the residuals give no positive variance for the band`.
"Before the first pair" months lie before the levels exist or before the first pair after them.
"Estimated month ends without data that month": the estimate carries the earlier pairs forward, decaying; its
band widens as they age, but no new measurement enters. The figures show these months as band without a rug mark.

**Tests without an estimate, by reason** (systems with results): not possible: no levels yet: 8; not possible: too few full-empty pairs: 6; not possible: no full-empty pair within the memory time: 1.
This is the designed answer: where the operation gives too few full and empty rests, the output says so and gives no value.
This version covers home storage that reaches full and empty regularly. Storage that works at a middle state of
charge, or only in partial windows, is not covered, and version 0.2 does not refuse it: it reads the edges of the
daily window as full and empty and gives too low a capacity ([limits](limits.md), [simulator](05_simulator.md)).

**The band is on the capacity between two rest voltages, not on the charge between the BMS stops.** The trend term
reads the value at the date. On 200 synthetic packs the band held the capacity between the rest voltages at
3789 of 4562 month ends (83%), but the charge between the stops only at 1524 of
4782 (32%). The stops move as the system ages ([version 0.2 page](04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with)). The band does not
include that drift, nor a systematic difference between daily operation and a field test at maximum power.

**The paper's interval is not this band.** In Figgener et al. (2024, Fig. 4) the shaded interval contains a fixed share of all
estimates: it is the spread of the estimates. besslib's 90 % band is a band on the capacity, and V4 tests how often it
holds the field test.

**Field tests are not daily operation.** A field test discharges at the system's maximum power, often deeper than
daily operation. No rate correction is applied; the estimate is compared as it is. The bias table above shows the
median signed error per chemistry next to the test C-rates.

**Plausibility.** Month ends that are `not possible` by the plausibility rules: 27 (system 09: 27 crossed levels). Estimated month ends with a value or band at or below zero: 0.

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

**Largest held-out misses:** 10/4 -12.3 points (band 1.1 wide, not held); 06/3 -9.0 points (band 22.6 wide, held); 21/2 +8.1 points (band 4.5 wide, not held).

**Held because the band is wide:** the median band of the held-out estimates is 1.7 points; the widest bands among the held tests are 08/2 23.1 points (error -7.0); 06/3 22.6 points (error -9.0); 10/3 20.0 points (error -5.4). A test held by a wide band says little about accuracy.

**Estimates above the nominal capacity** (month ends, highest value): 12 (18 of 23, up to 105.7 %; its highest field test 100.9 %); 19 (8 of 44, up to 108.5 %; its highest field test 93.9 %); 20 (4 of 25, up to 104.1 %; its highest field test 96.2 %); 21 (9 of 28, up to 106.8 %; its highest field test 96.5 %). On 19, 20 and 21 no field test reaches the nominal capacity.

**Fade bands that include zero:** 10 (held-out, +0.60 [-1.69, +2.90]), 18 (held-out, -2.80 [-15.39, +9.78]), 19 (development, -1.00 [-2.63, +0.63]). On these records the fade is not resolved.

**Fewest pairs:** system 18 with 36 pairs (9 full-to-empty or empty-to-full) over 1.7 years; its fade -2.80 points per year, band [-15.39, +9.78], month bootstrap [-41.3, +14.2], reduced chi-square 5.2.

**System 17: a capacity step after a data gap.** The data stop on 25 Jan 2022 and resume on 11 Feb 2022 (17 days). The monthly estimate around the gap, % of nominal: 89.5 (Jan 2022), 94.1 (Feb 2022), 97.2 (Mar 2022). The field tests move the same way: 17/3 read 89.2 %, 17/4 96.7 %. A straight line through a record with a step reads positive here: +2.64 points per year [+2.16, +3.12]. Cells do not gain capacity, so the step is in the usable capacity the system gives; the fade model has no term for a step.

**System 19: high scatter.** Its median monthly band is 11.8 points wide, against 1.7 for the median of the other systems with results. The fade fit's reduced chi-square is 8.8. Its median end-of-discharge level is 3.168 V per cell; the other LFP systems: 14 2.973, 15 2.990, 16 3.010, 17 2.907, 18 3.100, 20 3.144, 21 3.165. That this stop lies on the flat part of the LFP voltage curve, where the edge voltage says little about the state of charge, is an observation from the development work that this page does not back with a number.

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
Every result row carries the configuration hash; this page refuses to build from results of any hash other than `b30137b3f868`.

## Source and credit

Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in
capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.
Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9,
licence CC BY-NC-ND 4.0: cited only; no figure or text of it is copied.
The values on this page are computed by besslib from that data; only the fade means marked as the paper's are taken from it.
besslib computes capacity figures; it does not certify them.
