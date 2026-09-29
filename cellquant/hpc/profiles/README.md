# Cluster profiles

A profile says where and with what resources a package runs. Copy `alpine_h200_example.json`,
fill in your Slurm account, your project and scratch folders, and the environment's Python, and put
the maintainer's runtime contract (`<runtime id>.runtime.json`) beside it. Set `runtime_sha256` to
that file's SHA-256 (PowerShell: `Get-FileHash file.json`; Linux: `sha256sum file.json`).

The example's partition, QoS and GPU request are unverified. Check them against current cluster
documentation and record `profile_verified_date`. A profile stays "experimental" until a cluster smoke
test is recorded in `live_smoke_date` (see docs/HPC_PREP_AND_SUBMISSION.md).
