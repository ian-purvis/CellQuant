"""Safe generation of Alpine setup / validate / submit helpers."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from cellquant.hpc.cluster_profiles import AlpineProfile
from cellquant.persist.atomic import write_bytes_atomic


_DISALLOWED_IN_VALUES = re.compile(r"[\r\n`]|\$\(")
_OOD_GUIDE_NAME = "SUBMIT_THROUGH_OPEN_ONDEMAND.md"


@dataclass(frozen=True)
class UserClusterSettings:
    account: str | None
    qos: str
    gres: str
    walltime: str
    project_root: str
    scratch_root: str
    env_location: str
    email: str | None = None
    job_name: str = "cellquant-hpc"

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "qos": self.qos,
            "gres": self.gres,
            "walltime": self.walltime,
            "project_root": self.project_root,
            "scratch_root": self.scratch_root,
            "env_location": self.env_location,
            "email": self.email,
            "job_name": self.job_name,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], profile: AlpineProfile) -> UserClusterSettings:
        return cls(
            account=(str(raw["account"]) if raw.get("account") else None),
            qos=str(raw.get("qos") or profile.default_qos),
            gres=str(raw.get("gres") or profile.default_gres),
            walltime=str(raw.get("walltime") or profile.default_walltime),
            project_root=str(raw.get("project_root") or ""),
            scratch_root=str(raw.get("scratch_root") or ""),
            env_location=str(raw.get("env_location") or ""),
            email=(str(raw["email"]) if raw.get("email") else None),
            job_name=str(raw.get("job_name") or "cellquant-hpc"),
        )


def ood_guide_source_path() -> Path | None:
    """Return the repository Open OnDemand guide when present beside the package."""

    candidate = Path(__file__).resolve().parents[3] / "docs" / _OOD_GUIDE_NAME
    return candidate if candidate.is_file() else None


def _reject_unsafe(name: str, value: str | None) -> str:
    text = "" if value is None else str(value)
    if _DISALLOWED_IN_VALUES.search(text):
        raise ValueError(f"{name} contains disallowed characters (newlines or shell substitution)")
    return text


def _posix_quote(value: str) -> str:
    return shlex.quote(value)


def _write_lf(path: Path, text: str) -> None:
    """Write UTF-8 without BOM and LF endings only."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    write_bytes_atomic(path, normalized.encode("utf-8"))


def _sbatch_header(
    *,
    job_name: str,
    partition: str,
    qos: str,
    gres: str,
    cpus: int,
    mem: str,
    walltime: str,
    account_line: str,
    mail_lines: str,
    output_path: str,
    error_path: str,
) -> str:
    return f"""#!/usr/bin/env bash
#SBATCH --job-name={job_name}
#SBATCH --partition={partition}
#SBATCH --qos={qos}
#SBATCH --gres={gres}
#SBATCH --cpus-per-task={cpus}
#SBATCH --mem={mem}
#SBATCH --time={walltime}
#SBATCH --nodes=1
#SBATCH --ntasks=1
{account_line}
{mail_lines}#SBATCH --output={output_path}
#SBATCH --error={error_path}
"""


