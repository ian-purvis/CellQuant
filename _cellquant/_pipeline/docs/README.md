# CellQuant documentation

Choose the guide for your task. The package is currently **0.4.0a3**, an alpha for lab testing. The most recent recorded software verification is **2026-09-16**; expert-reviewed retinal accuracy and a live Alpine smoke run remain unverified.

| Start here | Use it for |
| --- | --- |
| [First-time user guide](START_HERE.md) | Install, launch, analyze one image or a folder, review nuclear coexpression, and find results without coding |
| [Developer guide](DEVELOPER_GUIDE.md) | Current source map, setup, tests, CLI, configuration, output contracts, how to trace changes, and [how to maintain these docs](DEVELOPER_GUIDE.md#maintain-the-documentation) |
| [Project README](../README.md) | Short overview and Windows quick start |
| [Architecture and scientific contract](../ARCHITECTURE.md) | Detailed package boundaries, axes, provenance and parity expectations |

## Current task guides

- [HPC prep and submission](HPC_PREP_AND_SUBMISSION.md) — local export, transfer, Alpine prerequisites, submission choice and import. Follow either the [terminal](SUBMIT_THROUGH_TERMINAL.md) or [Open OnDemand](SUBMIT_THROUGH_OPEN_ONDEMAND.md) instructions after export.
- [CUDA gauntlet](CUDA_GAUNTLET.md) — scientific/performance validation procedure, not a record of a passed CUDA candidate run.

## Release and verification record

- [0.4.0a3 release note](RELEASE_0.4.0a3.md) — latest recorded behavior changes and software checks.
- [0.4.0a2 release note](RELEASE_0.4.0a2.md) — prior alpha (calibration and coexpression state).
- [`STATUS.json`](STATUS.json) — read **`current_verification`** for the 0.4.0a3 assessment. Its other dated software/module/gauntlet records are historical and may name older versions or artifacts.

## Design rationale and proposed work

These retained documents explain design decisions or proposed behavior. Check current source and the release record before treating any described feature as implemented.

- [Quantification design](QUANTIFICATION_DESIGN.md) — rationale for the quantification approach.
- [Quantification review UX specification](QUANTIFICATION_REVIEW_UX_SPEC_2026-09-11.md) — a **proposal**, not current UI instructions; check the source for what has been implemented.
- [Segmentation Review/QC specification](SEGMENTATION_REVIEW_QC_SPEC.md) — behavioral contract for standalone mask review (implemented in `0.4.0a3`); distinct from downstream quantification review.
- [HPC contract gate](HPC_CONTRACT_GATE_2026-09-11.md) — the settled first-release HPC contract.
- [Deferred parameter sweep decision](../memory/2026-09-08-deferred-parameter-sweep.md) — records why a parameter search has not been authorized to run.
