# Known limits of version 0.3

Every number on this page is written by `validation/make_limits.py` from the result files, today's and those of release 0.1.0 (tag `v0.1.0`).

## Open in version 0.3

### 1. The capacity band does not hold the charge between the BMS stops

- **What:** Since version 0.2 the capacity is the capacity between a full and an empty rest at two relaxed voltages: the mean full and empty rest voltages of the memory time, which the record gives. A BMS stops at voltage limits under load. Its stops move as the cells age, so the charge between them is a different quantity. The band holds the first, not the second.
- **The number:** On 200 synthetic packs the monthly 90 % band held the charge between the stops at the date in 1524 of 4782 month ends (32 %); the estimate reads +0.63 points of nominal capacity above it on average. Against the capacity between the rest voltages it reports, the band held 3789 of 4562 (83 %), +0.12 points on average; that truth was chosen after the first results.
- **The rule that found it:** Share of month ends whose band holds the true charge between the stops at the date (release 0.1.0's rule), with the share against the capacity between the rest voltages beside it.
- **File:** `validation/results/synthetic_lag.csv`, `validation/results/checks_sim_v03.csv`
- **Status:** On the simulator, version 0.3 holds the capacity it is built for (10416 of 11031 (94.4%) month ends without drift), but a simulated capacity test lies in its band at 0.4 to 3.3 % of the test dates: the test's rate gives +1.25 to +2.27 points of the gap ([simulator](05_simulator.md#version-03-on-new-seeds)). No band term for the test's quantity is planned. The cause of the field misses is not verified ([version 0.3 page](06_version_0_3.md)).

### 2. Apparent charge loss (A4) is biased on synthetic packs

- **What:** The apparent charge loss reads below the true drain on synthetic packs: the band's share passes, the mean error lies outside version 0.3's limit.
- **The number:** The band held the true drain in 182 of 200 packs (91 %). The estimate is off by -0.08 mA on average (standard deviation 0.17 mA) on a true drain of 50 mA, just outside ±0.02 (2 sd/√200): a small bias remains. Release 0.1.0: the band held 87 of 200; +4.43 mA on average (standard deviation 2.34 mA); 194 of 200 packs read above the truth.
- **The rule that found it:** W7 row, synthetic packs: the band's share, and the mean and the standard deviation of the error (`w7_error_ma`) against the true drain (`w7_truth_ma`); version 0.3's rule adds the mean clause (85-95 % and mean error within 2 sd / sqrt(n) of zero: fail).
- **File:** `validation/results/checks_phase3.csv`, `validation/results/checks_v03.csv`, `validation/results/synthetic_phase3.csv`
- **Status:** Open: published as measured; no fix in 0.3.

### 3. Storage at a middle state of charge gets a wrong capacity without the pack's voltage limits

- **What:** Systems without regular full cycles need the partial-window form of the capacity fit (Plett's form). Version 0.3 refuses such storage when the pack's voltage limits are given; it cannot recognise it without them.
- **The number:** Do not use the capacity (A1) on storage that works at a middle state of charge unless the pack's documented voltage limits are given (`PackSpec.voltage_max_v`, `voltage_min_v`). With them, version 0.3 refuses: on simulated packs cycled daily between 25 and 75 % and never charged full, 200 of 200 packs with 0 estimated month ends and every reason 'not possible from this data', for NMC, LFP and the string. Without them it still takes the edges of the daily window for full and empty and reports a median 51.8 % (NMC), 51.0 % (LFP), 51.0 % (the string) of the true capacity, with the status `estimated` and the label "full and empty are the ends of the observed window; not checked against voltage limits".
- **The rule that found it:** The A1 estimate over the true capacity and the refusals, per setting, in case 5 of the pack simulator ([page](05_simulator.md#version-03-on-new-seeds)).
- **File:** `validation/results/checks_sim_v03.csv`, `validation/results/sim_v03/grid.csv`
- **Status:** Partly fixed: the refusal with documented limits is built; the partial-window form is not.

### 4. Apparent charge loss (A4) trails a new leak

- **What:** A4 is a weighted value over the memory time; a step in the true loss reaches it late.
- **The number:** On simulated packs with a cell leak the A4 band holds the true apparent charge loss at 3424 of 3549 (96.5%) month ends without a leak, 1869 of 3574 (52.3%) at 5 mA and 1033 of 3590 (28.8%) at 15 mA, before the onset and from 180 days after it. Version 0.3's band is narrower; the estimate weighs its history with a 180-day memory, so after a new leak it trails the change and the band no longer covers it.
- **The rule that found it:** T3a of the pack simulator, case 3, and the four-setting diagnosis `validation/diag_a4_band.py` ([page](05_simulator.md#version-03-on-new-seeds)).
- **File:** `validation/results/checks_sim_v03.csv`, `validation/results/diag_a4_band.csv`
- **Status:** Open: published as measured; no fix in 0.3.

### 5. The fade rate's band is too narrow on synthetic packs

- **What:** The capacity fade per year (`fade_rate`) carries a band that should hold the true fade.
- **The number:** On 200 synthetic packs the band of the fade rate held the true usable fade (-1.88) in 27.0% (rule 85-95 %: fail). The reading times move the rate's bias, the quadratic terms narrow its band; together the band is too narrow for the bias.
- **The rule that found it:** V6, and the five-setting diagnosis `validation/diag_fade_band.py` ([version 0.3 page](06_version_0_3.md#why-v6-fell)).
- **File:** `validation/results/synthetic_coverage.csv`, `validation/results/diag_fade_band.csv`
- **Status:** Open: published as measured; no fix in 0.3.

## Fixed in version 0.2

Found in release 0.1.0 and fixed in version 0.2: the same rule, applied to today's results, still finds no fault.

### 1. Capacity at or below zero said estimated

- **What:** A capacity at or below zero is not a measurement, yet the status said `estimated`; it should say `not possible from this data`.
- **Before:** Release 0.1.0: system 09, -35.25 Ah `estimated`.
- **Now:** Records with a capacity at or below zero and the status `estimated`: no record. Month ends of the capacity tables with the same fault: 0. System 09 now says `not possible from this data` at 27 month ends (27 with crossed levels).
- **The rule:** A1 value at or below zero and A1 status `estimated`, over every record; the same rule over the month ends of the capacity tables, for information.
- **File:** `validation/results/phase3/system_*_record.json`, `validation/results/phase2/system_*_monthly.csv`

### 2. Power capability estimated while resistance was not possible

- **What:** A2 is derived from the resistance, so it should not be `estimated` where A5 is `not possible`.
- **Before:** Release 0.1.0: system 08 reads 90 %; system 18 reads 102 %; system 19 reads 102 %; system 20 reads 144 %.
- **Now:** Power capability `estimated` while the resistance is `not possible`: none.
- **The rule:** A2 status `estimated` and A5 status starting with `not possible`, over every record.
- **File:** `validation/results/phase3/system_*_record.json`

## Limits already published on the pages

- The method needs regular full cycles, as in home storage; it does not suit mid-state-of-charge grid duty. ([page](02_capacity_validation.md#limits))
- The capacity checks V3 (LFP), V4 (the band), V5 NMC, V5 LFP and V6 failed. ([page](02_capacity_validation.md#why-a-check-failed))
- A4, the apparent charge loss, is an upper bound for self-discharge, not a measure of it. ([page](03_annex_vii.md#method-and-limits-per-field))
- A3, the efficiency, is DC at the battery terminals, not the AC efficiency of a plant. ([page](03_annex_vii.md#method-and-limits-per-field))
- A5 is a 1-second resistance, not the ohmic value of impedance spectroscopy. ([page](03_annex_vii.md#method-and-limits-per-field))
- The first year of a record gives no capacity estimate, because the full and empty levels need a year of history. ([page](02_capacity_validation.md#limits))
- The checks W2 (value) and W6 failed and W3 was not possible. ([page](03_annex_vii.md#result))

## Source and credit

Data: Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in capacity estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.
Paper: Figgener, J. et al., Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9 (CC BY-NC-ND 4.0): cited only; no figure or text of it is copied.