def _resolve_python_snippet(*, env_location: str | None = None) -> str:
    """Bash fragment: set PYTHON to a real Python 3.9+.

    Alpine login nodes often ship ``python`` as 2.7 and ``python3`` as an old
    3.6.x. Prefer the configured CellQuant env's ``bin/python`` (works even when
    ``bin/activate`` is missing), then newer versioned interpreters, then
    ``python3`` / ``python``.
    """

    baked = ""
    if env_location:
        env_root = env_location.rstrip("/")
        env_q = _posix_quote(env_root)
        py_q = _posix_quote(env_root + "/bin/python")
        py3_q = _posix_quote(env_root + "/bin/python3")
        baked = f"""
# Baked-in package env location (available even before resolved_paths.env).
if [[ -z "${{ENV_LOCATION:-}}" ]]; then
  ENV_LOCATION={env_q}
fi
# Prefer the configured env interpreter even when bin/activate is missing.
_CQ_BAKED_PYTHONS=( {py_q} {py3_q} )
"""

    return (
        baked
        + r'''# Resolve PYTHON to 3.9+ without trusting Alpine login defaults.
_cq_python_ok() {
  local cand="$1"
  [[ -n "$cand" ]] || return 1
  if [[ "$cand" == */* || "$cand" == ./* ]]; then
    [[ -x "$cand" ]] || return 1
  else
    command -v "$cand" >/dev/null 2>&1 || return 1
  fi
  "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null
}

if [[ -n "${PYTHON:-}" ]]; then
  if ! _cq_python_ok "$PYTHON"; then
    echo "ERROR: PYTHON=$PYTHON is not a usable Python 3.9+." >&2
    echo "Got: $($PYTHON -c 'import sys; print(sys.version)' 2>&1 || true)" >&2
    exit 1
  fi
else
  _CQ_CANDIDATES=()
  if [[ -n "${_CQ_BAKED_PYTHONS:-}" ]]; then
    _CQ_CANDIDATES+=("${_CQ_BAKED_PYTHONS[@]}")
  fi
  if [[ -n "${ENV_LOCATION:-}" ]]; then
    _CQ_CANDIDATES+=("${ENV_LOCATION}/bin/python" "${ENV_LOCATION}/bin/python3")
  fi
  _CQ_CANDIDATES+=(
    python3.12 python3.11 python3.10 python3.9
    python3
    python
  )
  PYTHON=""
  for _cq_cand in "${_CQ_CANDIDATES[@]}"; do
    if _cq_python_ok "$_cq_cand"; then
      PYTHON="$_cq_cand"
      break
    fi
  done
  if [[ -z "$PYTHON" ]]; then
    echo "ERROR: need Python 3.9+ on PATH (Alpine login python/python3 are often too old)." >&2
    if [[ -n "${ENV_LOCATION:-}" ]]; then
      echo "Configured env: ${ENV_LOCATION}" >&2
      echo "If that prefix has a working interpreter, run:" >&2
      echo "  export PATH=\"${ENV_LOCATION}/bin:\$PATH\"" >&2
      echo "  export PYTHON=\"${ENV_LOCATION}/bin/python\"" >&2
    else
      echo "Activate the CellQuant env first, or set:" >&2
      echo "  export PYTHON=/path/to/cellquant-hpc/bin/python" >&2
    fi
    exit 1
  fi
fi
# Prefer an absolute path when the chosen name is on PATH.
if [[ "$PYTHON" != /* ]]; then
  _cq_resolved="$(command -v "$PYTHON" 2>/dev/null || true)"
  if [[ -n "$_cq_resolved" ]]; then
    PYTHON="$_cq_resolved"
  fi
fi
echo "Using PYTHON=$PYTHON ($("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
'''
    )


def _activate_env_snippet() -> str:
    """Activate CellQuant env; fall back to PATH prefix when ``bin/activate`` is absent."""

    return r'''# Activate the configured CellQuant environment.
# Some Alpine conda prefixes have bin/python but no bin/activate; PATH prefix works.
if [[ -f "${ENV_LOCATION}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${ENV_LOCATION}/bin/activate"
elif [[ -f "${ENV_LOCATION}/etc/profile.d/conda.sh" ]]; then
  # shellcheck disable=SC1091
  source "${ENV_LOCATION}/etc/profile.d/conda.sh"
  conda activate "${ENV_LOCATION}"
elif [[ -x "${ENV_LOCATION}/bin/python" ]]; then
  export PATH="${ENV_LOCATION}/bin:${PATH}"
  echo "No bin/activate at ${ENV_LOCATION}; using ${ENV_LOCATION}/bin on PATH"
else
  echo "ERROR: cannot activate environment at ${ENV_LOCATION}" >&2
  echo "Expected bin/activate, etc/profile.d/conda.sh, or an executable bin/python." >&2
  exit 1
fi
'''


