# Version 0.2: what changed, and what it does on the same data

Written by `validation/make_v02_doc.py` from `validation/results/` and from the files of release 0.1.0 (tag `v0.1.0`). No number on this page is typed by hand.

## Seen once

The 14 held-out systems were seen once before, at the settings of release 0.1.0 (tagged 20 November 2026). Two faults this version fixes were found in those results: a capacity at or below zero marked `estimated` (held-out 09) and power capability `estimated` without a resistance (held-out 08, 18 and 20; development 19). The 0.2 settings were fixed on synthetic packs and the 7 development systems; then the held-out systems were run once with them. **The 0.2 held-out results are a second look, not a blind test.**

## What changed and why

| Fault or change | Release 0.1.0 | Version 0.2 | Setting | Found on |
|---|---|---|---|---|
| Apparent charge loss (A4) biased on synthetic packs (W7) | the capacity fit took two rests of one kind as the same state of charge | two relaxed rest-voltage terms on every pair; the capacity is read at the mean full and empty rest voltages of the memory time, which the record gives | `voltage_terms`: none → every_pair | synthetic packs |
| The capacity lagged a fading capacity | a forgetting-weighted mean: about the memory time behind | a linear trend in the fit with forgetting, value read at the date; its gate tuned on the development systems | `local_trend`: False → True; `trend_min_cross` = 0 | synthetic packs |
| The capacity band used the forgetting weight once | the weight entered the band's sums once | squared, as for a weighted least-squares fit | `band_forgetting`: 1 → 2 | synthetic chains |
| The gain error in the capacity band | 0.5 % added as it is | read as a rectangular half-width, ÷ √3 (JCGM 100:2008, 4.3.7), as A4 already did | `gain_rectangular`: False → True | synthetic chains (X7), before any 0.2 held-out run |
| A capacity at or below zero said `estimated` | no plausibility rule | crossed levels, a value or band at or below zero, or a wrong-sign pair in memory give `not possible from this data` | `plausibility`: False → True | held-out 09 |
| Power capability `estimated` without a resistance | A2 used the whole-record growth fit | A2 is `estimated` only where A5 is `estimated` at the same date | `a2_needs_a5`: False → True | held-out 08, 18 and 20; development 19 |
| W4 and W6 counted a day twice | 31 December at 00:00 and a last sample on 31 December were two rows | one row per system and day (X5) | none: check only | held-out results |
| Empty-slice warnings in the rest search | a warning when a rest had no temperature reading | the statistic is NaN without a warning; values unchanged (X8) | none | held-out results |
| The A3 method text | read awkwardly | new wording (X9) | none: text | the README |
| A warning in `soh.report` on pandas 2.2.2 | `dt.to_pydatetime` FutureWarning | Python datetimes built per value; the figure is the same (X10) | none | the lowest-version test |

With every new setting at its 0.1.0 value (`CapacityConfig().v0_1()`, `RecordConfig().v0_1()`) the code gives the 0.1.0 hashes (capacity `109e9eb1efa5`, record `c2f7f545cebb`) and the 0.1.0 numbers (X0). The 0.2 hashes: capacity `b3959941c19b`, record `d49cbfdcd5b6`. Every other threshold keeps its frozen 0.1.0 value.

