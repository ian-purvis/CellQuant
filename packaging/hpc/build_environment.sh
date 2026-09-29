#!/usr/bin/env bash
# Build a CellQuant cluster environment, fetch its model weights once, and export its runtime contract.
# Run by the maintainer on a cluster login node (or a compile node). Jobs never install or download anything.
#
#   bash build_environment.sh ENGINE PREFIX CELLQUANT_SOURCE MODEL_DIR RUNTIME_ID [TORCH_INDEX]
#
#   ENGINE            cellpose4 or cellpose3
#   PREFIX            new folder for the environment, e.g. /projects/$USER/cellquant/envs/cq-cp4-2026.09
#   CELLQUANT_SOURCE  the CellQuant_v2 folder of the release to run (the same files as on the lab computers)
#   MODEL_DIR         folder for the model weights, e.g. /projects/$USER/cellquant/models
#   RUNTIME_ID        a name for this environment, e.g. alpine-cp4-2026.09
#   TORCH_INDEX       PyTorch wheel index (default https://download.pytorch.org/whl/cu128, CUDA 12.8)
#
# Afterwards: run the GPU preflight and the parity check (docs/HPC_PREP_AND_SUBMISSION.md), then enable
# the validated Z modes and set runtime_validation_date in the runtime contract.
set -euo pipefail
if [[ $# -lt 5 ]]; then
  sed -n '2,15p' "$0"
  exit 2
fi
ENGINE="$1"; PREFIX="$2"; SOURCE="$3"; MODELS="$4"; RUNTIME_ID="$5"
TORCH_INDEX="${6:-https://download.pytorch.org/whl/cu128}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
case "$ENGINE" in
  cellpose4) MODEL=cpsam_v2; TORCH_PIN="torch==2.14.0" ;;
  cellpose3) MODEL=nuclei; TORCH_PIN="torch==2.5.1" ;;
  *) echo "ENGINE must be cellpose4 or cellpose3" >&2; exit 2 ;;
esac
PYTHON="${PYTHON:-python3.11}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.11 was not found as $PYTHON. Load a Python 3.11 module or set PYTHON=/path/to/python3.11." >&2
  exit 3
fi
if [[ -e "$PREFIX" ]]; then
  echo "$PREFIX exists. Environments are never changed in place; choose a new folder." >&2
  exit 2
fi
"$PYTHON" -m venv "$PREFIX"
PY="$PREFIX/bin/python"
"$PY" -m pip install --upgrade pip==26.2.1
"$PY" -m pip install "$TORCH_PIN" --index-url "$TORCH_INDEX"
"$PY" -m pip install -r "$HERE/requirements-$ENGINE.txt"
"$PY" -m pip install --no-deps "$SOURCE"
"$PY" -m pip check
mkdir -p "$MODELS"
export CELLPOSE_LOCAL_MODELS_PATH="$MODELS"
echo "Fetching the $MODEL weights into $MODELS (the only download; jobs run offline)..."
"$PY" - "$ENGINE" "$MODEL" <<'PYCODE'
import sys
from cellpose import models
engine, model = sys.argv[1], sys.argv[2]
if engine == "cellpose3":
    models.Cellpose(gpu=False, model_type=model)
else:
    models.CellposeModel(gpu=False, pretrained_model=model)
print("weights ready")
PYCODE
LOCK="$PREFIX/cellquant-environment.lock"
{
  echo "# CellQuant cluster environment $RUNTIME_ID, built $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# python: $("$PY" -c 'import platform; print(platform.python_version())')"
  echo "# torch index: $TORCH_INDEX"
  "$PY" -m pip freeze --all
} > "$LOCK"
"$PY" -m cellquant.hpc runtime-inspect --engine "$ENGINE" --model "$MODEL" --runtime-id "$RUNTIME_ID" \
  --lock "$LOCK" --models-dir "$MODELS" --output "$PREFIX/$RUNTIME_ID.runtime.json"
chmod -R a-w "$PREFIX/lib" "$MODELS" || true
echo
echo "Environment:      $PREFIX"
echo "Python:           $PY"
echo "Dependency lock:  $LOCK (keep it; the runtime contract records its SHA-256)"
echo "Runtime contract: $PREFIX/$RUNTIME_ID.runtime.json (no modes enabled yet)"
echo "Next: GPU preflight and parity checks, then enable validated modes and set runtime_validation_date."