def _job_body(*, resolve_bundle: str, profile_id: str, partition: str) -> str:
    """Shared segmentation body. ``resolve_bundle`` assigns BUNDLE_DIR."""

    # Double braces for surrounding f-strings are NOT used here — callers
    # format this as a plain string (already expanded).
    return f'''set -euo pipefail

{resolve_bundle}
if [[ -z "${{BUNDLE_DIR:-}}" || ! -d "${{BUNDLE_DIR}}" ]]; then
  echo "ERROR: BUNDLE_DIR is unset or missing: ${{BUNDLE_DIR:-}}" >&2
  exit 1
fi
if [[ ! -f "${{BUNDLE_DIR}}/runtime/PREPARED.json" ]]; then
  echo "ERROR: package not prepared. On Alpine run: bash ${{BUNDLE_DIR}}/scripts/prepare_submission.sh" >&2
  echo "Job Composer Submit bypasses submit.sh; preparation must happen first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${{BUNDLE_DIR}}/runtime/resolved_paths.env"
SCRATCH_ROOT="${{SCRATCH_ROOT}}"
ENV_LOCATION="${{ENV_LOCATION}}"
PROJECT_ROOT="${{PROJECT_ROOT}}"
WORK_ID="${{WORK_ID}}"
STAGE_DIR="${{SCRATCH_ROOT}}/${{WORK_ID}}"
BUNDLE_STAGE="${{STAGE_DIR}}/bundle"
SCRATCH_RESULT="${{STAGE_DIR}}/results"
PUBLISH_DIR="${{PROJECT_ROOT}}/results/${{WORK_ID}}"

mkdir -p "${{BUNDLE_STAGE}}" "${{SCRATCH_RESULT}}" "${{PUBLISH_DIR}}"

{_activate_env_snippet()}

{_resolve_python_snippet()}

# Stage the complete checksummed bundle (inputs, configs, scripts, profiles,
# checksums.json, READY.json, …). Keep results in a sibling directory so
# restaging never deletes the only scratch copy. Logs and prepare-time
# markers stay on the project copy; they are not part of the immutable contract.
rsync -a --delete \\
  --exclude 'logs/' \\
  --exclude 'runtime/PREPARED.json' \\
  --exclude 'runtime/resolved_paths.env' \\
  "${{BUNDLE_DIR}}/" "${{BUNDLE_STAGE}}/"

"$PYTHON" "${{BUNDLE_STAGE}}/scripts/validate_bundle.py"

"$PYTHON" - <<'PY'
import sys
try:
    import torch
except Exception as exc:
    print("ERROR: torch unavailable:", exc, file=sys.stderr)
    sys.exit(1)
if not torch.cuda.is_available():
    print(
        "ERROR: CUDA GPU required by profile {profile_id} ({partition}) "
        "but torch.cuda.is_available() is False",
        file=sys.stderr,
    )
    sys.exit(1)
print("GPU OK:", torch.cuda.get_device_name(0))
PY

export CELLQUANT_HPC_BUNDLE="${{BUNDLE_STAGE}}"
export CELLQUANT_HPC_RESULT="${{SCRATCH_RESULT}}"
"$PYTHON" -m cellquant.hpc.runner --bundle "${{BUNDLE_STAGE}}" --result "${{SCRATCH_RESULT}}"

if [[ ! -f "${{SCRATCH_RESULT}}/result_manifest.json" ]]; then
  echo "ERROR: runner did not write result_manifest.json under ${{SCRATCH_RESULT}}" >&2
  echo "Scratch working data retained at ${{STAGE_DIR}}" >&2
  exit 1
fi

if ! rsync -a --delete "${{SCRATCH_RESULT}}/" "${{PUBLISH_DIR}}/"; then
  echo "ERROR: copy-back to ${{PUBLISH_DIR}} failed. Scratch results retained at ${{SCRATCH_RESULT}}" >&2
  exit 1
fi
if [[ ! -f "${{PUBLISH_DIR}}/result_manifest.json" ]]; then
  echo "ERROR: published results missing result_manifest.json. Scratch results retained at ${{SCRATCH_RESULT}}" >&2
  exit 1
fi

echo "Job finished. Results published under ${{PUBLISH_DIR}}"
echo "Scratch working copy retained at ${{SCRATCH_RESULT}}"
'''