Not in version 0.2: partial windows (Plett's form), for systems without regular full cycles. They come after the pack simulator (besslib.sim), which tests them at middle state of charge.

## The checks of version 0.2

The pass rules were fixed with the 0.2 plan, before any result.

| Check | Scope | Value | Pass rule | Result |
|---|---|---|---|---|
| X1 | cause check, 200 W7 packs, true change of state of charge in place of the labels | mean error -0.11 mA (sd 0.53); with the labels +4.43 mA | mean within ±0.075 mA (2 sd/√200) | **fail** |
| X0 files | – | 0 of 102 files differ | 0 differences, except the A3 text and the W4/W6 rows | pass |
| X0 vectors | – | 0 of 6 0.1 vector files differ | 0 differences | pass |
| X2 month ends | – | 0 of 847 estimated month ends (value or low <= 0: 0; crossed levels: 0; wrong-sign pair: 0); systems [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21] | 0 estimated month ends with value or low <= 0, crossed levels or a wrong-sign pair | pass |
| X2 records | – | 0 of 96 | 0 estimated A1 values with value or low <= 0 in the yearly tables | pass |
| X2 synthetic case | – | not possible from this data: the empty level has reached the full level, so full and empty no longer mark the two ends of the charge window | levels crossed gives status 12 | pass |
| X4 | – | 0 of 21 records | 0 records with A2 estimated while A5 is not | pass |
| X3 | band at the date, 200 synthetic packs, against the charge between the stops | 2278 of 4782 month ends (47.6%); mean lag +0.473 points | 85-95 %; mean lag within ±0.040 (2 sd/√200) | **fail** |
| X5 | development: W4 table, system-days with more than one row | 0 | 0 | pass |
| X5 | development: W6 table, system-days with more than one row | 0 | 0 | pass |
| X5 | held-out: W4 table, system-days with more than one row | 0 | 0 | pass |
| X5 | held-out: W6 table, system-days with more than one row | 0 | 0 | pass |
| X7 | capacity band on simulated pair chains without fade, gain term 0 | 116 of 300 (38.7%) with 180 days of memory; 134 of 300 (44.7%) without memory | 85-95 % each | **fail** |
| X8 | – | tests passed | test_rests.py passes under -W error::RuntimeWarning | pass |
| X9 record code | – | tests passed | the A3 method equals the approved wording | pass |
| X9 record files | – | 0 of 21 0.2 records differ | exact | pass |
| X9 README | – | wording found | exact | pass |
| X9 report | – | wording found | exact | pass |
| X10 | – | tests passed | test_report.py passes under -W error::FutureWarning | pass |

Failed: X1, X3, X7.

- **X1** failed by its number: with the true change of state of charge the bias falls from +4.43 to -0.11 mA. With the exact charge in place of the measured one it is -0.11 mA, so the rest does not come from the sampling. The fix was built as planned; W7 is its test.
- **X3** and **X7** fail against truths built from the BMS stops. Against the capacity between the rest voltages the fit reports, X3 would pass and X7 would pass by their own pass numbers (information, not a result); see [what the capacity measures](#what-the-capacity-measures-and-what-the-checks-compare-it-with).
- **X10**, the check's note: with the interpreter and library versions of this environment; run it from the environment with numpy 2.0, pandas 2.2.2, pyarrow 16 for the check as planned.

## Every V and W check, 0.1.0 against 0.2

The same checks with the same pass rules; the held-out systems as above.

### Capacity, V2–V8

| Check | Scope | 0.1.0 value | 0.1.0 | 0.2 value | 0.2 |
|---|---|---|---|---|---|
| V2 | held-out LMO/NMC and NMC | median \|error\| 2.62 points (22/28 estimated) | pass | median \|error\| 2.99 points (22/28 estimated) | pass |
| V2 info | same, without 03/3 and 08/1 | median \|error\| 2.62 points (26 tests) | information | median \|error\| 3.25 points (26 tests) | information |
| V3 | held-out LFP | median \|error\| inf points; median error +4.80 (5/10 estimated) | **fail** | median \|error\| inf points; median error +4.48 (5/10 estimated) | **fail** |
| V4 | held-out LMO/NMC and NMC | band held the test in 43% of 28 | **fail** | band held the test in 29% of 28 | **fail** |
| V4 info | held-out LFP | band held the test in 10% of 10 | information | band held the test in 10% of 10 | information |
| V4 info | same as V4, without 03/3 and 08/1 | band held the test in 42% of 26 | information | band held the test in 27% of 26 | information |
| V5 | held-out LMO/NMC (4 systems) | mean fade -3.14 points/year; paper -3.1 | pass | mean fade -3.49 points/year; paper -3.1 | pass |
| V5 info | all LMO/NMC (6 systems) | mean fade -2.69 points/year | information | mean fade -3.47 points/year | information |
| V5 | held-out NMC (4 systems) (0.1.0: held-out NMC (5 systems)) | mean fade -4.58 points/year; paper -1.9 | **fail** | mean fade -1.21 points/year; paper -1.9 | **fail** |
| V5 info | all NMC (6 systems) (0.1.0: all NMC (7 systems)) | mean fade -3.67 points/year | information | mean fade -1.17 points/year | information |
| V5 | held-out LFP (5 systems) | mean fade -1.39 points/year; paper -2.0 | **fail** | mean fade -1.84 points/year; paper -2.0 | pass |
| V5 info | all LFP (7 systems) (0.1.0: all LFP (8 systems)) | mean fade -0.80 points/year | information | mean fade -1.07 points/year | information |
| V6 | 200 synthetic packs | band held the true usable fade (-1.88) in 89.5% | pass | band held the true usable fade (-1.88) in 76.0% | **fail** |
| V7 | core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2) | max relative difference 2.0e-15 | pass | max relative difference 2.0e-14 | pass |
| V8 | 21 systems | one command per system: python validation/run_capacity.py <id> | pass | one command per system: python validation/run_capacity.py <id> | pass |

Result changed: V5 (held-out LFP (5 systems)); V6 (200 synthetic packs). A dash: the check has no such row in that version.

### The other fields, W1–W10

| Check | Scope | 0.1.0 value | 0.1.0 | 0.2 value | 0.2 |
|---|---|---|---|---|---|
| W1 info | development records | 7 of 7 records; 0 fields break the rule | information | 7 of 7 records; 0 fields break the rule | information |
| W1 | held-out records | 14 of 14 records; 0 fields break the rule | pass | 14 of 14 records; 0 fields break the rule | pass |
| W2 | 200 synthetic packs: resistance at the weighted mean age of the days in memory | fails under each truth. same-days truth 200 of 200: too wide, because that truth shares every day with the estimate, so the day-to-day scatter the statistical half-width stands for (median 0.62% of the value) is not in the difference; the largest difference is 0.77%, and the systematic half-width alone (median 0.53%) holds 187 of 200. At the date 0 of 200: the estimate lags the growing resistance (the value at the date is a median 2.3% above it). At 25 degrees C 90 of 200: the days in the band read a median +0.9% against 25 degrees C, and the estimator has no temperature model | **fail** | fails under each truth. same-days truth 200 of 200: too wide, because that truth shares every day with the estimate, so the day-to-day scatter the statistical half-width stands for (median 0.62% of the value) is not in the difference; the largest difference is 0.77%, and the systematic half-width alone (median 0.53%) holds 187 of 200. At the date 0 of 200: the estimate lags the growing resistance (the value at the date is a median 2.3% above it). At 25 degrees C 90 of 200: the days in the band read a median +0.9% against 25 degrees C, and the estimator has no temperature model | **fail** |
| W2 | 200 synthetic packs: resistance growth per year, whole record, against the simulator's known growth (median 4.93 % per year at the fit's origin; this truth was chosen after the first results) | band held the truth in 184 of 200 (92.0%); same-days truth (information): 199 of 200 (99.5%) | pass | band held the truth in 184 of 200 (92.0%); same-days truth (information): 199 of 200 (99.5%) | pass |
| W5 | 200 synthetic packs: round-trip efficiency, full-to-full loops | band held the truth in 176 of 200 (88.0%) | pass | band held the truth in 176 of 200 (88.0%) | pass |
| W7 | 200 synthetic packs: apparent charge loss (hidden drain) | band held the truth in 87 of 200 (43.5%) | **fail** | band held the truth in 188 of 200 (94.0%) | pass |
| W8 | 200 synthetic packs: Ah, kWh, cycles; planted events (pack B: current gain error only) | largest relative error 0.498%; events exact on 200 of 200; 200 of 200 packs within the rule | pass | largest relative error 0.498%; events exact on 200 of 200; 200 of 200 packs within the rule | pass |
| W8 info | 200 synthetic packs: the same on pack B with current and voltage noise | largest relative error 0.498%; events exact on 189 of 200; 189 of 200 packs within the rule | information | largest relative error 0.498%; events exact on 189 of 200; 189 of 200 packs within the rule | information |
| W3 info | development capacity tests | median \|difference\| 21.9 % over 12 of 22 tests with a reference; median difference by chemistry {'LFP': 64.1, 'LMO/NMC': -8.2, 'NMC': -11.5} | information | median \|difference\| 21.9 % over 12 of 22 tests with a reference; median difference by chemistry {'LFP': 64.1, 'LMO/NMC': -8.2, 'NMC': -11.5} | information |
| W3 | held-out capacity tests | 18 of 38 tests have a reference: not possible | not possible | 18 of 38 tests have a reference: not possible | not possible |
| W4 info | development: neighbouring 5-degree bands: the colder reads higher | 59 of 68 (87%) | information | 50 of 58 (86%) | information |
| W6 info | development: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 24 of 36 (67%) | information | 21 of 31 (68%) | information |
| W4 | held-out: neighbouring 5-degree bands: the colder reads higher | 112 of 120 (93%) | pass | 94 of 101 (93%) | pass |
| W6 | held-out: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 47 of 64 (73%) | **fail** | 39 of 55 (71%) | **fail** |
| W9 | core against batch, resistance: 240 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 3.6e-14 (64-bit); 2.6e-06 (32-bit, information) | pass | max relative difference 3.6e-14 (64-bit); 2.6e-06 (32-bit, information) | pass |
| W9 | core against batch, efficiency: 10 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 0.0e+00 (64-bit); 1.3e-06 (32-bit, information) | pass | max relative difference 0.0e+00 (64-bit); 1.3e-06 (32-bit, information) | pass |
| W9 | daily counters against rest counters: 138814 rests, systems [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21] | max relative difference 8.7e-13 (throughput, system 13) | pass | max relative difference 8.7e-13 (throughput, system 13) | pass |
| W10 | phase 2 rebuilt for 21 systems | 0 of 60 files differ | pass | 0 of 60 files differ | pass |
| W10 | record A1 against the phase 2 capacity run, every month end, 21 systems | – | – | 0 values differ over 1139 month ends (21 systems with a file) | pass |
| W10 | records rebuilt twice for 21 systems | 0 differences over 42 files | pass | 0 differences over 42 files | pass |
| W10 | capacity settings and committed phase 2 files | hash 109e9eb1efa5; 0 of 60 committed files differ | pass | – | – |

