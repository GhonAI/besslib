# Known limits of version 0.1

Every number on this page is written by `validation/make_limits.py` from the result files.

## To be fixed in version 0.2

### 1. Apparent charge loss (A4) is biased

- **What:** The offset current of the capacity fit, and with it the apparent charge loss, reads too high on synthetic packs.
- **The number:** The band held the true drain in 87 of 200 packs (44 %). The estimate is off by +4.43 mA on average (standard deviation 2.34 mA) on a true drain of 50 mA; 194 of 200 packs read above the truth.
- **The rule that found it:** W7 row, synthetic packs: the mean and the standard deviation of the error (`w7_error_ma`) against the true drain (`w7_truth_ma`).
- **File:** `validation/results/checks_phase3.csv`, `validation/results/synthetic_phase3.csv`
- **To be fixed in version 0.2.**

### 2. Capacity at or below zero still says estimated

- **What:** A capacity at or below zero is not a measurement, yet the status says `estimated`; it should say `not possible from this data`.
- **The number:** Records with a capacity at or below zero and the status `estimated`: system 09: -35.25 Ah. Month ends of the capacity tables with the same fault: 4 in system 09.
- **The rule that found it:** A1 value at or below zero and A1 status `estimated`, over every record; the same rule over the month ends of the capacity tables, for information.
- **File:** `validation/results/phase3/system_*_record.json`, `validation/results/phase2/system_*_monthly.csv`
- **To be fixed in version 0.2.**

### 3. The capacity band leaves out the lag

- **What:** The estimate is causal and weighs the past, so on a fading pack it lags the capacity at the date; the band does not include the lag.
- **The number:** On 200 synthetic packs the monthly 90 % band held the true usable capacity at the date in 1386 of 4782 month ends (29 %), and at the weighted mean age of the pairs in memory in 4227 (88 %). The estimate reads +1.05 points of nominal capacity above the truth at the date on average.
- **The rule that found it:** Share of month ends whose band holds the true usable capacity at the date, against the share at the weighted mean age of the pairs.
- **File:** `validation/results/synthetic_lag.csv`
- **To be fixed in version 0.2.**

### 4. Power capability estimated while resistance is not possible

- **What:** A2 is derived from the resistance, so it should not be `estimated` where A5 is `not possible`.
- **The number:** Power capability `estimated` while the resistance is `not possible`: system 08 reads 90 %; system 18 reads 102 %; system 19 reads 102 %; system 20 reads 144 %.
- **The rule that found it:** A2 status `estimated` and A5 status starting with `not possible`, over every record.
- **File:** `validation/results/phase3/system_*_record.json`
- **To be fixed in version 0.2.**

## Limits already published on the pages

- The method needs regular full cycles, as in home storage; it does not suit mid-state-of-charge grid duty. ([page](03_annex_vii.md#method-and-limits-per-field))
- The capacity checks V3 (LFP) and V4 (the band) failed. ([page](02_capacity_validation.md#why-a-check-failed))
- A4, the apparent charge loss, is an upper bound for self-discharge, not a measure of it. ([page](03_annex_vii.md#method-and-limits-per-field))
- A3, the efficiency, is DC at the battery terminals, not the AC efficiency of a plant. ([page](03_annex_vii.md#method-and-limits-per-field))
- A5 is a 1-second resistance, not the ohmic value of impedance spectroscopy. ([page](03_annex_vii.md#method-and-limits-per-field))
- The first year of a record gives no capacity estimate, because the full and empty levels need a year of history. ([page](02_capacity_validation.md#limits))
- The checks W2 (value), W6 and W7 failed and W3 was not possible. ([page](03_annex_vii.md#result))