def generate_scripts(
    scripts_dir: Path,
    *,
    profile: AlpineProfile,
    user_settings: UserClusterSettings,
    bundle_name: str,
) -> dict[str, Path]:
    """Generate fixed-template scripts; user values are quoted shell assignments."""

    scripts_dir.mkdir(parents=True, exist_ok=True)
    account = _reject_unsafe("account", user_settings.account)
    qos = _reject_unsafe("qos", user_settings.qos)
    gres = _reject_unsafe("gres", user_settings.gres)
    walltime = _reject_unsafe("walltime", user_settings.walltime)
    project_root = _reject_unsafe("project_root", user_settings.project_root)
    scratch_root = _reject_unsafe("scratch_root", user_settings.scratch_root)
    env_location = _reject_unsafe("env_location", user_settings.env_location)
    email = _reject_unsafe("email", user_settings.email)
    job_name = _reject_unsafe("job_name", user_settings.job_name)
    partition = _reject_unsafe("partition", profile.partition)
    package_root = f"{project_root.rstrip('/')}/{bundle_name}"

    validate_py = r'''#!/usr/bin/env python3
"""Validate an HPC bundle before submission (no GPU required)."""

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ready = root / "READY.json"
    bundle = root / "bundle.json"
    checksums = root / "checksums.json"
    errors = []
    if not ready.is_file():
        errors.append("READY.json missing — package is incomplete and must not be submitted")
    if not bundle.is_file():
        errors.append("bundle.json missing")
    if not checksums.is_file():
        errors.append("checksums.json missing")
    else:
        payload = json.loads(checksums.read_text(encoding="utf-8"))
        files = payload.get("files", {})
        import hashlib
        for rel, expected in files.items():
            path = root / rel
            if not path.is_file():
                errors.append(f"missing file listed in checksums: {rel}")
                continue
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != expected:
                errors.append(f"checksum mismatch: {rel}")
    if errors:
        print("VALIDATION FAILED", file=sys.stderr)
        for item in errors:
            print(f" - {item}", file=sys.stderr)
        return 1
    print("VALIDATION OK")
    print(f"bundle={root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    setup_sh = f'''#!/usr/bin/env bash
# One-time environment setup helper for CellQuant HPC packages.
set -euo pipefail

ENV_LOCATION={_posix_quote(env_location)}
PROJECT_ROOT={_posix_quote(project_root)}
BUNDLE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"

echo "CellQuant HPC setup"
echo "  bundle: ${{BUNDLE_DIR}}"
echo "  env:    ${{ENV_LOCATION}}"
echo "  project:${{PROJECT_ROOT}}"
echo
if [[ ! -d "${{ENV_LOCATION}}" ]]; then
  echo "Environment path does not exist yet: ${{ENV_LOCATION}}"
  echo "Create/activate a compatible CellQuant env there, then re-run validate."
  exit 1
fi
echo "Environment location exists."
if [[ -f "${{ENV_LOCATION}}/bin/activate" ]]; then
  echo "Activate before submit:"
  echo "  source { _posix_quote(env_location.rstrip('/') + '/bin/activate') }"
elif [[ -x "${{ENV_LOCATION}}/bin/python" ]]; then
  echo "This prefix has bin/python but no bin/activate. Use:"
  echo "  export PATH={_posix_quote(env_location.rstrip('/') + '/bin')}:$PATH"
  echo "  export PYTHON={_posix_quote(env_location.rstrip('/') + '/bin/python')}"
else
  echo "WARNING: no bin/activate or bin/python under ${{ENV_LOCATION}}"
fi
echo "Download standard model weights during setup — not inside the GPU job."
'''

    account_line = f"#SBATCH --account={account}" if account else "# account not required by profile"
    mail_lines = ""
    if email:
        mail_lines = f"#SBATCH --mail-user={email}\n#SBATCH --mail-type=END,FAIL\n"

    prepare_sh = f'''#!/usr/bin/env bash
# Required before terminal submit OR Open OnDemand Job Composer Submit.
# Creates logs/, validates the package, and writes absolute resolved paths.
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
cd "${{BUNDLE_DIR}}"

if [[ ! -f READY.json ]]; then
  echo "ERROR: READY.json missing — package is incomplete and cannot be submitted." >&2
  exit 1
fi

{_resolve_python_snippet(env_location=env_location)}

"$PYTHON" - <<'PY'
import json, sys
from pathlib import Path
profile = json.loads((Path("profiles") / "alpine.json").read_text(encoding="utf-8"))
if not profile.get("submit_ready", True):
    print(
        "ERROR: profile",
        profile.get("profile_id"),
        "is not submit-ready:",
        profile.get("display_name"),
        file=sys.stderr,
    )
    print(
        "Prepare/Compare packages are allowed; do not submit until environment + parity gates pass.",
        file=sys.stderr,
    )
    sys.exit(1)
