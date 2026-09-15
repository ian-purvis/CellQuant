# Submit a CellQuant HPC package through the terminal

Start with [Prepare images in CellQuant](HPC_PREP_AND_SUBMISSION.md). This guide assumes the complete package has been transferred to the expected Alpine location and a compatible environment is installed there.

**Before submitting:** obtain a compatible Alpine environment from the maintainer and run a small trial first. The commands below document the current helper interface. A successful local package validation is not a live Alpine smoke test.

The environment is a prerequisite, not a step in this submission procedure. The
maintainer must provide its path and verify that the generated batch script can
activate it on a compute node. See [Alpine environment prerequisite](HPC_PREP_AND_SUBMISSION.md#alpine-environment-prerequisite).

## 1. Open an Alpine terminal

Use your existing CURC SSH connection, or sign in to Open OnDemand and select an Alpine shell under **Clusters** (the exact menu label may vary). You do not need to start Core Desktop. See the [CURC portal overview](https://curc.readthedocs.io/en/latest/open_ondemand/index.html).

All Bash commands below run in that cluster terminal, not Windows PowerShell. Type:

```bash
whoami
pwd
```

`whoami` prints your cluster username. `pwd` prints your current directory. You can use the username to confirm paths; it does not identify your allocation/account name.

## 2. Go to the transferred package

Read your package's `README_SUBMIT.md` for its expected Alpine path. In this example, replace `your_username` and `cellquant_hpc_abc123` with the actual values:

```bash
cd '/projects/your_username/cellquant/cellquant_hpc_abc123'
pwd
ls
```

You should see `README_SUBMIT.md`, `READY.json`, `checksums.json`, `inputs`, `configs`, and `scripts` directly in this directory. If not, stop and find the correct folder; do not submit from a parent directory or a partial upload.

To read the package instructions in the terminal:

```bash
less README_SUBMIT.md
```

Press `q` to leave the viewer. The file contains your actual package name, profile and destination paths.

## 3. Activate the existing environment

Use the exact activation command supplied by the maintainer. Prefer this order:

**A. Standard activate script (if present):**

```bash
source '/projects/your_username/cellquant/envs/cellquant-hpc/bin/activate'
python --version
python -c 'import cellquant; import torch; print(torch.__version__)'
```

**B. Prefix-only env (common when `bin/activate` is missing):**

```bash
export PATH="/projects/your_username/cellquant/envs/cellquant-hpc/bin:$PATH"
export PYTHON="/projects/your_username/cellquant/envs/cellquant-hpc/bin/python"
"$PYTHON" --version
"$PYTHON" -c 'import cellquant; import torch; print(torch.__version__)'
```

Replace the example environment path. Expect Python 3.11 and successful imports without a traceback. This checks availability, not the full environment/model compatibility. Do not launch segmentation to test a GPU on a login node; GPU validation belongs inside an allocated compute job.

The configured environment must also activate correctly **inside the batch script**. Generated `run.sbatch` / Job Composer scripts now fall back to putting `ENV_LOCATION/bin` on `PATH` when `bin/activate` is absent. Successful interactive `conda activate` alone still does not establish compute-node readiness.

From the package directory run:

```bash
bash scripts/setup_environment.sh
```

Despite its name, this helper currently only checks the environment directory and prints advice. It is not an installer. An “Environment location exists” message does not prove the software or model weights are installed. Ask the maintainer for a provisioned environment if this prerequisite is missing; do not spend a GPU allocation installing one experimentally.

## 4. Validate and prepare

Still inside the package directory, run:

```bash
bash scripts/prepare_submission.sh
```

Newer packages auto-select a Python 3.9+ interpreter in this order: the configured
CellQuant env's `bin/python`, then `python3.12`…`python3.9`, then `python3` /
`python`. Alpine login nodes often provide **Python 2** as bare `python` and an
**old 3.6.x** as `python3` — those are skipped when a newer candidate exists.

You can still override explicitly:

```bash
export PYTHON=/projects/your_username/cellquant/envs/cellquant-hpc/bin/python
bash scripts/prepare_submission.sh
```

Optional local checks:

```bash
"$PYTHON" --version
"$PYTHON" scripts/validate_bundle.py
```

A `SyntaxError` on `file=sys.stderr` almost always means Python 2 ran a Python 3 script.

The first command checks local transferred files against the package inventory. The second checks the profile, validates again, creates log directories and writes resolved runtime paths. Success includes `PREPARED`, your package directory, and `Preparation complete.` It creates `runtime/PREPARED.json` and `runtime/resolved_paths.env`.

If either command prints an error, resolve it before submitting. Missing files usually mean an incomplete transfer. A checksum mismatch can mean a changed or damaged file: transfer the original again or re-export the package. Do not remove validation checks or manually mark an incomplete package ready.

RTX Pro 6000 packages are submit-ready for comparative trials. Prefer H200 for production runs until a matched smoke/parity result is recorded.

## 5. Submit one small test job

Only after the environment is verified, run:

```bash
bash scripts/submit.sh
```

This helper reruns preparation and submits the batch script. Prefer this command to a bare `sbatch`, which bypasses required preparation. Do not also click Submit in Job Composer for the same test.

Success should include a scheduler response such as:

```text
Submitted batch job 123456
```

The number will be different for your job. Record it. This means the scheduler accepted the request, not that segmentation finished. The helper also prints the logs and results locations.

If `sbatch` is unavailable, check that you are in an Alpine shell configured for its scheduler. If the account, partition or QoS is rejected, check your allocation and package profile; do not substitute random resource names. Use [CURC submission error guidance](https://curc.readthedocs.io/en/latest/running-jobs/error-status-codes.html).

## 6. Monitor progress

List your queued/running jobs:

```bash
squeue -u "$USER"
```

Common states are `PD` (pending/queued) and `R` (running). Pending does not by itself indicate an error. The reason column can explain a resource wait or account limit. An empty listing means no matching active jobs; it does not distinguish completed from failed jobs.

Inspect accounting for your job; replace `123456`:

```bash
sacct -j 123456 --format=JobID,JobName,State,ExitCode,Elapsed
```

Look for the overall job state and its steps. `COMPLETED` with exit code `0:0` is useful scheduler evidence, but also inspect CellQuant's result summary. `FAILED`, `TIMEOUT`, `CANCELLED` and `OUT_OF_MEMORY` require investigation before a larger run. Accounting can take time to update.

From the package directory, list logs:

```bash
ls logs
```

The default job name produces files such as `cellquant-hpc_123456.out` and `.err`. Substitute the filenames you actually see:

```bash
tail -n 50 logs/cellquant-hpc_123456.out
tail -n 50 logs/cellquant-hpc_123456.err
```

To watch new output as it arrives, use `tail -f` on the output file. Press Ctrl+C to stop watching; that stops the viewer, not the cluster job. Silence in the log is not a reliable progress estimate.

## 7. Cancel or retry deliberately

To cancel your particular job, first confirm its ID in `squeue`, then replace the example number:

```bash
scancel 123456
```

Do not cancel all jobs belonging to your account. After cancellation, inspect retained results and failure status. If a lock remains after an interrupted run, ask the maintainer to establish whether a worker is still active; do not delete lock files blindly.

For a retry with the same settings, use the same original bundle and documented submission helper only after confirming that no prior worker is active and that the failure has been resolved. Changed settings require a newly exported package. Do not assume resubmission fixes a checksum, environment, staging or GPU-memory error.

## 8. Bring results back

The configured result root is `<Project root>/results/<package name>`, for example:

```text
/projects/your_username/cellquant/results/cellquant_hpc_abc123
```

Check `result_manifest.json` and the logs for completed, resumed, failed and unfinished acquisitions. Absence of the manifest is a failure to investigate, not an empty successful dataset.

Download the complete result directory and retain the input bundle. Follow [Download and import results](HPC_PREP_AND_SUBMISSION.md#8-download-and-import-results), using a new empty local import destination. Inspect representative masks before submitting the full dataset with the same validated settings.

## Quick troubleshooting

| Symptom | Next action |
|---|---|
| `No such file or directory` | Check `pwd`, `ls`, username and package name; verify transfer layout |
| `python` missing or imports fail | Activate the supplied environment; have the maintainer verify installation |
| `READY.json missing` | Use a completed export; do not create the marker yourself |
| Checksum mismatch | Restore the original transferred file or re-export changed settings |
| Profile not submit-ready | Complete its validation or export with a supported profile |
| `Invalid account or account/partition` | Use the Slurm allocation from `sacctmgr` (e.g. `amc-general`), not your login email; re-export — do not edit package scripts |
| `package not prepared` and path under `/var/spool/slurmd` | Older packages resolved the bundle from the Slurm script copy. Re-export after the SLURM_SUBMIT_DIR fix, or resubmit a one-off `sbatch` with `BUNDLE_DIR` set to the absolute package path (submit from that package directory so `logs/` stay correct) |
| CUDA unavailable inside the job | Check GPU allocation and compatible environment; do not enable CPU fallback to hide it |
| Scheduler completes but some images fail | Read the result manifest and per-image errors; scheduler status is not scientific completion |
| Import cannot find cluster paths | Confirm you selected the downloaded result folder that contains `result_manifest.json` and `runs/`; re-export if the package predates portable run paths |
| `squeue` says invalid job id | Job left the queue; use `sacct -j <id>` and read `logs/*.err` |

No real Alpine smoke success is recorded yet for this alpha. The interface/commands were checked against CellQuant's source; a successful maintained smoke job remains the release gate.
