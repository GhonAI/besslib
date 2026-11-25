# Data behind the presets

The files in this folder are cells for `besslib.sim`. They hold values read from published data, not the data
itself, and they are not models of any product. `synth.toml` is besslib's own example.

## lfp.toml

Creators, as in the Zenodo record:

- Gray, Robert
- Vagg, Christopher

Title: LIC cell characterisation (version 1.0), EU Horizon project TEMPEST.

Source: Zenodo, https://doi.org/10.5281/zenodo.19632939, files LIC_capacity_analysis.csv, LIC_hppc_analysis.csv, 25C_capacity.csv,
15C_hppc.csv, 25C_hppc.csv and 35C_hppc.csv.

Licence: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/.

Modified: the resistance tables are the source's values at 15, 25 and 35 degC, with the time constant computed as R1 times C1; the
open-circuit curve, the hysteresis curve, the hysteresis rate and the activation energies are computed from the files by
`validation/sim_params_lic.py`, as the comments in the preset say.

## nmc.toml and lmo.toml

Authors: Manh-Kien Tran, Andre DaCosta, Anosh Mevawalla, Satyam Panchal, Michael Fowler.

Title: Comparative Study of Equivalent Circuit Models Performance in Four Common Lithium-Ion Batteries: LFP, NMC, LMO, NCA.

Source: Batteries 7, 51 (2021), https://doi.org/10.3390/batteries7030051, Tables 5, 7 and 8 and Figure 6 (b) and (c).

Licence: CC BY 4.0, https://creativecommons.org/licenses/by/4.0/.

Modified: the time constant is computed as R1 times C1; the open-circuit curves are digitised from the figure by
`validation/sim_params_tran.py`, as the comments in the presets say.

The activation energies in both presets come from Ecker et al., J. Electrochem. Soc. 162, A1836 (2015),
https://doi.org/10.1149/2.0551509jes (cited; two numbers used, nothing copied).

## Disclaimer

The licensed material is provided as-is and as-available, without representations or warranties of any kind, as stated in Section 5
of the CC BY 4.0 licence (disclaimer of warranties and limitation of liability).