print("submit_ready OK:", profile.get("profile_id"))
PY

"$PYTHON" scripts/validate_bundle.py

# Slurm opens log files before the batch script runs — must exist first.
mkdir -p logs
mkdir -p runtime

cat > runtime/resolved_paths.env <<EOF
BUNDLE_DIR=${{BUNDLE_DIR}}
SCRATCH_ROOT={_posix_quote(scratch_root)}
ENV_LOCATION={_posix_quote(env_location)}
PROJECT_ROOT={_posix_quote(project_root)}
WORK_ID={_posix_quote(bundle_name)}
PACKAGE_ROOT_EXPECTED={_posix_quote(package_root)}
EOF

"$PYTHON" - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
payload = {{
    "prepared_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "bundle_dir": str(Path(".").resolve()),
}}
Path("runtime/PREPARED.json").write_text(json.dumps(payload, indent=2) + "\\n", encoding="utf-8")
print("PREPARED", payload["bundle_dir"])
PY

echo "Preparation complete."
echo "  Terminal submit:   bash scripts/submit.sh"
echo "  Open OnDemand:     paste scripts/run_jobcomposer.sbatch into Job Composer (Alpine)."
echo "  Do NOT paste submit.sh into Job Composer (it would call sbatch again)."
'''

    # Terminal path: Slurm copies the batch script under /var/spool/slurmd, so
    # BASH_SOURCE cannot locate the package. Prefer SLURM_SUBMIT_DIR (cwd of
    # sbatch), then the absolute export package root, then local script parent.
    terminal_resolve = f'''if [[ -n "${{SLURM_SUBMIT_DIR:-}}" && -f "${{SLURM_SUBMIT_DIR}}/READY.json" ]]; then
  BUNDLE_DIR="$(cd "${{SLURM_SUBMIT_DIR}}" && pwd)"
elif [[ -f {_posix_quote(package_root + "/READY.json")} ]]; then
  BUNDLE_DIR={_posix_quote(package_root)}
else
  BUNDLE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
fi'''
    # Job Composer path: explicit absolute package root (Composer cwd differs).
    composer_resolve = f"BUNDLE_DIR={_posix_quote(package_root)}"

    run_sbatch = (
        _sbatch_header(
            job_name=job_name,
            partition=partition,
            qos=qos,
            gres=gres,
            cpus=int(profile.cpus_per_task),
            mem=profile.mem,
            walltime=walltime,
            account_line=account_line,
            mail_lines=mail_lines,
            output_path="logs/%x_%j.out",
            error_path="logs/%x_%j.err",
        )
        + "\n"
        + _job_body(
            resolve_bundle=terminal_resolve,
            profile_id=profile.profile_id,
            partition=partition,
        )
    )

    # Absolute log paths so Composer can submit from a different directory.
    composer_log_out = f"{package_root}/logs/%x_%j.out"
    composer_log_err = f"{package_root}/logs/%x_%j.err"
    run_composer = (
        _sbatch_header(
            job_name=job_name,
            partition=partition,
            qos=qos,
            gres=gres,
            cpus=int(profile.cpus_per_task),
            mem=profile.mem,
            walltime=walltime,
            account_line=account_line,
            mail_lines=mail_lines,
            output_path=composer_log_out,
            error_path=composer_log_err,
        )
        + "\n"
        + "# Job Composer payload: same resources/identity as scripts/run.sbatch.\n"
        + "# Requires: bash scripts/prepare_submission.sh on the transferred package first.\n"
        + "# Do not paste scripts/submit.sh here.\n"
        + _job_body(
            resolve_bundle=composer_resolve,
            profile_id=profile.profile_id,
            partition=partition,
        )
    )

    submit_sh = f'''#!/usr/bin/env bash
# Terminal submission: prepare (if needed) then sbatch the package batch script.
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/.." && pwd)"
cd "${{BUNDLE_DIR}}"

bash scripts/prepare_submission.sh

