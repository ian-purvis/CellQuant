# Alpine smoke test report (template)

Copy to `docs/HPC_SMOKE_REPORT.md` and fill in. Keep private image data and credentials out of it.
A profile may be marked `production` only when every line below is filled in and passed.

| Item | Value |
|---|---|
| Date (UTC) | |
| Person | |
| CellQuant release and build digest (`application_build_sha256`) | |
| Profile file and `profile_id` | |
| Partition / QoS / GRES / CPUs / memory / time limit | |
| Runtime contract (`runtime_id`, SHA-256 of runtime.json) | |
| Dependency lock SHA-256 | |
| Engine, Cellpose version, model, model file SHA-256s | |
| Z mode(s) tested | |
| Package name and bundle ID | |
| Images (2 representative acquisitions: file name, position, shape) | |
| Job ID(s) | |
| GPU from `preflight.json` (name, memory, driver, CUDA build) | |
| `preflight.json` ok and inference device `cuda` | |
| `results.json`: compute outcome, publication outcome, finalized | |
| Wall time per image (from `worker.log`) | |
| Parity (AC08): original vs prepared input on the same node, labels identical, measurements within rtol 1e-6 / atol 1e-8 | |
| Download: whole run folder, `python -m cellquant.hpc import` result | |
| Windows review: opened, one object deleted and restored, thresholds changed, exported, reopened after moving the folder | |
| Forced failure: what was done (e.g. `scancel` after the first image) | |
| Resume: `submit.sh --resume RUN_ID` result; images skipped vs rerun | |
| Lease recovery needed? `recover-lease` output | |
| Scratch removed after download (command used) | |
| Problems found and follow-up | |

Result: PASS / FAIL. If PASS, set in the profile: `live_smoke_date`, `validation_status: "production"`.