Result changed: W7 (200 synthetic packs: apparent charge loss (hidden drain)). A dash: the check has no such row in that version.

## Development tests, 0.1.0 against 0.2

The tests of the development systems, where the settings were tuned; not a result.

| Version | Tests | With an estimate | Median abs. error, points (estimated tests) | Band held the test | Median band width, points |
|---|---|---|---|---|---|
| 0.1.0 | 22 | 18 | 1.81 | 13 of 22 | 4.49 |
| 0.2 | 22 | 18 | 1.71 | 8 of 22 | 1.85 |

## What the capacity measures, and what the checks compare it with

Version 0.2 gives each rest a voltage term. The capacity it reports is then the capacity between a full and an empty rest at two given relaxed voltages. For A1 and the monthly estimate these are the forgetting-weighted mean rest voltages of the memory time; the record gives both. For the fade rate they are the record's mean, fixed. Release 0.1.0 reported the charge between the BMS's own stops, wherever they lay.

The checks' truths are charges between BMS stops. In the simulator these are the stops at its voltage limits; in the field, the stops of the capacity test, at the system's maximum power. The truths were fixed before version 0.2 and are kept: no truth is changed after the results. The second truth below was chosen after the first 0.2 results and is information only.

| Check | Truth fixed with the plan: charge between the stops | Capacity between the rest voltages (information) |
|---|---|---|
| X3, band at the date | 2278 of 4782 month ends (47.6%); mean lag +0.473 points (limit ±0.040) | 4248 of 4562 (93.1%); mean lag -0.037 (limit ±0.038) |
| X7, 180 days of memory | 116 of 300 (38.7%) | 270 of 300 (90.0%) |
| X7, no memory | 134 of 300 (44.7%) | 264 of 300 (88.0%) |
| V6, fade rate | 152 of 200 (76.0%) | 167 of 200 (83.5%) |

