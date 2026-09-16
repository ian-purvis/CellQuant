"""CUDA compute-capability checks and remediation for CellQuant.

``torch.cuda.is_available()`` is not enough: Blackwell (sm_120) GPUs can
report available while the installed wheel only ships through sm_90. Prefer a
CUDA 12.8+ (cu128) PyTorch wheel, which still includes older arches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Preferred PyTorch CUDA index for Blackwell + older GPUs in one env.
PREFERRED_TORCH_CUDA_TAG = "cu128"
PREFERRED_TORCH_CUDA_INDEX = f"https://download.pytorch.org/whl/{PREFERRED_TORCH_CUDA_TAG}"


@dataclass(frozen=True)
class CudaArchStatus:
    """Result of probing whether this torch build can run on the visible GPU."""

    ok: bool
    detail: str
    remediation: str
    device_name: str | None = None
    required_sm: str | None = None
    arch_list: tuple[str, ...] = ()


def sm_tag_from_capability(major: int, minor: int) -> str:
    """Map ``get_device_capability`` to a torch arch tag (e.g. (12, 0) → sm_120)."""

    return f"sm_{int(major)}{int(minor)}"


def windows_cu128_remediation() -> str:
    return (
        "Fix: update the NVIDIA driver (CUDA Version ≥ 12.8 in nvidia-smi), then "
        "re-run Install CellQuant.bat and choose Update so resolve_cuda_torch "
        f"installs a {PREFERRED_TORCH_CUDA_TAG}+ PyTorch wheel."
    )


def alpine_cu128_remediation(env_prefix: str | None = None) -> str:
    prefix = (env_prefix or "${CONDA_PREFIX}").rstrip("/\\")
    python = f"{prefix}/bin/python"
    return (
        "Fix on a login node (do not pip-install inside the batch job): upgrade "
        "the shared CellQuant HPC env to a CUDA 12.8+ PyTorch wheel that includes "
        "sm_120 (still supports A100/L40/H200):\n"
        f'  "{python}" -m pip install --upgrade torch '
        f"--index-url {PREFERRED_TORCH_CUDA_INDEX}\n"
        f'  "{python}" -c "import torch; print(torch.__version__, '
        f'torch.version.cuda, torch.cuda.get_arch_list())"\n'
        "Confirm get_arch_list() includes the GPU's sm_ tag, then resubmit. "
        "One upgraded env covers all Alpine GPU partitions."
    )


def _arch_list_covers(required_sm: str, arch_list: list[str] | tuple[str, ...]) -> bool:
    arches = {str(a) for a in arch_list}
    if required_sm in arches:
        return True
    # Some builds list compute_XX alongside sm_XX.
    if required_sm.startswith("sm_"):
        compute = "compute_" + required_sm[3:]
        if compute in arches:
            return True
    return False


def _remediation_for(platform: str, env_prefix: str | None) -> str:
    key = (platform or "generic").casefold()
    if key in {"windows", "win32", "win"}:
        return windows_cu128_remediation()
    if key in {"alpine", "hpc", "linux"}:
        return alpine_cu128_remediation(env_prefix)
    return (
        f"Install/upgrade PyTorch from {PREFERRED_TORCH_CUDA_INDEX} "
        "(CUDA 12.8+ wheel; includes Blackwell sm_120 and older GPUs), "
        "then re-check torch.cuda.get_arch_list()."
    )


def check_cuda_device(
    *,
    torch_module: Any | None = None,
    allocate_smoke: bool = False,
    platform: str = "generic",
    env_prefix: str | None = None,
) -> CudaArchStatus:
    """Return whether the active torch build supports the visible CUDA device.

    Parameters
    ----------
    torch_module
        Injected torch-like module for tests; defaults to importing ``torch``.
    allocate_smoke
        When True and the arch list matches, allocate a 1-element CUDA tensor.
    platform
        ``windows``, ``alpine``/``hpc``, or ``generic`` — selects remediation text.
    env_prefix
        Optional conda prefix for Alpine remediation commands.
    """

    remediation = _remediation_for(platform, env_prefix)

    if torch_module is None:
        try:
            import torch as torch_module
        except Exception as exc:  # noqa: BLE001
            return CudaArchStatus(
                ok=False,
                detail=f"PyTorch not importable ({exc})",
                remediation=remediation,
            )

    try:
        available = bool(torch_module.cuda.is_available())
    except Exception as exc:  # noqa: BLE001
        return CudaArchStatus(
            ok=False,
            detail=f"torch.cuda.is_available() failed ({exc})",
            remediation=remediation,
        )

    if not available:
        return CudaArchStatus(
            ok=False,
            detail="torch.cuda.is_available() is False",
            remediation=remediation,
        )

    try:
        name = str(torch_module.cuda.get_device_name(0))
    except Exception:  # noqa: BLE001
        name = "CUDA device 0"

    try:
        major, minor = torch_module.cuda.get_device_capability(0)
        required_sm = sm_tag_from_capability(int(major), int(minor))
    except Exception as exc:  # noqa: BLE001
        return CudaArchStatus(
            ok=False,
            detail=f"Could not read CUDA device capability ({exc})",
            remediation=remediation,
            device_name=name,
        )

    try:
        raw_arches = list(torch_module.cuda.get_arch_list())
    except Exception as exc:  # noqa: BLE001
        return CudaArchStatus(
            ok=False,
            detail=f"Could not read torch.cuda.get_arch_list() ({exc})",
            remediation=remediation,
            device_name=name,
            required_sm=required_sm,
        )

    arch_tuple = tuple(str(a) for a in raw_arches)
    if not _arch_list_covers(required_sm, arch_tuple):
        supported = " ".join(arch_tuple) if arch_tuple else "(none)"
        return CudaArchStatus(
            ok=False,
            detail=(
                f"{name} requires {required_sm}, but this PyTorch build only "
                f"supports: {supported}. Prefer a {PREFERRED_TORCH_CUDA_TAG}+ "
                "wheel (CUDA 12.8+)."
            ),
            remediation=remediation,
            device_name=name,
            required_sm=required_sm,
            arch_list=arch_tuple,
        )

    if allocate_smoke:
        try:
            torch_module.zeros(1, device="cuda")
        except Exception as exc:  # noqa: BLE001
            return CudaArchStatus(
                ok=False,
                detail=(
                    f"{name} is listed as {required_sm}-compatible but a CUDA "
                    f"tensor allocate failed ({exc})"
                ),
                remediation=remediation,
                device_name=name,
                required_sm=required_sm,
                arch_list=arch_tuple,
            )

    return CudaArchStatus(
        ok=True,
        detail=f"{name} ({required_sm})",
        remediation="",
        device_name=name,
        required_sm=required_sm,
        arch_list=arch_tuple,
    )
