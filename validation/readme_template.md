# besslib

{{doi_badge}}

State of health from battery storage operating data — with confidence bands, in the fields of the
EU Battery Regulation (2023/1542, Annex VII) — and a pack simulator that tests it, and your BMS or
energy-management code, against a known truth. Checked on field data only on home storage that reaches full and
empty regularly. On mid-state-of-charge duty it refuses when the pack's voltage limits are given; without them it
reads about half the true capacity ({{t5a_share}}, simulated packs).

**Version {{version}}, {{release_date}}.** It computes the Annex VII parameters; it does not certify them. It is not a
compliance certificate and not legal advice.

Author: Islam Ghonaiem, ghonaiem@vgtai.com — questions on the method and its failures are welcome.

![{{figure_alt}}]({{raw:docs/img/00_readme_05.png}})

*{{figure_caption}}*

Checked on open field data: {{n_systems}} home storage systems, {{n_months}} months, {{n_tests}} field capacity tests.
On the {{n_heldout}} held-out systems (not used to tune any threshold; seen at releases {{seen_releases}}, so these numbers are a third look, not a blind test):

- LMO/NMC and NMC, {{v2_tests}} tests: median error {{v2_value}} points of nominal capacity, a test without an estimate counted as infinite (target ≤ {{v2_target}}): **{{v2_result}}**; over the {{v2_have}} tests with an estimate, {{v2_est_error}} points.
- LFP, {{v3_tests}} tests: {{v3_missing}} have no estimate ({{v3_reasons}}) and the other {{v3_have}} read a median {{v3_bias}} points {{v3_direction}} (target: median error ≤ {{v3_target}} points, a test without an estimate counted as infinite): **{{v3_result}}**.
- The {{band_level}} % band held the measured value in {{v4_share}} % of {{v4_tests}} tests (target {{v4_target}} %): **{{v4_result}}**. [Why →]({{url_main:docs/limits.md}})

{{version_sentence}}

{{data_showed}}

{{sim_block}}

Every number on this page is written by `validation/make_readme.py` from a results file in `validation/results/`.

## Install and run

```
pip install besslib
```

{{quickstart_code}}

{{quickstart_table}}

{{sample_note}} The sample's data is CC BY 4.0, not BSD-3-Clause; its credit is in
[`NOTICE.md`]({{url:src/besslib/data/sample_05/NOTICE.md}}) and in each file's metadata.

The quickstart runs without the capacity-test mask, as a user would. The published record of the same system leaves
the test windows out ([Annex VII page]({{url:docs/03_annex_vii.md}})); a test checks that the sample with that mask
gives the published record exactly.

**Your own data:** a pandas DataFrame in the frame format of
[`besslib/frames.py`]({{url:src/besslib/frames.py}}) (columns, units, sign convention) and a `besslib.PackSpec`;
then `besslib.soh.annex_vii(frame, spec)`. **The full field data** of one system, from the Zenodo archive:

{{fulldata_code}}

## What it computes

Each field carries a value, a band, the number of events it rests on, the method with its source, and a status.
Where the data cannot give a value, the status says `not possible from this data` with the reason. Nothing is
filled in silently.

{{annex_table}}

## How it was checked

Thresholds were tuned on {{n_development}} development systems and frozen. The results below come from the
{{n_heldout}} held-out systems and from synthetic packs with a known truth. The held-out systems were run once at the
settings of releases {{seen_releases}} and once more at the settings of this version. The capacity plausibility
rules and the power-capability rule of version {{previous_minor}} were found on them; the changes of this version were found on the
pack simulator. What changed, with every check before and after: {{version_page_link}}. A failed check is published as
failed, with its reason.

Configuration hashes: capacity `{{capacity_hash}}`, record `{{record_hash}}`. Every result file carries them.

**Capacity** ([page]({{url:docs/02_capacity_validation.md}}); the reference tests: [page]({{url:docs/01_reference.md}}))

{{v_table}}

**The other Annex VII fields** ([page]({{url:docs/03_annex_vii.md}}))

{{w_table}}

## Limits

{{limits_list}}

All limits, with their numbers and pages: [`docs/limits.md`]({{url_main:docs/limits.md}}).

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

Use GitHub's "Cite this repository" ([`CITATION.cff`]({{url:CITATION.cff}})), and cite the data.

## Data and credit

{{data_credit}}

Paper: {{paper_credit}}

## Contact

Islam Ghonaiem, ghonaiem@vgtai.com. Issues: [GitHub]({{issues_url}}).

## Licence

Code: BSD-3-Clause, see [`LICENSE`]({{url:LICENSE}}). The shipped sample: CC BY 4.0 (see above). The SunSpec model files in `besslib.sim.bus` are Apache-2.0.
