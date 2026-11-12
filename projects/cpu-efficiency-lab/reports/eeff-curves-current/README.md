# Efficiency curve render

Rendered locally from the synchronized inputs `data/eeff.tsv` (86 single-core points) and `data/eeff_combo.tsv` (23 measured eight-core combinations). No new device measurement was performed for this render.

- `1_throughput.svg`: single-core throughput by locked frequency.
- `2_energy_per_op.svg`: derived whole-device battery-side energy per operation; not CPU-rail energy.
- `3_power.svg`: whole-device average battery-side power by frequency.
- `4_combo_heatmap.svg`: **model extrapolation from single-core data, not a measured combination**; do not use as measured evidence.
- `5_combo_measured.svg`: measured eight-core combination throughput/efficiency.
- `6_power_band.svg`, `7_power_vs_ops.svg`: measured whole-device power spread and power-vs-throughput views.
- `summary.tsv`: aggregate single-core curve values.

The measurements were made with manual fixed-frequency control. They do not validate the automated `matrix-sweep` recovery path; `INDEPENDENT_RECOVERY_VERIFIED` remains false.

A subsequent authorized on-device recovery fault-injection run completed 11/11 `PASS` (0 `RESTRICTED`, `SKIPPED`, or `FAIL`). Postflight confirmed all CPUs online, no benchmark process, and the screen timeout and policy bounds restored to the run's starting snapshot. This is fault-injection evidence only—not new performance data or proof that sysfs check/write races are gone. The matrix gate remains closed.
