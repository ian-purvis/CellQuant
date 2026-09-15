# Submit through Open OnDemand

Use CURC's **Job Composer** to submit an Alpine segmentation job through a
browser. You do not need Core Desktop. This guide assumes you already have a
prepared segmentation package and an Alpine-compatible Slurm batch script.
CellQuant's local package validation does not establish that a package has
passed a real Alpine run; check the package's validation status before a large
submission.

For the full local preparation walkthrough, see
[Prepare images and submit to Alpine](HPC_PREP_AND_SUBMISSION.md); terminal
instructions are in [Submit through the terminal](SUBMIT_THROUGH_TERMINAL.md).
These repository guides may not be included in older exported packages.

**Current implementation note (September 11, 2026):** environment setup requires a
preinstalled compatible environment. `scripts/setup_environment.sh` checks that
location; it does not install CellQuant. No live Alpine Job Composer run has
been recorded yet. The generated job stages the complete bundle to scratch,
validates it there, writes results on scratch, then copies them to
`<Project root>/results/<package name>`. The steps below describe the current
helpers. Confirm environment activation inside the batch script before a large
submission.

## 1. Prepare and transfer the package

1. Prepare the images and review the effective segmentation settings in
   CellQuant's HPC prep workflow. Follow the exported package instructions for
   environment setup and validation; an existing Alpine package may have a
   different setup procedure.
2. Transfer the complete package to storage accessible from Alpine. Use
   Globus for large transfers; CURC advises against uploading or downloading
   files larger than 1 GB through the [Open OnDemand Files application](https://curc.readthedocs.io/en/latest/open_ondemand/files_app.html).
   Keep large inputs and durable results outside the directory managed
   by Job Composer.
3. Obtain the compatible environment/model setup and exact activation command
   from the maintainer. `scripts/setup_environment.sh` currently checks an
   existing environment directory; it does not install the software. Confirm
   that the batch script can activate it, not just your interactive terminal.
   If the env has `bin/python` but no `bin/activate`, generated batch scripts put
   that `bin` directory on `PATH` automatically after prepare.
4. After transfer, open an Alpine terminal
   through Open OnDemand's **Clusters** menu or your usual SSH connection,
   activate the supplied environment, then run the following. Replace the
   example username and package name with the path in `README_SUBMIT.md`:

```bash
cd '/projects/your_username/cellquant/cellquant_hpc_abc123'
python scripts/validate_bundle.py
bash scripts/prepare_submission.sh
```

Expect `PREPARED` and `Preparation complete.` before proceeding. Preparation
creates `logs/` and resolved runtime paths. Slurm must be able to open its log
paths before the job body runs. Resolve errors before clicking Submit.

Do not bypass a package's submission helper without checking its contract. In
particular, a `submit.sh` wrapper that calls `sbatch` is not the batch payload:
pasting that wrapper into Job Composer could submit a second job instead of
running segmentation in the resources you requested. Use the actual Slurm
batch script after completing the wrapper's required preparation.

## 2. Create the job

1. Sign in to CURC Open OnDemand and open **Jobs → Job Composer**.
2. Choose **New Job → From Default Template**, then select **Alpine**.
3. In Open OnDemand **Files**, open the transferred package's
   `scripts/run_jobcomposer.sbatch` in the text viewer/editor and copy its
   contents without modifying the original. In Job Composer's script editor,
   replace the default template with that complete payload. This is the
   dedicated Composer script, not `submit.sh` or `run.sbatch`.
4. Review every path. Job Composer's working directory may differ from your
   package directory. Use explicit cluster paths for the package, environment,
   inputs, results and logs, and an explicit working directory where required.
   The generated Composer script expects `<Project root>/<package name>` and
   uses absolute log paths there. Transfer to that exact path. If the path is
   wrong, correct the profile fields and re-export instead of editing the
   original checksummed script.
5. Save the script and use **Submit**. Record the job ID.

The recommended GPU choice for this workflow is H200, based on Ian's prior
segmentation experience. The following is an **incomplete resource fragment**,
not a runnable job script:

```bash
#SBATCH --partition=ah200
#SBATCH --qos=gpu-normal
#SBATCH --gres=gpu:h200:1
#SBATCH --nodes=1
```

Retain the validated package's CPU, memory, walltime, output/error paths and
account settings as applicable. Check current limits and account access before
submission. H200 is a starting choice, not a guarantee of shortest queue time
or fastest performance for every image/model. See
[CURC Alpine hardware](https://curc.readthedocs.io/en/latest/clusters/alpine/alpine-hardware.html)
and [Job Composer instructions](https://curc.readthedocs.io/en/latest/open_ondemand/jobs_app.html).

## 3. Monitor and collect results

Use **Jobs → Active Jobs** to check status or cancel a job. Submission success
means the scheduler accepted the job; it does not mean segmentation succeeded.
Inspect the job's logs and `result_manifest.json` after it
finishes. Transfer the validated results back and follow the package's
CellQuant import instructions.

Keep an independent copy of the package, submitted script, job ID, logs and
results. Job Composer's **Delete** action removes its job directory: do not
use that directory as the only home of data you need to retain. See the
[CURC Jobs application documentation](https://curc.readthedocs.io/en/latest/open_ondemand/jobs_app.html).

## When Core Desktop helps

Core Desktop provides a browser-accessible Linux desktop for graphical
applications. It is optional for this workflow and does not give you an Alpine
H200 allocation. Its shared visualization GPUs are intended for visualization
and interactive work, not intensive segmentation. Submit the compute job to
Alpine through Job Composer instead. See
[CURC Core Desktop](https://curc.readthedocs.io/en/latest/open_ondemand/core_desktop.html).

## Validation status

These instructions describe CURC's documented GUI workflow. The end-to-end
CellQuant package → Job Composer → Alpine → CellQuant import path has not been
validated in a live CURC account as part of this documentation change. Run a
small smoke job after environment setup and before committing a large dataset.
