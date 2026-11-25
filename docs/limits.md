# Known limits of version 0.2

Every number on this page is written by `validation/make_limits.py` from the result files, today's and those of release 0.1.0 (tag `v0.1.0`).

## Open in version 0.2

### 1. The capacity band does not hold the charge between the BMS stops

- **What:** Since version 0.2 the capacity is the capacity between a full and an empty rest at two relaxed voltages: the mean full and empty rest voltages of the memory time, which the record gives. A BMS stops at voltage limits under load. Its stops move as the cells age, so the charge between them is a different quantity. The band holds the first, not the second.
- **The number:** On 200 synthetic packs the monthly 90 % band held the charge between the stops at the date in 2278 of 4782 month ends (48 %); the estimate reads +0.47 points of nominal capacity above it on average. Against the capacity between the rest voltages it reports, the band held 4248 of 4562 (93 %), -0.04 points on average; that truth was chosen after the first results. On the same seeds with one state-of-charge path (a pack whose BMS copy equals its plant) it held 3827 of 4569 (84 %); [simulator](05_simulator.md#the-rerun-on-one-state-of-charge-path).
- **The rule that found it:** Share of month ends whose band holds the true charge between the stops at the date (release 0.1.0's rule), with the share against the capacity between the rest voltages beside it.
- **File:** `validation/results/synthetic_lag.csv`
- **Status:** On simulated packs whose test stop falls with the daily stop, the drift of the stops does not carry the gap to a capacity test: estimate less test is +1.72 points without drift and +1.95 at 157 mV per cell ([simulator](05_simulator.md#6-a-drifting-stop)). With other test set-ups it moves: -0.72 to -1.74 at C/10, +15.80 to +10.71 with one stop for tests and daily operation. No band term for it is planned. The cause of the field misses is not verified. See the [version 0.2 page](04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with).

### 2. Storage at a middle state of charge gets a wrong capacity

- **What:** Version 0.2 does not recognise storage that works in partial windows, and does not refuse it. Systems without regular full cycles need the partial-window form of the capacity fit (Plett's form).
- **The number:** Do not use the capacity (A1) on storage that works at a middle state of charge. Version 0.2 takes the edges of the daily window for full and empty and reports the charge between them as the capacity, with the status `estimated`. On simulated packs cycled daily between 25 and 75 % it reports a median 51 to 56 % of the true capacity (9 settings of 200 packs: a full charge every week, every month or never).
- **The rule that found it:** The A1 estimate over the true capacity, median per setting, in case 5 of the pack simulator ([page](05_simulator.md#5-grid-duty-at-middle-state-of-charge)).
- **File:** `validation/results/sim/grid.csv`
- **Status:** Open: neither a refusal nor the partial-window form is built.

## Fixed in version 0.2

Found in release 0.1.0 and fixed in version 0.2: the same rule, applied to the 0.2 results, no longer finds the fault.

### 1. Apparent charge loss (A4) was biased

- **What:** The offset current of the capacity fit, and with it the apparent charge loss, read too high on synthetic packs.
- **Before:** Release 0.1.0: the band held 87 of 200; +4.43 mA on average (standard deviation 2.34 mA); 194 of 200 packs read above the truth.
- **Version 0.2:** The band held the true drain in 188 of 200 packs (94 %). The estimate is off by -0.03 mA on average (standard deviation 0.19 mA) on a true drain of 50 mA, just outside ±0.03 (2 sd/√200): a small bias remains.
- **The rule:** W7 row, synthetic packs: the band's share, and the mean and the standard deviation of the error (`w7_error_ma`) against the true drain (`w7_truth_ma`).
- **File:** `validation/results/checks_phase3.csv`, `validation/results/synthetic_phase3.csv`

### 2. Capacity at or below zero said estimated

- **What:** A capacity at or below zero is not a measurement, yet the status said `estimated`; it should say `not possible from this data`.
- **Before:** Release 0.1.0: system 09, -35.25 Ah `estimated`.
- **Version 0.2:** Records with a capacity at or below zero and the status `estimated`: no record. Month ends of the capacity tables with the same fault: 0. System 09 now says `not possible from this data` at 28 month ends (27 with crossed levels, 1 with a wrong-sign pair).
- **The rule:** A1 value at or below zero and A1 status `estimated`, over every record; the same rule over the month ends of the capacity tables, for information.
- **File:** `validation/results/phase3/system_*_record.json`, `validation/results/phase2/system_*_monthly.csv`

### 3. Power capability estimated while resistance was not possible

- **What:** A2 is derived from the resistance, so it should not be `estimated` where A5 is `not possible`.
- **Before:** Release 0.1.0: system 08 reads 90 %; system 18 reads 102 %; system 19 reads 102 %; system 20 reads 144 %.
- **Version 0.2:** Power capability `estimated` while the resistance is `not possible`: none.
- **The rule:** A2 status `estimated` and A5 status starting with `not possible`, over every record.
- **File:** `validation/results/phase3/system_*_record.json`

## Limits already published on the pages

- The method needs regular full cycles, as in home storage; it does not suit mid-state-of-charge grid duty. ([page](02_capacity_validation.md#limits))
- The capacity checks V3 (LFP), V4 (the band), V5 NMC and V6 failed. ([page](02_capacity_validation.md#why-a-check-failed))
- A4, the apparent charge loss, is an upper bound for self-discharge, not a measure of it. ([page](03_annex_vii.md#method-and-limits-per-field))
- A3, the efficiency, is DC at the battery terminals, not the AC efficiency of a plant. ([page](03_annex_vii.md#method-and-limits-per-field))
- A5 is a 1-second resistance, not the ohmic value of impedance spectroscopy. ([page](03_annex_vii.md#method-and-limits-per-field))
- The first year of a record gives no capacity estimate, because the full and empty levels need a year of history. ([page](02_capacity_validation.md#limits))
- The checks W2 (value) and W6 failed and W3 was not possible. ([page](03_annex_vii.md#result))

## Source and credit

Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.
Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9 (CC BY-NC-ND 4.0): cited only; no figure or text of it is copied.
