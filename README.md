# besslib

State of health from battery storage operating data — with confidence bands, in the fields of the
EU Battery Regulation (2023/1542, Annex VII).

**Status: in development, not released.**

## What works today

| Part | What it does | Where |
|---|---|---|
| Reader | Reads the open field dataset of Figgener et al. (2024) straight from its archives | `besslib.io.read_figgener` |
| Reference | Measures a field capacity test that is recorded inside the operating data | `besslib.soh.measure_capacity_test` |
| Phase 1 result | The dataset's 60 field capacity tests, measured again | [`docs/01_reference.md`](docs/01_reference.md) |
| Capacity | Usable capacity with a confidence band and its fade rate, from rests at full and empty in operating data; checked against the field tests on systems it was not tuned on | `besslib.soh.estimate_capacity`, `besslib.soh.fade_rate`; result: [`docs/02_capacity_validation.md`](docs/02_capacity_validation.md) |

The other state-of-health estimators (resistance, efficiency, the Annex VII record) are phase 3.

## Rebuild the phase 1 result

```
pip install -e .[dev]
python validation/build_reference.py      # downloads 21 GB from Zenodo, one archive at a time
python validation/make_reference_doc.py
```

## Data and credit

Figgener, J. et al., *Data for: Multi-year field measurements of home storage systems and their use in capacity
estimation*, Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.
Paper: Nature Energy 9, 1438–1447 (2024), https://doi.org/10.1038/s41560-024-01620-9.

## Licence

BSD-3-Clause, see [`LICENSE`](LICENSE).
