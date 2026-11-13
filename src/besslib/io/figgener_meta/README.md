# Metadata of the Figgener et al. (2024) field dataset

`systems.csv` and `capacity_tests.csv` are converted from `Metadata_Systems.xlsx` and
`Capacity_Tests.xlsx` in `Metadata_and_Code.zip` of:

Figgener, J., Haberschusz, D., Wessels, O., Kairies, K.-P., Bors, J., van Ouwerkerk, J., Sauer, D. U.:
"Data for: Multi-year field measurements of home storage systems and their use in capacity estimation",
Zenodo (2024), https://doi.org/10.5281/zenodo.12091223, licence CC BY 4.0.

Changes made: installation dates converted from Unix seconds to dates (UTC); capacity-test start times
converted from spreadsheet serial numbers to timestamps, rounded to the second. Rebuilt by
`validation/make_metadata.py`.
