# CUDA scientific gauntlet

This is the remaining Wave 4 procedure. Run it on the same NVIDIA workstation
used to time the Fiji/reference workflow. Do not score identity-control masks as
candidate results.

## 1. Verify the runtime before inference

```powershell
nvidia-smi
python -c "import torch, cellpose; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0)); print(cellpose.__version__)"
Get-FileHash -Algorithm SHA256 "$env:USERPROFILE\.cellpose\models\cpsam_v2"
```

The weight hash must equal
`0f1cc3f7ecdd8a037a57c6c48d9d8921391be4cbce3fa9f13c3e3a2e1253c667`.
`torch.cuda.is_available()` must be `True`; the reference profile forbids silent
CPU fallback.

## 2. Three-stack critic round

Run the low-, median-, and high-density cases recorded in `docs/STATUS.json`
through `cellquant harness`, using a separate `*.cellquant` output directory for
each. Preserve stdout/stderr and the generated provenance. Compare their
`labels.tif` files with the same-basename TIFFs in the reference `outputs`
directory:

```powershell
cellquant parity REFERENCE_LABEL_DIR THREE_CASE_CANDIDATE_ROOT THREE_CASE_REPORT
```

Do not pass unless mean matched IoU, F1, and count delta meet the rubric in
`ARCHITECTURE.md`, every configuration/model fingerprint validates, and the
candidate wall time is at most 1.5 times the measured reference on that machine.

## 3. Full 37-stack batch

```powershell
cellquant batch REFERENCE_TEST_IMAGES CANDIDATE_ROOT --config reference_config.yaml --recursive
cellquant parity REFERENCE_OUTPUTS CANDIDATE_ROOT FULL_REPORT
```

Supply reference/candidate timing, peak RAM, and peak VRAM records to the parity
report API so `performance_comparison.csv` is populated. Investigate every
out-of-tolerance stack against the raw signal and write a cause; do not assume
the reference is correct.

## 4. Plugin empirical gate

Launch napari with the installed wheel, open a representative lazy stack, run
and cancel once, edit labels, and save. Capture the dock and layer screenshots.
Instrument GUI event-loop latency throughout open/run/cancel/save; the maximum
stall must be below 100 ms. Record the lazy array type, source size, event trace,
and stable `CellQuant image`/`CellQuant labels` layer types.

## 5. Blind review

Generate paired overlay crops for every outlier, randomly swap A/B using the
serialized runtime seed, hide pipeline identity, and record which boundary set
better follows nuclear signal plus the reason. Only then update the candidate
score and performance budgets in `docs/STATUS.json`.