JOB_MSG="$(sbatch scripts/run.sbatch)"
echo "${{JOB_MSG}}"
echo "Logs: ${{BUNDLE_DIR}}/logs"
echo "Results root: {_posix_quote(project_root)}/results/{bundle_name}"
'''

    written = {
        "scripts/validate_bundle.py": scripts_dir / "validate_bundle.py",
        "scripts/setup_environment.sh": scripts_dir / "setup_environment.sh",
        "scripts/prepare_submission.sh": scripts_dir / "prepare_submission.sh",
        "scripts/run.sbatch": scripts_dir / "run.sbatch",
        "scripts/run_jobcomposer.sbatch": scripts_dir / "run_jobcomposer.sbatch",
        "scripts/submit.sh": scripts_dir / "submit.sh",
    }
    _write_lf(written["scripts/validate_bundle.py"], validate_py)
    _write_lf(written["scripts/setup_environment.sh"], setup_sh)
    _write_lf(written["scripts/prepare_submission.sh"], prepare_sh)
    _write_lf(written["scripts/run.sbatch"], run_sbatch)
    _write_lf(written["scripts/run_jobcomposer.sbatch"], run_composer)
    _write_lf(written["scripts/submit.sh"], submit_sh)
    return written


def render_readme(
    *,
    profile: AlpineProfile,
    user_settings: UserClusterSettings,
    bundle_name: str,
    acquisition_count: int,
) -> str:
    project = user_settings.project_root or profile.project_root_hint
    scratch = user_settings.scratch_root or profile.scratch_root_template
    env_loc = user_settings.env_location or "<env-location>"
    package_root = f"{project.rstrip('/')}/{bundle_name}"
    smoke = profile.smoke_verified_utc
    docs = profile.documentation_checked_utc
    smoke_line = (
        f"Cluster smoke verified: {smoke}"
        if smoke
        else "Cluster smoke test: NOT YET RECORDED — local export success does not mean cluster verified."
    )
    docs_line = f"CURC documentation checked: {docs or 'unknown'}"
    ready_line = (
        "Profile submit-ready: yes"
        if profile.submit_ready
        else "Profile submit-ready: NO — Requires validation (do not submit until gates pass)."
    )
    vram_line = f"VRAM: {profile.vram_gb} GB" if profile.vram_gb else "VRAM: see profile"
    return f"""# CellQuant HPC package — transfer and submit

Bundle: `{bundle_name}`  
Acquisitions: {acquisition_count}  
Profile: {profile.display_name} (`{profile.profile_id}`)  
Partition/GRES: `{profile.partition}` / `{user_settings.gres or profile.default_gres}` ({vram_line})  
Expected Alpine package path: `{package_root}`  
{docs_line}  
{smoke_line}  
{ready_line}

This package is **ready for transfer**. CellQuant did **not** log into Alpine,
upload files, or submit a job. Local validation is not a live Alpine run.

## What will be uploaded

- This entire folder (`{bundle_name}/`), including `inputs/`, `configs/`, `scripts/`, and manifests.
- Keep large inputs and durable results **outside** any Open OnDemand Job Composer job directory.

## Where work runs

- Scratch/compute staging: `{scratch}/{bundle_name}` (not durable; can be purged)
- Durable results copy-back: `{project}/results/{bundle_name}`
- Do not run intensive I/O under `/home` or `/projects`

## Steps (user-performed)

1. **Transfer** this folder to Alpine (expected: `{package_root}`). Prefer **Globus** for large image bundles.
   CURC advises against uploading files larger than **1 GB** through the Open OnDemand Files browser app.
2. **One-time environment setup** (if needed) at `{env_loc}`: `bash scripts/setup_environment.sh`.
3. **Prepare** (required before either submit path; creates `logs/` and validates):

```bash
cd {_posix_quote(package_root)}
bash scripts/prepare_submission.sh
```

4. **Submit** using **one** of the paths below (only if the profile is submit-ready).

### Path A — Submit through terminal

```bash
cd {_posix_quote(package_root)}
bash scripts/submit.sh
```

(`submit.sh` re-runs preparation, then `sbatch scripts/run.sbatch`.)

### Path B — Submit through Open OnDemand (Job Composer)

Follow the packaged guide: [`SUBMIT_THROUGH_OPEN_ONDEMAND.md`](SUBMIT_THROUGH_OPEN_ONDEMAND.md)
(also in the CellQuant repo under `docs/`). Core Desktop is **not** required.

1. Complete step 3 (prepare) in a terminal / OOD shell first.
2. Open OnDemand → **Jobs → Job Composer** → **New Job → From Default Template** → **Alpine**.
3. Replace the template with the contents of `scripts/run_jobcomposer.sbatch`
   (same resources and job body as the terminal batch script; absolute package paths).
4. **Do not** paste `scripts/submit.sh` into Job Composer — that wrapper calls `sbatch`
   and would nest another submission instead of running segmentation in your allocation.
5. Save and **Submit**. Record the job ID. Keep copies of the package, script, logs and results
   outside Composer’s job folder (Composer **Delete** removes that directory).

Resource fragment reminder (incomplete — retain the package’s full validated script):

```bash
#SBATCH --partition={profile.partition}
#SBATCH --qos={user_settings.qos or profile.default_qos}
#SBATCH --gres={user_settings.gres or profile.default_gres}
#SBATCH --nodes=1
```

## Bring results back

1. After the job finishes, download `{project}/results/{bundle_name}` (Globus preferred for large results).
2. In CellQuant use **Import HPC results…** (or `cellquant hpc import-results`) and select the result folder.
3. Incomplete acquisitions remain unfinished; successful ones open into measurement/coexpression review without renaming files.

Do not edit generated scripts to inject credentials. Do not submit if `READY.json` is missing,
preparation was skipped, or the profile is not submit-ready.
"""


_OOD_FILE_LIMIT_BYTES = 1 * 1024 * 1024 * 1024


def transfer_instructions(
    *,
    local_bundle: Path,
    remote_parent: str,
    max_input_bytes: int | None = None,
) -> str:
    """Return Globus-oriented transfer guidance plus an advanced rsync example."""

    remote = remote_parent.rstrip("/")
    local = str(local_bundle).replace("\\", "/")
    lines = [
        "Preferred: transfer this package with Globus to your Alpine project space.",
        f"  Local package: {local}",
        f"  Remote parent: {remote}/",
        "  Destination folder name: " + local_bundle.name,
        "",
        "Advanced CLI alternative (rsync):",
        f"  rsync -avP {_posix_quote(local + '/')} "
        f"{_posix_quote(remote + '/' + local_bundle.name + '/')}",
        "",
        "Do not use Open OnDemand Files browser upload for large TIFFs.",
        "Keep large inputs/results outside Job Composer-managed directories.",
    ]
    if max_input_bytes is not None and max_input_bytes > _OOD_FILE_LIMIT_BYTES:
        lines.append(
            f"This package contains at least one file over 1 GB "
            f"({max_input_bytes} bytes observed) — CURC advises against OOD Files for >1 GB uploads."
        )
    elif max_input_bytes is None:
        lines.append(
            "If any exported TIFF exceeds 1 GB, use Globus (or rsync/scp), not the OOD Files app."
        )
    return "\n".join(lines) + "\n"


def submission_command(*, remote_bundle: str) -> str:
    """Terminal path: prepare + submit from the transferred package."""

    return (
        f"cd {_posix_quote(remote_bundle)} && "
        "bash scripts/prepare_submission.sh && bash scripts/submit.sh"
    )


def jobcomposer_instructions(*, remote_bundle: str) -> str:
    """Clipboard text for the Open OnDemand Job Composer path."""

    return "\n".join(
        [
            "Submit through Open OnDemand (Job Composer) — not Core Desktop.",
            f"1. On Alpine: cd {_posix_quote(remote_bundle)}",
            "2. bash scripts/prepare_submission.sh",
            "3. OOD → Jobs → Job Composer → New Job → From Default Template → Alpine",
            "4. Paste the contents of scripts/run_jobcomposer.sbatch (NOT submit.sh)",
            "5. Save → Submit; record the job ID",
            "6. See SUBMIT_THROUGH_OPEN_ONDEMAND.md in this package for full guidance",
            "",
        ]
    )


__all__ = [
    "UserClusterSettings",
    "generate_scripts",
    "jobcomposer_instructions",
    "ood_guide_source_path",
    "render_readme",
    "submission_command",
    "transfer_instructions",
]