**In the field the stops move.** The mean empty rest voltage at the first and the last estimated month end, per cell (`ref_empty_v` of the monthly capacity files ÷ cells in series): on the LMO/NMC systems it changes by 01 -145, 02 -142, 03 -132, 04 -157, 05 -50, 06 -93 mV; on NMC 07, 08, 09, 10, 11, 12 and 13 and LFP 14, 15, 16, 17, 18, 19, 20 and 21 by at most 37 mV. The LMO/NMC systems are discharged to a lower relaxed voltage as they age.

| System | Set | Chemistry | Cells in series | First and last estimated month end | Empty rest voltage, V per cell (first → last) | Change, mV per cell | Full rest voltage change, mV per cell | Fade 0.1.0, points/year | Fade 0.2, points/year |
|---|---|---|---|---|---|---|---|---|---|
| 01 | development | LMO/NMC | 40 | Jul 2016 – Oct 2022 | 3.424 → 3.278 | -145 | +1 | -1.80 | -2.76 |
| 02 | held-out | LMO/NMC | 40 | Jan 2017 – Dec 2022 | 3.411 → 3.269 | -142 | -15 | -4.10 | -3.55 |
| 03 | held-out | LMO/NMC | 40 | Jun 2017 – Nov 2022 | 3.397 → 3.265 | -132 | +24 | -3.19 | -3.76 |
| 04 | development | LMO/NMC | 40 | Jul 2017 – Dec 2022 | 3.426 → 3.269 | -157 | +6 | -1.80 | -4.13 |
| 05 | held-out | LMO/NMC | 40 | Nov 2017 – Aug 2022 | 3.330 → 3.279 | -50 | +33 | -2.50 | -3.39 |
| 06 | held-out | LMO/NMC | 40 | Sep 2017 – Nov 2022 | 3.362 → 3.269 | -93 | +14 | -2.76 | -3.27 |
| 07 | development | NMC | 13 | Jan 2017 – Nov 2022 | 3.680 → 3.682 | +2 | -3 | -1.30 | -0.73 |
| 08 | held-out | NMC | 13 | Feb 2017 – Nov 2022 | 3.459 → 3.446 | -13 | -29 | -2.13 | -2.10 |
| 09 | held-out | NMC | 13 | Nov 2016 – Sep 2019 | 3.688 → 3.685 | -3 | -6 | -18.73 | – |
| 10 | held-out | NMC | 13 | Apr 2018 – Nov 2022 | 3.463 → 3.456 | -7 | -13 | -1.12 | +0.13 |
| 11 | development | NMC | 14 | Mar 2020 – Nov 2022 | 3.346 → 3.321 | -25 | +6 | -1.49 | -1.47 |
| 12 | held-out | NMC | 14 | Feb 2021 – Dec 2022 | 3.364 → 3.332 | -31 | +11 | +0.84 | -0.93 |
| 13 | held-out | NMC | 14 | Aug 2020 – Nov 2022 | 3.605 → 3.602 | -2 | -1 | -1.77 | -1.92 |
| 14 | development | LFP | 16 | Mar 2017 – Aug 2020 | 3.034 → 3.034 | +0 | +7 | -0.96 | -1.02 |
| 15 | held-out | LFP | 16 | Oct 2017 – Nov 2022 | 3.043 → 3.024 | -19 | +4 | -1.89 | -2.09 |
| 16 | held-out | LFP | 16 | Apr 2020 – Nov 2022 | 3.084 → 3.047 | -37 | +5 | -0.96 | -1.37 |
| 17 | development | LFP | 16 | May 2020 – Oct 2022 | 3.032 → 3.016 | -17 | +12 | +2.76 | +2.74 |
| 18 | held-out | LFP | 16 | Feb 2018 – Jun 2018 | 3.123 → 3.121 | -2 | +1 | +3.49 | +1.45 |
| 19 | development | LFP | 14 | Oct 2016 – Feb 2021 | 3.227 → 3.222 | -5 | -2 | -1.21 | – |
| 20 | held-out | LFP | 14 | Oct 2017 – Aug 2020 | 3.220 → 3.225 | +6 | +14 | -4.23 | -4.79 |
| 21 | held-out | LFP | 14 | Apr 2017 – Nov 2020 | 3.209 → 3.224 | +15 | +20 | -3.39 | -2.40 |

