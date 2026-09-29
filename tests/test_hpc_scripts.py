"""Generated Slurm scripts: syntax, ShellCheck, quoting, resources, and a full run with stubbed Slurm (AC15)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cellquant.hpc.common import read_json
from cellquant.hpc.models import ClusterProfile
from cellquant.hpc.templates import walltime
from tests.hpc_helpers import FAKE_V4, cellpose_recipe, install_fake_cellpose, make_experiment, prepared_package, write_profile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("scripts/submit.sh", "scripts/job.sbatch", "scripts/preflight.sh")


def test_scripts_parse_pass_shellcheck_and_use_lf(tmp_path: Path):
    _controller, package, _runtime = prepared_package(tmp_path / "folder with spaces")
    shellcheck = shutil.which("shellcheck") or str(Path(sys.executable).parent / "shellcheck")
    for relative in SCRIPTS:
        path = package / relative
        data = path.read_bytes()
        assert b"\r" not in data and data.startswith(b"#!/usr/bin/env bash\n")
        assert os.access(path, os.X_OK)
        subprocess.run(["bash", "-n", str(path)], check=True)
        if Path(shellcheck).exists():
            result = subprocess.run([shellcheck, "-s", "bash", str(path)], capture_output=True, text=True)
            assert result.returncode == 0, result.stdout
    assert b"\r" not in (package / "README_SUBMIT.md").read_bytes()


def test_resources_render_from_the_profile(tmp_path: Path):
    _controller, package, _runtime = prepared_package(tmp_path)
    job = (package / "scripts" / "job.sbatch").read_text()
    for line in (
        "#SBATCH --account=amc-general",
        "#SBATCH --partition=ah200",
        "#SBATCH --qos=gpu-normal",
        "#SBATCH --gres=gpu:h200_3g.71gb:1",
        "#SBATCH --cpus-per-task=8",
        "#SBATCH --mem=65536M",
        "#SBATCH --time=04:00:00",
        "#SBATCH --nodes=1",
    ):
        assert line in job
    assert "CELLPOSE_LOCAL_MODELS_PATH=/projects/lab/cellquant/models" in job and "HF_HUB_OFFLINE=1" in job
    assert walltime(90061) == "1-01:01:01" and walltime(3600) == "01:00:00"
    profile = ClusterProfile.model_validate({**json.loads((package / "cluster.json").read_text()), "qos": None})
    from cellquant.hpc.templates import job_script
    from cellquant.hpc.validate import open_bundle

    bundle = open_bundle(package)
    assert "--qos" not in job_script(bundle.manifest, profile, bundle.runtime)


# --- a whole run through the generated scripts, with Slurm and the GPU stubbed -----------------------------

FAKE_TORCH = '''
import types
__version__ = "2.99.0+fake"
version = types.SimpleNamespace(cuda="12.8")

class _Props:
    total_memory = 71 * 1024**3

class cuda:
    @staticmethod
    def is_available(): return True
    @staticmethod
    def get_device_name(index=0): return "Fake H200 3g.71gb"
    @staticmethod
    def get_device_properties(index=0): return _Props()
    @staticmethod
    def get_device_capability(index=0): return (9, 0)
    @staticmethod
    def get_arch_list(): return ["sm_90"]
    @staticmethod
    def device_count(): return 1

def manual_seed(value): return None
'''

SBATCH_STUB = """#!/usr/bin/env bash
# Runs the job at once, like a node that was free, after checking the log folder exists already.
set -euo pipefail
OUT=""
ARGS=("$@")
for index in "${!ARGS[@]}"; do
  case "${ARGS[$index]}" in
    --output=*) OUT="${ARGS[$index]#--output=}" ;;
  esac
  if [[ "${ARGS[$index]}" == *job.sbatch ]]; then SCRIPT_INDEX=$index; fi
