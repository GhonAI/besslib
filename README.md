# besslib

State of health from battery storage operating data — with confidence bands, in the fields of the
EU Battery Regulation (2023/1542, Annex VII).

**Version 0.1.0, 20 November 2026.** It computes the Annex VII parameters; it does not certify them. It is not a
compliance certificate and not legal advice.

![Capacity of system 05 in Ah from August 2016 to August 2022: a line with its 90 % band from September 2017, and the 4 field capacity tests as dots.](https://raw.githubusercontent.com/GhonAI/besslib/v0.1.0/docs/img/00_readme_05.png)

*System 05 (LMO/NMC, 15 Ah nominal): the capacity from operating data, at the end of each month, with its 90 % band, against the 4 field capacity tests (dots). 2 of the 4 tests have an estimate at their date, and the band holds the test value in 2 of them. The first estimate is at the end of September 2017: the first year gives none (grey). Hollow dots: tests without an estimate. 17 month ends without data are left blank.*

Checked on open field data: 21 home storage systems, 1,269 months, 60 field capacity tests.
On the 14 systems the method was not tuned on:

- LMO/NMC and NMC, 28 tests: median error 2.62 points of nominal capacity (target ≤ 3): **passed**.
- LFP, 10 tests: 5 have no estimate (target ≤ 5 points): **failed**.
- The 90 % band held the measured value in 43 % of 28 tests (target 80 %): **failed**. [Why →](https://github.com/GhonAI/besslib/blob/v0.1.0/docs/limits.md)

Every number on this page is written by `validation/make_readme.py` from a results file in `validation/results/`.

## Install and run

```
pip install besslib
```

```python
import besslib
reduced, spec = besslib.io.read_sample()     # system 05, Aug 2016 – Sep 2022, CC BY 4.0
record = besslib.soh.annex_vii(None, spec, reduced=reduced)
print(record.to_frame())
```

Output (the frame, as a table; resistance in ohm, efficiency and power capability as shares):

| code | field | value | low | high | unit | events | status |
|---|---|---|---|---|---|---|---|
| A1 | the remaining capacity | 11.52 | 11.41 | 11.64 | Ah | 210.4 | estimated |
| A2 | where possible, the remaining power capability | 0.7262 | 0.6553 | 0.797 | share of the first day | 283 | estimated |
| A3 | where possible, the remaining round trip efficiency | 0.9605 | 0.9602 | 0.9608 | share | 97.41 | estimated |
| A4 | the evolution of self-discharging rates |  |  |  | mA | 210.4 | not possible from this data: the band of the offset current could not be computed |
| A5 | where possible, the ohmic resistance | 0.1406 | 0.1341 | 0.1471 | ohm | 55.67 | estimated |
| B1 | the date of manufacture of the battery and, where appropriate, the date of putting into service |  |  |  | date | 0 | not possible from this data: not in the dataset |
| B2 | the energy throughput | 5646 | 5616 | 5676 | kWh | 1607 | estimated |
| B3 | the capacity throughput | 37250 | 37070 | 37420 | Ah | 1607 | estimated |
| B4 | the tracking of harmful events, such as the number of deep discharge events, time spent in extreme temperatures, time spent charging in extreme temperatures | 566 | 566 | 566 | events | 566 | estimated |
| B5 | the number of full equivalent charge-discharge cycles | 1242 | 1236 | 1247 | cycles | 1607 | estimated |

The sample is system 05, LMO/NMC, 15 Ah nominal, from August 2016 to September 2022: three reduced tables (one row per day, per current step and per rest candidate, not the raw samples), 6.8 MB in the package; the raw archive is 856 MB. The sample's data is CC BY 4.0, not BSD-3-Clause; its credit is in
[`NOTICE.md`](https://github.com/GhonAI/besslib/blob/v0.1.0/src/besslib/data/sample_05/NOTICE.md) and in each file's metadata.

The quickstart runs without the capacity-test mask, as a user would. The published record of the same system leaves
the test windows out ([Annex VII page](https://github.com/GhonAI/besslib/blob/v0.1.0/docs/03_annex_vii.md)); a test checks that the sample with that mask
gives the published record exactly.

**Your own data:** a pandas DataFrame in the frame format of
[`besslib/frames.py`](https://github.com/GhonAI/besslib/blob/v0.1.0/src/besslib/frames.py) (columns, units, sign convention) and a `besslib.PackSpec`;
then `besslib.soh.annex_vii(frame, spec)`. **The full field data** of one system, from the Zenodo archive:

```
git clone https://github.com/GhonAI/besslib && cd besslib
pip install -e .[dev]
# downloads Data_ID_05.zip (856 MB), reduces it, deletes it, and writes
# validation/results/phase3/system_05_record.json and system_05_yearly.csv
python validation/run_annex_vii.py --held-out 5
```

## What it computes

Each field carries a value, a band, the number of events it rests on, the method with its source, and a status.
Where the data cannot give a value, the status says `not possible from this data` with the reason. Nothing is
filled in silently.

| Code | Annex VII (quoted) | What besslib reports | Method and source |
|---|---|---|---|
| A1 | "the remaining capacity" | remaining capacity between the system's own end of charge and end of discharge | capacity between full and empty rests by coulomb counting, fitted with the offset current (Figgener et al., Nature Energy 9, 1438 (2024), doi:10.1038/s41560-024-01620-9) |
| A2 | "where possible, the remaining power capability" | power capability share, derived from the 1-second resistance, not measured | ratio of the resistance on the first day to the resistance at the date, from dV/dI of current steps between consecutive samples (Barai et al., Sci. Rep. 8, 21 (2018), doi:10.1038/s41598-017-18424-5; Schweiger et al., Sensors 10, 5604 (2010), doi:10.3390/s100605604); no power test |
| A3 | "where possible, the remaining round trip efficiency" | round-trip efficiency (DC, at the battery terminals) over full-to-full loops | energy out / energy in over closed loops between full rests (Regulation (EU) 2023/1542, Annex IV (6)); band: the ratio over these loops had each closed exactly, from the error of the state of charge at their end rests, estimated as the mean lag-2 product of the loop scores minus the mean lag-1 product |
| A4 | "the evolution of self-discharging rates" | apparent charge loss (an upper bound for self-discharge) | minus the offset current of the capacity fit (Figgener et al., Nature Energy 9, 1438 (2024), doi:10.1038/s41560-024-01620-9); band with the current sensor's gain error |
| A5 | "where possible, the ohmic resistance" | 1-second resistance, pack temperature band at 25 degrees C | dV/dI of current steps between consecutive samples (Barai et al., Sci. Rep. 8, 21 (2018), doi:10.1038/s41598-017-18424-5; Schweiger et al., Sensors 10, 5604 (2010), doi:10.3390/s100605604) |
| B1 | "the date of manufacture of the battery and, where appropriate, the date of putting into service" | date of manufacture; date of putting into service | the pack's documents (PackSpec), passed through |
| B2 | "the energy throughput" | energy throughput (in + out) since the first day of data | trapezoid sums of charging and discharging power over the measured time; band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width (JCGM 100:2008, 4.3.7) |
| B3 | "the capacity throughput" | capacity throughput (in + out) since the first day of data | trapezoid sums of charging and discharging current over the measured time; band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width (JCGM 100:2008, 4.3.7) |
| B4 | "the tracking of harmful events, such as the number of deep discharge events, time spent in extreme temperatures, time spent charging in extreme temperatures" | discharges to the system's own end-of-discharge level (observed; not a limit count) | measured hours per pack-temperature band and mode; discharges that reached the system's own end-of-discharge level (empty rests); voltage extremes; counts against limits when limits are given |
| B5 | "the number of full equivalent charge-discharge cycles" | full equivalent cycles since the first day of data | capacity throughput / (2 x reference capacity); band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width (JCGM 100:2008, 4.3.7) |

## How it was checked

Thresholds were tuned on 7 development systems and frozen; the results below come from the
14 held-out systems, run once, and from synthetic packs with a known truth. A failed check is published as
failed, with its reason.

Configuration hashes: capacity `109e9eb1efa5`, record `c2f7f545cebb`. Every result file carries them.

**Capacity** ([page](https://github.com/GhonAI/besslib/blob/v0.1.0/docs/02_capacity_validation.md); the reference tests: [page](https://github.com/GhonAI/besslib/blob/v0.1.0/docs/01_reference.md))

| Check | Scope | Value | Pass if | Result |
|---|---|---|---|---|
| V2 | held-out LMO/NMC and NMC | median \|error\| 2.62 points (22/28 estimated) | <= 3 points | passed |
| V2 info | same, without 03/3 and 08/1 | median \|error\| 2.62 points (26 tests) | information | – |
| V3 | held-out LFP | median \|error\| inf points; median error +4.80 (5/10 estimated) | <= 5 points, bias shown | failed |
| V4 | held-out LMO/NMC and NMC | band held the test in 43% of 28 | >= 80 % | failed |
| V4 info | held-out LFP | band held the test in 10% of 10 | information | – |
| V4 info | same as V4, without 03/3 and 08/1 | band held the test in 42% of 26 | information | – |
| V5 | held-out LMO/NMC (4 systems) | mean fade -3.14 points/year; paper -3.1 | within +-0.5 | passed |
| V5 info | all LMO/NMC (6 systems) | mean fade -2.69 points/year | information | – |
| V5 | held-out NMC (5 systems) | mean fade -4.58 points/year; paper -1.9 | within +-0.5 | failed |
| V5 info | all NMC (7 systems) | mean fade -3.67 points/year | information | – |
| V5 | held-out LFP (5 systems) | mean fade -1.39 points/year; paper -2.0 | within +-0.5 | failed |
| V5 info | all LFP (8 systems) | mean fade -0.80 points/year | information | – |
| V6 | 200 synthetic packs | band held the true usable fade (-1.88) in 89.5% | 85-95 % | passed |
| V7 | core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2) | max relative difference 2.0e-15 | <= 1e-6 | passed |
| V8 | 21 systems | one command per system: python validation/run_capacity.py <id> | all 21 rebuilt with one hash | passed |

**The other Annex VII fields** ([page](https://github.com/GhonAI/besslib/blob/v0.1.0/docs/03_annex_vii.md))

| Check | Scope | Value | Pass if | Result |
|---|---|---|---|---|
| W1 info | development records | 7 of 7 records; 0 fields break the rule | information | – |
| W1 | held-out records | 14 of 14 records; 0 fields break the rule | 0 | passed |
| W2 | 200 synthetic packs: resistance at the weighted mean age of the days in memory | fails under each truth. same-days truth 200 of 200: too wide, because that truth shares every day with the estimate, so the day-to-day scatter the statistical half-width stands for (median 0.62% of the value) is not in the difference; the largest difference is 0.77%, and the systematic half-width alone (median 0.53%) holds 187 of 200. At the date 0 of 200: the estimate lags the growing resistance (the value at the date is a median 2.3% above it). At 25 degrees C 90 of 200: the days in the band read a median +0.9% against 25 degrees C, and the estimator has no temperature model | 85-95 % | failed |
| W2 | 200 synthetic packs: resistance growth per year, whole record, against the simulator's known growth (median 4.93 % per year at the fit's origin; this truth was chosen after the first results) | band held the truth in 184 of 200 (92.0%); same-days truth (information): 199 of 200 (99.5%) | 85-95 % | passed |
| W5 | 200 synthetic packs: round-trip efficiency, full-to-full loops | band held the truth in 176 of 200 (88.0%) | 85-95 % | passed |
| W7 | 200 synthetic packs: apparent charge loss (hidden drain) | band held the truth in 87 of 200 (43.5%) | 85-95 % | failed |
| W8 | 200 synthetic packs: Ah, kWh, cycles; planted events (pack B: current gain error only) | largest relative error 0.498%; events exact on 200 of 200; 200 of 200 packs within the rule | <= 0.6 % and exact, every pack | passed |
| W8 info | 200 synthetic packs: the same on pack B with current and voltage noise | largest relative error 0.498%; events exact on 189 of 200; 189 of 200 packs within the rule | information | – |
| W3 info | development capacity tests | median \|difference\| 21.9 % over 12 of 22 tests with a reference; median difference by chemistry {'LFP': 64.1, 'LMO/NMC': -8.2, 'NMC': -11.5} | information | – |
| W3 | held-out capacity tests | 18 of 38 tests have a reference: not possible | >= 30 references, then <= 20 % | not possible |
| W4 info | development: neighbouring 5-degree bands: the colder reads higher | 59 of 68 (87%) | information | – |
| W6 info | development: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 24 of 36 (67%) | information | – |
| W4 | held-out: neighbouring 5-degree bands: the colder reads higher | 112 of 120 (93%) | >= 80% | passed |
| W6 | held-out: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 47 of 64 (73%) | >= 80% | failed |
| W9 | core against batch, resistance: 240 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 3.6e-14 (64-bit); 2.6e-06 (32-bit, information) | <= 1e-6 | passed |
| W9 | core against batch, efficiency: 10 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 0.0e+00 (64-bit); 1.3e-06 (32-bit, information) | <= 1e-6 | passed |
| W9 | daily counters against rest counters: 138814 rests, systems [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21] | max relative difference 8.7e-13 (throughput, system 13) | <= 1e-6 | passed |
| W10 | capacity settings and committed phase 2 files | hash 109e9eb1efa5; 0 of 60 committed files differ | 0 differences | passed |
| W10 | phase 2 rebuilt for 21 systems | 0 of 60 files differ | 0 differences | passed |
| W10 | records rebuilt twice for 21 systems | 0 differences over 42 files | 0 differences | passed |

## Limits

1. **Apparent charge loss (A4) is biased.** The band held the true drain in 87 of 200 packs (44 %).
2. **Capacity at or below zero still says estimated.** Records with a capacity at or below zero and the status `estimated`: system 09: -35.25 Ah.
3. **The capacity band leaves out the lag.** On 200 synthetic packs the monthly 90 % band held the true usable capacity at the date in 1386 of 4782 month ends (29 %), and at the weighted mean age of the pairs in memory in 4227 (88 %).
4. **Power capability estimated while resistance is not possible.** Power capability `estimated` while the resistance is `not possible`: system 08 reads 90 %; system 18 reads 102 %; system 19 reads 102 %; system 20 reads 144 %.
5. Already published on the pages: the method needs regular full cycles, the first year of a record gives no capacity estimate, apparent charge loss is an upper bound for self-discharge, efficiency is DC at the battery terminals, and resistance is a short-pulse value, not the impedance-spectroscopy one.

All limits, with their numbers and pages: [`docs/limits.md`](https://github.com/GhonAI/besslib/blob/v0.1.0/docs/limits.md).

## Rebuild every number

```
git clone https://github.com/GhonAI/besslib && cd besslib
pip install -e .[dev]
python validation/build_reference.py        # the field capacity tests, from the Zenodo archives
python validation/run_capacity.py <id>      # capacity, one system
python validation/run_annex_vii.py <id>     # the Annex VII record, one system
python validation/make_readme.py            # this page
```

The scripts download the archives one at a time and delete them after reducing.

## Cite

Use GitHub's "Cite this repository" ([`CITATION.cff`](https://github.com/GhonAI/besslib/blob/v0.1.0/CITATION.cff)), and cite the data.

## Data and credit

Data: Figgener, J. et al., Data for: Multi-year field measurements of home storage systems and their use in capacity estimation, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.

Paper: Figgener, J. et al., Multi-year field measurements of home storage systems and their use in capacity estimation, Nature Energy 9, 1438-1447 (2024), https://doi.org/10.1038/s41560-024-01620-9, licence CC BY-NC-ND 4.0: cited, nothing copied.

## Contact

Islam Ghonaiem, ghonaiem@vgtai.com. Issues: [GitHub](https://github.com/GhonAI/besslib/issues).

## Licence

Code: BSD-3-Clause, see [`LICENSE`](https://github.com/GhonAI/besslib/blob/v0.1.0/LICENSE). The shipped sample: CC BY 4.0 (see above).
