# besslib

State of health from battery storage operating data — with confidence bands, in the fields of the
EU Battery Regulation (2023/1542, Annex VII) — and a pack simulator that tests it, and your BMS or
energy-management code, against a known truth. Checked on field data only on home storage that reaches full and
empty regularly. On mid-state-of-charge duty it refuses when the pack's voltage limits are given; without them it
reads about half the true capacity (51.0 to 51.8 %, simulated packs).

**Version 0.3.0, 26 November 2026.** It computes the Annex VII parameters; it does not certify them. It is not a
compliance certificate and not legal advice.

Author: Islam Ghonaiem, ghonaiem@vgtai.com — questions on the method and its failures are welcome.

![Capacity of system 05 in Ah from August 2016 to August 2022: a line with its 90 % band from September 2017, and the 4 field capacity tests as dots.](https://raw.githubusercontent.com/GhonAI/besslib/v0.3.0/docs/img/00_readme_05.png)

*System 05 (LMO/NMC, 15 Ah nominal): the capacity from operating data, at the end of each month, with its 90 % band, against the 4 field capacity tests (dots). 2 of the 4 tests have an estimate at their date, and the band holds the test value in 2 of them. The first estimate is at the end of September 2017: the first year gives none (grey). Hollow dots: tests without an estimate. 17 month ends without data are left blank.*

Checked on open field data: 21 home storage systems, 1,269 months, 60 field capacity tests.
On the 14 held-out systems (not used to tune any threshold; seen at releases 0.1.0 and 0.2.0, so these numbers are a third look, not a blind test):

- LMO/NMC and NMC, 28 tests: median error 2.62 points of nominal capacity, a test without an estimate counted as infinite (target ≤ 3): **passed**; over the 22 tests with an estimate, 1.61 points.
- LFP, 10 tests: 5 have no estimate (2 in the first year, 3 with too few full-to-empty pairs) and the other 5 read a median 4.62 points high (target: median error ≤ 5 points, a test without an estimate counted as infinite): **failed**.
- The 90 % band held the measured value in 32 % of 28 tests (target 80 %): **failed**. [Why →](https://github.com/GhonAI/besslib/blob/main/docs/limits.md)

**Version 0.3** reads the capacity with two quadratic voltage terms and the rests' reading times, and refuses mid-range storage against documented voltage limits. On the 28 held-out LMO/NMC and NMC field tests: median error 1.54 → 1.61 points over the 22 with an estimate (0.2: 22); the band held 29 % → 32 %. The fade rate got worse: on the 4 held-out LFP systems the mean is -2.73 points/year against the paper's -2.0 (0.2: -1.84 on 5, passed; now failed), and the band held the synthetic fade in 27.0 % of 200 packs (0.2: 76.0 %; failed). [What changed →](https://github.com/GhonAI/besslib/blob/main/docs/06_version_0_3.md)

**What the data showed.** On the 6 LMO/NMC systems the relaxed voltage of the empty rests fell by 50 to 157 mV per cell between the first and the last month end that has one: the systems were discharged to a lower voltage as they aged (cause not verified). The cause of the field misses is not verified; on simulated packs the drift does not carry them (below). [Data →](https://github.com/GhonAI/besslib/blob/main/docs/04_version_0_2.md#what-the-capacity-measures-and-what-the-checks-compare-it-with)

**besslib.sim: a battery pack that fails on demand, with its truth.** Six cases, each with its truth file: overheating, cell imbalance, a failing cell, the cold, grid duty at mid state of charge, a drifting discharge stop. It writes the frames besslib reads, and serves the pack's BMS over SunSpec Modbus TCP (models 1, 802, 803 and 804), checked with the SunSpec Alliance's own client (pysunspec2).

```
pip install "besslib[modbus]"
python -m besslib.sim serve --case failing-cell --port 5020 --speed 100 --out truth/
# your EMS reads the BMS at localhost:5020; truth/ keeps what really happened
```

What it showed on 200 packs per setting, seeds 2000–2199 (no fix was designed on them):

- A1's band holds the capacity it is built for (between two rest voltages) at 10,416 of 11,031 (94.4%) month ends (target 85-95 %, mean error within its limit): **passed**; 0.2 held 8,068 of 10,964 (73.6%) (seeds 1000–1199). Width 0.694 points (0.2: 0.695).
- With the discharge stop drifting down 157 mV per cell, the band holds that capacity at 843 of 1000 test dates (84.3%) (target 85–95 %): **failed** at 100 mV and 157 mV (849 and 843 of 1000; 850 pass), passed at 0 mV and 50 mV.
- A simulated capacity test lies in the band at 0.4 to 3.3 % of test dates: the test measures the charge between the stops at its rate, a different quantity; the rate gives +1.25 to +2.27 points of the miss, the drift -0.09 to +0.70. [Why →](https://github.com/GhonAI/besslib/blob/main/docs/05_simulator.md#version-03-on-new-seeds)
- Grid duty that never charges full: with the pack's documented voltage limits it refuses every month on 200 of 200 packs: **passed**; without them it reads a median 51.0 to 51.8 % of the true capacity: **failed** (labelled on the record).
- Other failed checks include: with cell imbalance the band holds 89.0% of month ends but reads a mean 0.063 Ah low (limit 0.036); A4's band after a 5 mA leak holds 52.3 % of month ends (0.2: 79.0 %), and on 200 packs its mean error is -0.084 mA (limit ±0.024); on 2 of 22 settings 0.3 estimates fewer month ends than 0.2 (5 of 3,584 and 1 of 7). [All checks →](https://github.com/GhonAI/besslib/blob/main/docs/05_simulator.md#version-03-on-new-seeds)

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
| A1 | the remaining capacity | 11.42 | 11.36 | 11.47 | Ah | 210.4 | estimated |
| A2 | where possible, the remaining power capability | 0.7262 | 0.6553 | 0.797 | share of the first day | 283 | estimated |
| A3 | where possible, the remaining round trip efficiency | 0.9605 | 0.9602 | 0.9608 | share | 97.41 | estimated |
| A4 | the evolution of self-discharging rates | 5.776 | 5.016 | 6.536 | mA | 210.4 | estimated |
| A5 | where possible, the ohmic resistance | 0.1406 | 0.1341 | 0.1471 | ohm | 55.67 | estimated |
| B1 | the date of manufacture of the battery and, where appropriate, the date of putting into service |  |  |  | date | 0 | not possible from this data: not in the dataset |
| B2 | the energy throughput | 5646 | 5616 | 5676 | kWh | 1607 | estimated |
| B3 | the capacity throughput | 37250 | 37070 | 37420 | Ah | 1607 | estimated |
| B4 | the tracking of harmful events, such as the number of deep discharge events, time spent in extreme temperatures, time spent charging in extreme temperatures | 566 | 566 | 566 | events | 566 | estimated |
| B5 | the number of full equivalent charge-discharge cycles | 1242 | 1236 | 1247 | cycles | 1607 | estimated |

Each band is a 90 % band from its field's method (fit scatter, sensor accuracy or both; see the method column below). On the 28 held-out LMO/NMC and NMC field tests the capacity band held 32 %, so a band is not the whole uncertainty. For A1, A3, A4 and A5, "events" is a count weighted by the 180-day memory, hence the decimals; for A2 and B2–B5 it counts days, for B4 discharges. A2 is relative to the first day of the resistance fit (2017-03-16), not to the system's installation (2014-11-21, in B1's details; B1 is not possible because the date of manufacture is missing).

The sample is system 05, LMO/NMC, 15 Ah nominal, from August 2016 to September 2022: three reduced tables (one row per day, per current step and per rest candidate, not the raw samples), 6.8 MB in the package; the raw archive is 856 MB. The sample's data is CC BY 4.0, not BSD-3-Clause; its credit is in
[`NOTICE.md`](https://github.com/GhonAI/besslib/blob/v0.3.0/src/besslib/data/sample_05/NOTICE.md) and in each file's metadata.

The quickstart runs without the capacity-test mask, as a user would. The published record of the same system leaves
the test windows out ([Annex VII page](https://github.com/GhonAI/besslib/blob/v0.3.0/docs/03_annex_vii.md)); a test checks that the sample with that mask
gives the published record exactly.

**Your own data:** a pandas DataFrame in the frame format of
[`besslib/frames.py`](https://github.com/GhonAI/besslib/blob/v0.3.0/src/besslib/frames.py) (columns, units, sign convention) and a `besslib.PackSpec`;
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
| A1 | "the remaining capacity" | remaining capacity between a full and an empty rest at two stated relaxed voltages (the record gives both); not the charge between the BMS stops; full and empty are the ends of the observed window; not checked against voltage limits | capacity between full and empty rests by coulomb counting, fitted with the offset current and the relaxed voltage of each rest, a linear and a quadratic term at each end (Figgener et al., Nature Energy 9, 1438 (2024), doi:10.1038/s41560-024-01620-9); read at the mean full and empty rest voltages of the memory time, which the record gives |
| A2 | "where possible, the remaining power capability" | power capability share, derived from the 1-second resistance, not measured | ratio of the resistance on the first day to the resistance at the date, from dV/dI of current steps between consecutive samples (Barai et al., Sci. Rep. 8, 21 (2018), doi:10.1038/s41598-017-18424-5; Schweiger et al., Sensors 10, 5604 (2010), doi:10.3390/s100605604); no power test |
| A3 | "where possible, the remaining round trip efficiency" | round-trip efficiency (DC, at the battery terminals) over full-to-full loops | energy out / energy in, summed over closed loops from one full rest to the next (Regulation (EU) 2023/1542, Annex IV (6)); band: how far that ratio can be off because a loop does not end at exactly the state of charge it started from; the size of that error is learnt from the loops themselves (mean lag-2 product of the loop scores minus the mean lag-1 product) |
| A4 | "the evolution of self-discharging rates" | apparent charge loss (an upper bound for self-discharge) | minus the offset current of the capacity fit (Figgener et al., Nature Energy 9, 1438 (2024), doi:10.1038/s41560-024-01620-9); band with the current sensor's gain error |
| A5 | "where possible, the ohmic resistance" | 1-second resistance, pack temperature band at 25 degrees C | dV/dI of current steps between consecutive samples (Barai et al., Sci. Rep. 8, 21 (2018), doi:10.1038/s41598-017-18424-5; Schweiger et al., Sensors 10, 5604 (2010), doi:10.3390/s100605604) |
| B1 | "the date of manufacture of the battery and, where appropriate, the date of putting into service" | date of manufacture; date of putting into service | the pack's documents (PackSpec), passed through |
| B2 | "the energy throughput" | energy throughput (in + out) since the first day of data | trapezoid sums of charging and discharging power over the measured time; band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width (JCGM 100:2008, 4.3.7) |
| B3 | "the capacity throughput" | capacity throughput (in + out) since the first day of data | trapezoid sums of charging and discharging current over the measured time; band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width (JCGM 100:2008, 4.3.7) |
| B4 | "the tracking of harmful events, such as the number of deep discharge events, time spent in extreme temperatures, time spent charging in extreme temperatures" | discharges to the system's own end-of-discharge level (observed; not a limit count) | measured hours per pack-temperature band and mode; discharges that reached the system's own end-of-discharge level (empty rests); voltage extremes; counts against limits when limits are given |
| B5 | "the number of full equivalent charge-discharge cycles" | full equivalent cycles since the first day of data | capacity throughput / (2 x reference capacity); band: sensor accuracy (Figgener et al. 2024, Supplementary Table 2) as a rectangular half-width (JCGM 100:2008, 4.3.7) |

## How it was checked

Thresholds were tuned on 7 development systems and frozen. The results below come from the
14 held-out systems and from synthetic packs with a known truth. The held-out systems were run once at the
settings of releases 0.1.0 and 0.2.0 and once more at the settings of this version. The capacity plausibility
rules and the power-capability rule of version 0.2 were found on them; the changes of this version were found on the
pack simulator. What changed, with every check before and after: [version 0.3 page](https://github.com/GhonAI/besslib/blob/main/docs/06_version_0_3.md). A failed check is published as
failed, with its reason.

Configuration hashes: capacity `b30137b3f868`, record `d49cbfdcd5b6`. Every result file carries them.

**Capacity** ([page](https://github.com/GhonAI/besslib/blob/v0.3.0/docs/02_capacity_validation.md); the reference tests: [page](https://github.com/GhonAI/besslib/blob/v0.3.0/docs/01_reference.md))

| Check | Scope | Value | Pass if | Result |
|---|---|---|---|---|
| V2 | held-out LMO/NMC and NMC | median \|error\| 2.62 points (22/28 estimated) | <= 3 points | passed |
| V2 info | same, without 03/3 and 08/1 | median \|error\| 2.98 points (26 tests) | information | – |
| V3 | held-out LFP | median \|error\| inf points; median error +4.62 (5/10 estimated) | <= 5 points, bias shown | failed |
| V4 | held-out LMO/NMC and NMC | band held the test in 32% of 28 | >= 80 % | failed |
| V4 info | held-out LFP | band held the test in 10% of 10 | information | – |
| V4 info | same as V4, without 03/3 and 08/1 | band held the test in 31% of 26 | information | – |
| V5 | held-out LMO/NMC (4 systems) | mean fade -3.44 points/year; paper -3.1; without one system -3.51 to -3.39 | within +-0.5 | passed |
| V5 info | all LMO/NMC (6 systems) | mean fade -3.36 points/year | information | – |
| V5 | held-out NMC (4 systems) | mean fade -1.15 points/year; paper -1.9; without one system -1.74 to -0.86 | within +-0.5 | failed |
| V5 info | all NMC (6 systems) | mean fade -1.13 points/year | information | – |
| V5 | held-out LFP (4 systems) | mean fade -2.73 points/year; paper -2.0; without one system -3.18 to -2.09 | within +-0.5 | failed |
| V5 info | all LFP (7 systems) | mean fade -1.48 points/year | information | – |
| V6 | 200 synthetic packs | band held the true usable fade (-1.88) in 27.0% | 85-95 % | failed |
| V7 | core against batch path, 90 daily estimates (months of tests 01/1, 07/1, 17/2) | max relative difference 2.0e-13 | <= 1e-6 | passed |
| V8 | 21 systems | one command per system: python validation/run_capacity.py &lt;id> | all 21 rebuilt with one hash | passed |

**The other Annex VII fields** ([page](https://github.com/GhonAI/besslib/blob/v0.3.0/docs/03_annex_vii.md))

| Check | Scope | Value | Pass if | Result |
|---|---|---|---|---|
| W1 info | development records | 7 of 7 records; 0 fields break the rule | information | – |
| W1 | held-out records | 14 of 14 records; 0 fields break the rule | 0 | passed |
| W2 | 200 synthetic packs: resistance at the weighted mean age of the days in memory | fails under each truth: same days 200 of 200 (too wide: that truth shares every day with the estimate; the systematic half-width alone holds 187); at the date 0 of 200 (the estimate lags the growing resistance by a median 2.3%); at 25 degrees C 90 of 200 (no temperature model; the days read a median +0.9%) | 85-95 % | failed |
| W2 | 200 synthetic packs: resistance growth per year, whole record, against the simulator's known growth (median 4.93 % per year at the fit's origin; this truth was chosen after the first results) | band held the truth in 184 of 200 (92.0%); same-days truth (information): 199 of 200 (99.5%) | 85-95 % | passed |
| W5 | 200 synthetic packs: round-trip efficiency, full-to-full loops | band held the truth in 176 of 200 (88.0%) | 85-95 % | passed |
| W7 | 200 synthetic packs: apparent charge loss (hidden drain) | band held the truth in 182 of 200 (91.0%) | 85-95 % | passed |
| W7 (0.3 rule) | 200 synthetic packs: apparent charge loss (hidden drain) | band held the hidden drain in 182 of 200 packs (91.0%); mean error -0.084 mA, limit +-0.024 (200 packs with an estimate) | 85-95 % and mean error within 2 sd / sqrt(n) of zero | failed |
| W8 | 200 synthetic packs: Ah, kWh, cycles; planted events (pack B: current gain error only) | largest relative error 0.498%; events exact on 200 of 200; 200 of 200 packs within the rule | <= 0.6 % and exact, every pack | passed |
| W8 info | 200 synthetic packs: the same on pack B with current and voltage noise | largest relative error 0.498%; events exact on 189 of 200; 189 of 200 packs within the rule | information | – |
| W3 info | development capacity tests | median \|difference\| 21.9 % over 12 of 22 tests with a reference; median difference by chemistry LFP +64.1 %, LMO/NMC -8.2 %, NMC -11.5 % | information | – |
| W3 | held-out capacity tests | 18 of 38 tests have a reference: not possible | >= 30 references, then <= 20 % | not possible |
| W4 info | development: neighbouring 5-degree bands: the colder reads higher | 50 of 58 (86%) | information | – |
| W6 info | development: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 21 of 31 (68%) | information | – |
| W4 | held-out: neighbouring 5-degree bands: the colder reads higher | 94 of 101 (93%) | >= 80% | passed |
| W6 | held-out: full-to-full against empty-to-empty loop ratios within 1 point (no band needed) | 39 of 55 (71%) | >= 80% | failed |
| W9 | core against batch, resistance: 240 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 3.6e-14 (64-bit); 2.6e-06 (32-bit, information) | <= 1e-6 | passed |
| W9 | core against batch, efficiency: 10 daily estimates (months of tests 01/1 (2017-06), 07/1 (2017-06), 17/2 (2020-06)) | max relative difference 0.0e+00 (64-bit); 1.3e-06 (32-bit, information) | <= 1e-6 | passed |
| W9 | daily counters against rest counters: 138814 rests, 21 systems (1-21) | max relative difference 8.7e-13 (throughput, system 13) | <= 1e-6 | passed |
| W10 | capacity run rebuilt for 21 systems | 0 of 60 files differ | 0 differences | passed |
| W10 | record A1 against the capacity run, every month end, 21 systems | 0 values differ over 1139 month ends (21 systems with a file) | 0 differences, every system | passed |
| W10 | records rebuilt twice for 21 systems | 0 differences over 42 files | 0 differences | passed |

## Limits

1. **The capacity band does not hold the charge between the BMS stops.** On 200 synthetic packs the monthly 90 % band held the charge between the stops at the date in 1524 of 4782 month ends (32 %). It held the capacity between the rest voltages it reports in 3789 of 4562 (83 %), against a truth chosen after the first results.
2. **Apparent charge loss (A4) is biased on synthetic packs.** The band held the true drain in 182 of 200 packs (91 %). The estimate is off by -0.08 mA on average (standard deviation 0.17 mA) on a true drain of 50 mA, just outside ±0.02 (2 sd/√200): a small bias remains. Version 0.3's rule for W7 (85-95 % and mean error within 2 sd / sqrt(n) of zero): fail.
3. **Storage at a middle state of charge gets a wrong capacity without the pack's voltage limits.** Do not use the capacity (A1) on storage that works at a middle state of charge unless the pack's documented voltage limits are given (`PackSpec.voltage_max_v`, `voltage_min_v`). With them, version 0.3 refuses: on simulated packs cycled daily between 25 and 75 % and never charged full, 200 of 200 packs with 0 estimated month ends and every reason 'not possible from this data', for NMC, LFP and the string. Without them it still takes the edges of the daily window for full and empty and reports a median 51.8 % (NMC), 51.0 % (LFP), 51.0 % (the string) of the true capacity, with the status `estimated` and the label "full and empty are the ends of the observed window; not checked against voltage limits".
4. **Apparent charge loss (A4) trails a new leak.** On simulated packs with a cell leak the A4 band holds the true apparent charge loss at 3424 of 3549 (96.5%) month ends without a leak, 1869 of 3574 (52.3%) at 5 mA and 1033 of 3590 (28.8%) at 15 mA, before the onset and from 180 days after it. Version 0.3's band is narrower; the estimate weighs its history with a 180-day memory, so after a new leak it trails the change and the band no longer covers it.
5. **The fade rate's band is too narrow on synthetic packs.** On 200 synthetic packs the band of the fade rate held the true usable fade (-1.88) in 27.0% (rule 85-95 %: fail). The reading times move the rate's bias, the quadratic terms narrow its band; together the band is too narrow for the bias.
6. Already published on the pages: the method needs regular full cycles; the first year of a record gives no capacity estimate; apparent charge loss is an upper bound for self-discharge; efficiency is DC at the battery terminals; resistance is a short-pulse value, not the impedance-spectroscopy one.

Fixed in version 0.2, by the same rules: capacity at or below zero, power capability without resistance ([limits](https://github.com/GhonAI/besslib/blob/main/docs/limits.md)).

All limits, with their numbers and pages: [`docs/limits.md`](https://github.com/GhonAI/besslib/blob/main/docs/limits.md).

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

Use GitHub's "Cite this repository" ([`CITATION.cff`](https://github.com/GhonAI/besslib/blob/v0.3.0/CITATION.cff)), and cite the data.

## Data and credit

Data: Figgener, J. et al., Data for: Multi-year field measurements of home storage systems and their use in capacity estimation, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.

Paper: Figgener, J. et al., Multi-year field measurements of home storage systems and their use in capacity estimation, Nature Energy 9, 1438-1447 (2024), https://doi.org/10.1038/s41560-024-01620-9, licence CC BY-NC-ND 4.0: cited, nothing copied.

## Contact

Islam Ghonaiem, ghonaiem@vgtai.com. Issues: [GitHub](https://github.com/GhonAI/besslib/issues).

## Licence

Code: BSD-3-Clause, see [`LICENSE`](https://github.com/GhonAI/besslib/blob/v0.3.0/LICENSE). The shipped sample: CC BY 4.0 (see above). The SunSpec model files in `besslib.sim.bus` are Apache-2.0.
