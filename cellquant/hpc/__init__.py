"""Prepare an experiment for a cluster GPU job, run it there, and bring the results back.

Preparation (Windows) freezes the analysis settings and exports every selected
acquisition losslessly into a checksummed package. The worker (Linux, Slurm)
runs the same ``process_image`` as local analysis, one acquisition at a time,
and publishes each verified result to durable storage. Import (Windows) builds
a new, self-contained experiment from the package and the returned results.

Nothing here logs into a cluster, stores credentials or submits jobs: users
run the generated commands. See docs/HPC_PREP_AND_SUBMISSION.md.
"""

from __future__ import annotations

SCHEMA_VERSION = 1
BUNDLE_KIND = "cellquant_v2_hpc"
