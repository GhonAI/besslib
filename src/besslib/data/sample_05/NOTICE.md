# Sample data: system 05 of the Figgener home-storage dataset

## What the files are

`daily.parquet`, `steps.parquet` and `rests.parquet` are the reduced tables of system 05: one row per day, per current step and per rest candidate. besslib reads them with `besslib.io.read_sample()`.

Modified: reduced by besslib 0.1.0 with validation/reduce_phase3.py: daily sums, current steps and rest candidates; not the original samples.

Made with the settings of configuration hashes 109e9eb1efa5 (capacity) and c2f7f545cebb (record).

## Credit

Creators, as in the Zenodo record:

- Figgener, Jan
- Haberschusz, David
- Wessels, Oliver
- Kairies, Kai-Philipp
- Bors, Jakob
- van Ouwerkerk, Jonas
- Sauer, Dirk Uwe

Title: Data for: Multi-year field measurements of home storage systems and their use in capacity estimation.

Source: Zenodo, https://doi.org/10.5281/zenodo.12091223, file Data_ID_05.zip.

Licence: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/.

This sample is licensed under CC BY 4.0, not under the BSD-3-Clause licence of the besslib code.

## Disclaimer

The licensed material is provided as-is and as-available, without representations or warranties of any kind, as stated in Section 5 of the CC BY 4.0 licence (disclaimer of warranties and limitation of liability).

## The paper

The dataset belongs to an article in Nature Energy. Cite it when you use the data:

Figgener, J. et al., Multi-year field measurements of home storage systems and their use in capacity estimation, Nature Energy 9, 1438-1447 (2024), https://doi.org/10.1038/s41560-024-01620-9, licence CC BY-NC-ND 4.0: cited, nothing copied.