So the capacity between fixed voltages and the charge between the stops part. LMO/NMC fade, 0.1.0 → 0.2, points per year: 01 -1.80 → -2.76, 02 -4.10 → -3.55, 03 -3.19 → -3.76, 04 -1.80 → -4.13, 05 -2.50 → -3.39, 06 -2.76 → -3.27; held-out mean -3.14 → -3.49 (V5 passes in 0.1.0, passes in 0.2). On the field tests the band held 43 % of the held-out LMO/NMC and NMC tests in 0.1.0 and 29 % in 0.2 (V4 fails); their median error is 2.62 and 2.99 points (V2 passes in 0.1.0, passes in 0.2).

Neither number is wrong: they are two quantities. The 0.2 band holds the first and leaves out the drift of the stops. A band term for that drift is planned; it will be tested first on the pack simulator (besslib.sim), where the true drift is known, before it touches field data.

## Rebuild

```
pip install -e .[dev]
python validation/w7_cause.py              # X1
python validation/capacity_chains.py       # X7
python validation/run_capacity.py <id>     # capacity, one system
python validation/synthetic_coverage.py    # V6
python validation/synthetic_lag.py         # X3
python validation/check_capacity.py        # V2-V8
python validation/run_annex_vii.py <id>    # the record, one system
python validation/synthetic_phase3.py      # W2, W5, W7, W8
python validation/check_annex_vii.py       # W1-W10, X5
python validation/check_v02.py --systems all   # X0, X2, X4, X8-X10
python validation/make_v02_doc.py          # this page
```

The 0.1.0 files are read with `git show v0.1.0:<path>`. This page refuses results of any hash other than capacity `b3959941c19b` and record `d49cbfdcd5b6` (X1: the 0.1.0 hashes, by design).

## Source and credit

Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.
Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9 (CC BY-NC-ND 4.0): cited only; no figure or text of it is copied.
The values on this page are computed by besslib from that data.
Not a compliance certificate, not legal advice: besslib computes the Annex VII parameters; it does not certify them.
