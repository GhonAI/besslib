# Version 0.3: what changed, and what it does on the same data

Written by `validation/make_v03_doc.py` from `validation/results/` and from the files of release 0.2.0 (`git show v0.2.0:<path>`). No number on this page is typed by hand.

## A third look, not a blind test

The 14 held-out field systems were run at the settings of releases 0.1.0 and 0.2.0 before. Version 0.3 was built and judged on the simulator first (`besslib.sim`, [page](05_simulator.md#version-03-on-new-seeds)), on the development field systems as information, and then run once on the held-out field systems. Its numbers are published as they came; nothing was changed after that run.

## What changed

Version 0.2's capacity band held 29 % of the 28 held-out LMO/NMC and NMC field tests (V4), and on simulated packs it did not hold even the capacity it is built for, the charge between the two rest voltages it reads at. Version 0.3 reads that capacity better and refuses a capacity whose full or empty level is not the pack's. Settings `b30137b3f868` (0.2: `b3959941c19b`); the record's settings are unchanged (`d49cbfdcd5b6`).

| Setting | 0.2 | 0.3 | What it does |
|---|---|---|---|
| `voltage_terms` | every_pair | every_pair_quadratic | a quadratic voltage term at each end of the capacity fit, beside the linear one |
| `quad_min_rests` | 0 | 2 | each quadratic term is used only while its kind has this many rests in memory (tuned on the development systems, `validation/tune_quadratic.py`) |
| `rest_time` | start | reading | a pair's time runs between the moments its two rest voltages are read, not between the rests' starts |
| `above_full_min_rests` | 0 | 5 | rule A: this many charges in the trailing year that end above the full level mean the full level is not the pack's |
| `above_full_share` | 0 | 0.1 | rule A: how far above the full level, as a share of the span between the levels |
| `limits_share` | 0 | 0.25 | rule B (only with the pack's documented voltage limits): a level this far inside them, as a share of the window, is not the pack's (chosen on the simulator's development packs) |

On the simulator (seeds 2000-2199) A1's band now holds its own quantity at 10416 of 11031 (94.4%) month ends without drift; the other rows, passed and failed, are on the [simulator page](05_simulator.md#version-03-on-new-seeds).

## The checks of version 0.3

| Check | Value | Pass if | Result |
|---|---|---|---|
| F1 v0.1.0 files | 0 of 102 files differ | 0 differences, except the A3 text and the W4/W6 rows | pass |
| F1 v0.1.0 vectors | 0 of 6 test-vector files differ | 0 differences | pass |
| F1 v0.1.0 hashes | 109e9eb1efa5, c2f7f545cebb | the release's | pass |
| F1 v0.2.0 files | 0 of 102 files differ | 0 differences | pass |
| F1 v0.2.0 vectors | 0 of 8 test-vector files differ | 0 differences | pass |
| F1 v0.2.0 hashes | b3959941c19b, d49cbfdcd5b6 | the release's | pass |
| R2 | 0 of 303 month ends that 0.2.0 estimated on the 7 development systems are refused by rule A | 0 removed | pass |
| W7 | band held the hidden drain in 182 of 200 packs (91.0%); mean error -0.084 mA, limit +-0.024 (200 packs with an estimate) | 85-95 % and mean error within 2 sd / sqrt(n) of zero | fail |

F1: the settings of releases 0.1.0 and 0.2.0 still give those releases' published numbers. R2: rule A removes no estimate of the development field systems. W7: version 0.3's rule for A4 on the synthetic packs adds the mean clause.

## The field systems, 0.2 against 0.3

Capacity ([page](02_capacity_validation.md)):

| Check | Scope | 0.2 | 0.3 |
|---|---|---|---|
| V2 | held-out LMO/NMC and NMC | median \|error\| 2.99 points (22/28 estimated) (pass) | median \|error\| 2.62 points (22/28 estimated) (pass) |
| V2 info | same, without 03/3 and 08/1 | median \|error\| 3.25 points (26 tests) (information) | median \|error\| 2.98 points (26 tests) (information) |
| V3 | held-out LFP | median \|error\| inf points; median error +4.48 (5/10 estimated) (fail) | median \|error\| inf points; median error +4.62 (5/10 estimated) (fail) |
| V4 | held-out LMO/NMC and NMC | band held the test in 29% of 28 (fail) | band held the test in 32% of 28 (fail) |
| V4 info | held-out LFP | band held the test in 10% of 10 (information) | band held the test in 10% of 10 (information) |
| V4 info | same as V4, without 03/3 and 08/1 | band held the test in 27% of 26 (information) | band held the test in 31% of 26 (information) |
| V5 | held-out LMO/NMC (4 systems) | mean fade -3.49 points/year; paper -3.1 (pass) | mean fade -3.44 points/year; paper -3.1; without one system -3.51 to -3.39 (pass) |
| V5 info | all LMO/NMC (6 systems) | mean fade -3.47 points/year (information) | mean fade -3.36 points/year (information) |
| V5 | held-out NMC (4 systems) | mean fade -1.21 points/year; paper -1.9 (fail) | mean fade -1.15 points/year; paper -1.9; without one system -1.74 to -0.86 (fail) |
| V5 info | all NMC (6 systems) | mean fade -1.17 points/year (information) | mean fade -1.13 points/year (information) |
| V5 | held-out LFP (4 systems) | mean fade -1.84 points/year; paper -2.0 (pass; 0.2's scope: held-out LFP (5 systems)) | mean fade -2.73 points/year; paper -2.0; without one system -3.18 to -2.09 (fail) |
| V5 info | all LFP (7 systems) | mean fade -1.07 points/year (information) | mean fade -1.48 points/year (information) |
| V6 | 200 synthetic packs | band held the true usable fade (-1.88) in 76.0% (fail) | band held the true usable fade (-1.88) in 27.0% (fail) |
| V7 | core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2) | max relative difference 2.0e-14 (pass) | max relative difference 2.0e-13 (pass) |
| V8 | 21 systems | one command per system: python validation/run_capacity.py &lt;id> (pass) | one command per system: python validation/run_capacity.py &lt;id> (pass) |

- Rule A refuses month ends on: 09 (held-out): 1 month end, 0 of them estimated by 0.2; 21 (held-out): 2 month ends, 0 of them estimated by 0.2. On 21 the fade rate is then `not possible` (a capacity in the record breaks rule A), so check V5 counts it out.
- Information: month ends that 0.2 estimated and 0.3 refuses for another reason than rule A: 03 (held-out): 2 (the residuals give no positive variance for the band); 07 (development): 1 (the residuals give no positive variance for the band); 19 (development): 2 (the residuals give no positive variance for the band).

The fade rate of the held-out LFP systems (check V5), 0.2 against 0.3:

| System | 0.2, points/year (band) | 0.3, points/year (band) |
|---|---|---|
| 15 | -2.09 (-2.96 to -1.23) | -2.11 (-2.94 to -1.28) |
| 16 | -1.37 (-1.47 to -1.28) | -1.37 (-1.49 to -1.26) |
| 18 | +1.45 (-6.60 to +9.49) | -2.80 (-15.39 to +9.78) |
| 20 | -4.79 (-5.58 to -3.99) | -4.63 (-5.39 to -3.86) |
| 21 | -2.40 (-3.93 to -0.87) | not possible from this data: the capacity is implausible at 2020-04-30 (charges end above the full level) |

The other Annex VII fields ([page](03_annex_vii.md)):

| Check | Scope | 0.2 | 0.3 |
|---|---|---|---|
| W1 info | development records | 7 of 7 records; 0 fields break the rule (information) | 7 of 7 records; 0 fields break the rule (information) |
| W1 | held-out records | 14 of 14 records; 0 fields break the rule (pass) | 14 of 14 records; 0 fields break the rule (pass) |
| W2 | 200 synthetic packs: resistance at the weighted mean age of the days in memory | fails under each truth. same-days truth 200 of 200: too wide, because that truth shares every day with the estimate, so the day-to-day scatter the statistical half-width stands for (median 0.62% of the value) is not in the difference; the largest difference is 0.77%, and the systematic half-width alone (median 0.53%) holds 187 of 200. At the date 0 of 200: the estimate lags the growing resistance (the value at the date is a median 2.3% above it). At 25 degrees C 90 of 200: the days in the band read a median +0.9% against 25 degrees C, and the estimator has no temperature model (fail) | fails under each truth: same days 200 of 200 (too wide: that truth shares every day with the estimate; the systematic half-width alone holds 187); at the date 0 of 200 (the estimate lags the growing resistance by a median 2.3%); at 25 degrees C 90 of 200 (no temperature model; the days read a median +0.9%) (fail) |
| W2 | 200 synthetic packs: resistance growth per year, whole record, against the simulator's known growth (median 4.93 % per year at the fit's origin; this truth was chosen after the first results) | band held the truth in 184 of 200 (92.0%); same-days truth (information): 199 of 200 (99.5%) (pass) | band held the truth in 184 of 200 (92.0%); same-days truth (information): 199 of 200 (99.5%) (pass) |
| W5 | 200 synthetic packs: round-trip efficiency, full-to-full loops | band held the truth in 176 of 200 (88.0%) (pass) | band held the truth in 176 of 200 (88.0%) (pass) |
| W7 | 200 synthetic packs: apparent charge loss (hidden drain) | band held the truth in 188 of 200 (94.0%) (pass) | band held the truth in 182 of 200 (91.0%) (pass) |
| W8 | 200 synthetic packs: Ah, kWh, cycles; planted events (pack B: current gain error only) | largest relative error 0.498%; events exact on 200 of 200; 200 of 200 packs within the rule (pass) | largest relative error 0.498%; events exact on 200 of 200; 200 of 200 packs within the rule (pass) |
| W8 info | 200 synthetic packs: the same on pack B with current and voltage noise | largest relative error 0.498%; events exact on 189 of 200; 189 of 200 packs within the rule (information) | largest relative error 0.498%; events exact on 189 of 200; 189 of 200 packs within the rule (information) |
| W3 info | development capacity tests | median \|difference\| 21.9 % over 12 of 22 tests with a reference; median difference by chemistry {'LFP': 64.1, 'LMO/NMC': -8.2, 'NMC': -11.5} (information) | median \|difference\| 21.9 % over 12 of 22 tests with a reference; median difference by chemistry LFP +64.1 %, LMO/NMC -8.2 %, NMC -11.5 % (information) |
| W3 | held-out capacity tests | 18 of 38 tests have a reference: not possible (not possible) | 18 of 38 tests have a reference: not possible (not possible) |
| X5 | development: W4 table, system-days with more than one row | 0 (pass) | 0 (pass) |
| W4 info | development: neighbouring 5-degree bands: the colder reads higher | 50 of 58 (86%) (information) | 50 of 58 (86%) (information) |
| X5 | development: W6 table, system-days with more than one row | 0 (pass) | 0 (pass) |
| W6 info | development: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 21 of 31 (68%) (information) | 21 of 31 (68%) (information) |
| X5 | held-out: W4 table, system-days with more than one row | 0 (pass) | 0 (pass) |
| W4 | held-out: neighbouring 5-degree bands: the colder reads higher | 94 of 101 (93%) (pass) | 94 of 101 (93%) (pass) |
| X5 | held-out: W6 table, system-days with more than one row | 0 (pass) | 0 (pass) |
| W6 | held-out: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 39 of 55 (71%) (fail) | 39 of 55 (71%) (fail) |
| W9 | core against batch, resistance: 240 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 3.6e-14 (64-bit); 2.6e-06 (32-bit, information) (pass) | max relative difference 3.6e-14 (64-bit); 2.6e-06 (32-bit, information) (pass) |
| W9 | core against batch, efficiency: 10 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 0.0e+00 (64-bit); 1.3e-06 (32-bit, information) (pass) | max relative difference 0.0e+00 (64-bit); 1.3e-06 (32-bit, information) (pass) |
| W9 | daily counters against rest counters: 138814 rests, 21 systems (1-21) | max relative difference 8.7e-13 (throughput, system 13) (pass; 0.2's scope: daily counters against rest counters: 138814 rests, systems [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]) | max relative difference 8.7e-13 (throughput, system 13) (pass) |
| W10 | capacity run rebuilt for 21 systems | 0 of 60 files differ (pass; 0.2's scope: phase 2 rebuilt for 21 systems) | 0 of 60 files differ (pass) |
| W10 | record A1 against the capacity run, every month end, 21 systems | 0 values differ over 1139 month ends (21 systems with a file) (pass; 0.2's scope: record A1 against the phase 2 capacity run, every month end, 21 systems) | 0 values differ over 1139 month ends (21 systems with a file) (pass) |
| W10 | records rebuilt twice for 21 systems | 0 differences over 42 files (pass) | 0 differences over 42 files (pass) |

## Why V6 fell

Check V6 judges the fade rate's band on 200 synthetic packs against the true usable fade (-1.875 points per year). It fell from 76.0% to 27.0%. The same packs under five settings (`validation/diag_fade_band.py`) separate the two changes: the reading times double the bias (-0.013 → -0.024 points per year), and the quadratic terms narrow the band (median half-width 0.026 → 0.017). Each alone leaves 59.5% (the reading times) and 60.5% (the quadratic terms); together 27.0%. The bias is 1.3% of the true fade. Not fixed in 0.3; no pass rule moved.

| Settings | Band holds the true fade | Median half-width, points/year | Mean error, points/year |
|---|---|---|---|
| 0.2 | 152 of 200 (76.0%) | 0.025 | -0.013 |
| 0.2 with the reading times | 119 of 200 (59.5%) | 0.026 | -0.024 |
| 0.3 without the reading times | 121 of 200 (60.5%) | 0.016 | -0.013 |
| 0.3 without the quadratic terms | 119 of 200 (59.5%) | 0.026 | -0.024 |
| 0.3 | 54 of 200 (27.0%) | 0.017 | -0.024 |

## Why A4's band moved

The quadratic terms narrow A4's band too; a new leak then lies outside it for longer. The numbers are on the [simulator page](05_simulator.md#version-03-on-new-seeds) (finding 7).

## Rebuild

```
python validation/run_capacity.py <id>        # capacity, one system (all 21 for the page)
python validation/run_annex_vii.py <id>       # the record
python validation/check_capacity.py && python validation/check_annex_vii.py && python validation/check_v03.py
python validation/diag_fade_band.py           # why V6 fell (200 synthetic packs)
python validation/make_v03_doc.py             # this page
```