done
[[ -n "$OUT" ]] || { echo "no --output" >&2; exit 9; }
[[ -d "$(dirname "$OUT")" ]] || { echo "log folder missing before sbatch" >&2; exit 9; }
echo "$*" >> "$STUB_LOG"
JOB=${FAKE_JOB_ID:-5150}
LOG="${OUT//%j/$JOB}"
SLURM_JOB_ID=$JOB SLURM_CPUS_PER_TASK=2 SLURM_CLUSTER_NAME=alpine bash "${ARGS[@]:$SCRIPT_INDEX}" > "$LOG" 2>&1 || echo "job exit $?" >> "$STUB_LOG"
echo "$JOB"
"""


@pytest.fixture
def cluster(tmp_path: Path, monkeypatch):
    """A fake cluster: a Python wrapper, model files, stub sbatch, and a runtime contract made by runtime-inspect."""

    install_fake_cellpose(tmp_path, monkeypatch)
    fake_site = next(path for path in tmp_path.iterdir() if path.name.startswith("cellpose_"))
    (fake_site / "torch").mkdir()
    (fake_site / "torch" / "__init__.py").write_text(textwrap.dedent(FAKE_TORCH))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(f'#!/usr/bin/env bash\nexport PYTHONPATH="{fake_site}:{ROOT}"\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "sbatch").write_text(SBATCH_STUB)
    for path in (python, bin_dir / "sbatch"):
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    models = tmp_path / "models dir"
    models.mkdir()
    (models / "cpsam_v2").write_bytes(b"fake weights")
    lock = tmp_path / "environment.lock"
    lock.write_text("cellpose==4.2.1.1\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "sbatch.log"))
    monkeypatch.syspath_prepend(str(ROOT))
    runtime = tmp_path / "profiles" / "alpine-cp4-fake.json"
    runtime.parent.mkdir()
    from cellquant.hpc.__main__ import main

    code = main(
        [
            "runtime-inspect", "--engine", "cellpose4", "--model", "cpsam_v2", "--runtime-id", "alpine-cp4-fake", "--lock", str(lock),
            "--models-dir", str(models), "--modes", "max_projection,stitch_slices", "--output", str(runtime),
        ]
    )
    assert code == 0
    contract = json.loads(runtime.read_text())
    contract["runtime_validation_date"] = "2026-09-29"
    runtime.write_text(json.dumps(contract, indent=2))
    durable = tmp_path / "projects dir" / "cellquant"
    scratch = tmp_path / "scratch dir"
    profile = write_profile(
        tmp_path / "profiles", runtime, runtime.read_bytes(), python_path=str(python), remote_durable_root=str(durable), scratch_root=str(scratch)
    )
    return {"profile": profile, "durable": durable, "scratch": scratch, "tmp": tmp_path}


def _prepare(cluster, count: int = 2) -> Path:
    from cellquant.hpc.prepare import plan_preparation, prepare_package
    from cellquant.hpc.profiles import load_profile

    controller = make_experiment(cluster["tmp"] / "lab", count, recipe=cellpose_recipe("max_projection"))
    plan = plan_preparation(controller.experiment, controller.recipe, load_profile(cluster["profile"]))
    assert plan.ok, [issue.message for issue in plan.all_errors()]
    local = prepare_package(plan, cluster["tmp"] / "local packages").package_dir
    remote = cluster["durable"] / local.name  # "transfer"
    remote.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(local, remote)
    return remote


def test_submit_and_job_scripts_run_end_to_end(cluster):
    package = _prepare(cluster)
    preflight = subprocess.run(["bash", str(package / "scripts" / "preflight.sh")], capture_output=True, text=True)
    assert preflight.returncode == 0, preflight.stdout + preflight.stderr
    assert json.loads(preflight.stdout[preflight.stdout.index("{"): preflight.stdout.rindex("}") + 1])["inference"]["run"] is False
    submitted = subprocess.run(["bash", str(package / "scripts" / "submit.sh")], capture_output=True, text=True)
    assert submitted.returncode == 0, submitted.stdout + submitted.stderr
    assert "Submitted job 5150" in submitted.stdout
    runs = list((cluster["durable"] / "results" / package.name).iterdir())
    assert len(runs) == 1
    run_dir = runs[0]
    index = read_json(run_dir / "results.json")
    assert index["compute_outcome"] == "completed" and index["publication_outcome"] == "verified_complete", (run_dir / "attempts").iterdir()
    attempt = next((run_dir / "attempts").iterdir())
    assert read_json(attempt / "attempt.json")["job_id"] == "5150"
    gpu = read_json(attempt / "preflight.json")
    assert gpu["ok"] and gpu["gpu"]["name"] == "Fake H200 3g.71gb" and gpu["inference"]["device"] == "cuda"
    log = (attempt / "logs" / "slurm-5150.out").read_text()
    assert "Scratch was kept" in log and "rm -rf --" in log
    scratch = cluster["scratch"] / package.name / run_dir.name / attempt.name
    assert (scratch / "package" / "READY").is_file()
    # Resume a finished run: a new attempt, nothing redone.
    again = subprocess.run(["bash", str(package / "scripts" / "submit.sh"), "--resume", run_dir.name], capture_output=True, text=True)
    assert again.returncode == 0, again.stdout + again.stderr
    assert len(list((run_dir / "attempts").iterdir())) == 2
    assert read_json(run_dir / "results.json")["compute_outcome"] == "completed"


def test_submit_refuses_a_changed_package_and_an_owned_run(cluster, monkeypatch):
    package = _prepare(cluster)
    with (package / "recipe.yaml").open("a") as handle:
        handle.write("# changed\n")
    changed = subprocess.run(["bash", str(package / "scripts" / "submit.sh")], capture_output=True, text=True)
    assert changed.returncode == 2 and "E_CHECKSUM" in changed.stderr
    assert not (cluster["durable"] / "results").exists()
    package = _prepare(cluster)
    from cellquant.hpc.common import new_id, utc_now, sha256_file
    from cellquant.hpc.lease import acquire
    from cellquant.hpc.models import LeaseRecord
    from cellquant.hpc.runner import allocate_run

    bundle_id = json.loads((package / "bundle.json").read_text())["bundle_id"]
    run_id, run_dir = allocate_run(cluster["durable"] / "results" / package.name, bundle_id, sha256_file(package / "checksums.json"), package.name)
    acquire(run_dir, LeaseRecord(lease_token=new_id(length=32), ready_digest=sha256_file(package / "checksums.json"), run_id=run_id, attempt_id="att_old", job_id="999", acquired_at=utc_now()))
    blocked = subprocess.run(["bash", str(package / "scripts" / "submit.sh"), "--resume", run_id], capture_output=True, text=True)
    assert blocked.returncode == 2
    assert "recover-lease" in blocked.stderr and "--job-id 999" in blocked.stderr
    assert not (Path(os.environ["STUB_LOG"]).exists() and "job.sbatch" in Path(os.environ["STUB_LOG"]).read_text())


def test_a_failed_node_check_analyzes_nothing(cluster, monkeypatch):
    package = _prepare(cluster)
    monkeypatch.setenv("FAKE_CELLPOSE_DEVICE", "cpu")  # the test segmentation lands on the CPU
    submitted = subprocess.run(["bash", str(package / "scripts" / "submit.sh")], capture_output=True, text=True)
    assert submitted.returncode == 0
    run_dir = next((cluster["durable"] / "results" / package.name).iterdir())
    attempt = next((run_dir / "attempts").iterdir())
    report = read_json(attempt / "preflight.json")
    assert not report["ok"] and report["errors"][0]["code"] == "E_GPU"
    assert not (run_dir / "results.json").exists() and not (run_dir / "tasks").exists()
    assert "preflight failed" in (attempt / "notes.log").read_text()
    assert "job exit 3" in Path(os.environ["STUB_LOG"]).read_text()


def test_the_job_refuses_to_run_without_submit(cluster):
    package = _prepare(cluster)
    result = subprocess.run(["bash", str(package / "scripts" / "job.sbatch")], capture_output=True, text=True)
    assert result.returncode == 2 and "submit.sh" in result.stderr


def test_the_worker_rejects_a_mismatched_environment(cluster):
    package = _prepare(cluster)
    contract = json.loads((package / "runtime.json").read_text())
    from cellquant.hpc.runtime import compare_runtime, observed_runtime
    from cellquant.hpc.models import RuntimeContract

    observed = observed_runtime("cellpose4", "cpsam_v2", models_dir=contract["model_directory"])
    assert compare_runtime(RuntimeContract.model_validate(contract), observed) == []
    Path(contract["model_directory"], "cpsam_v2").write_bytes(b"other weights")
    issues = compare_runtime(RuntimeContract.model_validate(contract), observed_runtime("cellpose4", "cpsam_v2", models_dir=contract["model_directory"]))
    assert [issue.code for issue in issues] == ["E_RUNTIME_MODEL_HASH"]
    assert FAKE_V4
