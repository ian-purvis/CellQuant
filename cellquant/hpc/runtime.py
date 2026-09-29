"""The cluster software: the application build digest, the runtime contract, and checks against it.

The application build digest identifies the CellQuant source that runs the
analysis. It covers every release file under ``cellquant/`` (sorted POSIX
paths; text files with LF line endings; binary files as they are), so a
Windows copy and a Linux copy of one release have the same digest. Bytecode,
caches and OS metadata are left out.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
from pathlib import Path
from typing import Any

from cellquant.hpc.common import canonical_json, sha256_bytes, sha256_file, sha256_json, utc_now
from cellquant.hpc.models import Issue, RuntimeContract

TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".yaml", ".yml", ".txt", ".sh", ".sbatch", ".toml", ".cfg", ".ini", ".csv", ".rst"})
EXCLUDED_NAMES = frozenset({"__pycache__", ".DS_Store", "Thumbs.db", "desktop.ini", ".pytest_cache", ".mypy_cache"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".egg-info", ".tmp", ".orig", ".rej"})

# Weight files each built-in Cellpose model reads from the model folder.
# Cellpose 3 keeps a size model next to each network; Cellpose 4 models are one file.
CELLPOSE3_MODEL_FILES = {
    "nuclei": ("nucleitorch_0", "size_nucleitorch_0.npy"),
    "cyto3": ("cyto3", "size_cyto3.npy"),
    "cyto2": ("cyto2torch_0", "size_cyto2torch_0.npy"),
    "cyto": ("cytotorch_0", "size_cytotorch_0.npy"),
}


def package_root() -> Path:
    import cellquant

    return Path(cellquant.__file__).resolve().parent


def build_manifest(root: str | Path | None = None) -> list[tuple[str, str]]:
    """(relative POSIX path, SHA-256 of normalized content) for every release file, sorted."""

    base = Path(root) if root is not None else package_root()
    entries = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in EXCLUDED_NAMES or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name.startswith("."):
            continue
        data = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            data = data.replace(b"\r\n", b"\n")
        entries.append(("cellquant/" + relative.as_posix(), sha256_bytes(data)))
    return sorted(entries)


def application_build_sha256(root: str | Path | None = None) -> str:
    return sha256_bytes(canonical_json(build_manifest(root)))


def model_directory() -> Path:
    """Where Cellpose looks for weights: CELLPOSE_LOCAL_MODELS_PATH, else ~/.cellpose/models."""

    configured = os.environ.get("CELLPOSE_LOCAL_MODELS_PATH")
    return Path(configured) if configured else Path.home() / ".cellpose" / "models"


def model_file_names(engine: str, model: str) -> tuple[str, ...]:
    if engine == "cellpose3":
        if model not in CELLPOSE3_MODEL_FILES:
            raise ValueError(f"Classic Cellpose model '{model}' is not one of {', '.join(CELLPOSE3_MODEL_FILES)}.")
        return CELLPOSE3_MODEL_FILES[model]
    if "/" in model or "\\" in model:
        raise ValueError("Give the model as a file name in the model folder, not a path.")
    return (model,)


def installed_packages() -> dict[str, str]:
    packages: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():  # in sys.path order
        name = (distribution.metadata.get("Name") or "").strip().lower().replace("_", "-")
        # The first copy on the path is the one Python imports; later copies are shadowed.
        if name and name not in packages:
            packages[name] = distribution.version
    return dict(sorted(packages.items()))


def observed_runtime(engine: str, model: str, *, models_dir: str | Path | None = None) -> dict[str, Any]:
    """What is actually installed here. Does not import PyTorch or load a model."""

    directory = Path(models_dir) if models_dir else model_directory()
    files = {}
    missing = []
    for name in model_file_names(engine, model):
        path = directory / name
        if path.is_file():
            files[name] = sha256_file(path)
        else:
            missing.append(name)
    packages = installed_packages()
    return {
        "python_version": platform.python_version(),
        "application_build_sha256": application_build_sha256(),
        "packages": packages,
        "cellpose_version": packages.get("cellpose"),
        "engine": engine,
        "model": model,
        "model_directory": str(directory),
        "model_files": files,
        "missing_model_files": missing,
        "platform": platform.platform(),
        "executable": sys.executable,
    }


def fingerprint(values: dict[str, Any]) -> str:
    """One digest over the parts of a runtime that must match: Python, build, packages, engine, model files."""

    return sha256_json(
        {
            "python_version": values.get("python_version"),
            "application_build_sha256": values.get("application_build_sha256"),
            "packages": values.get("packages"),
            "engine": values.get("engine"),
            "cellpose_version": values.get("cellpose_version"),
            "model": values.get("model"),
            "model_files": values.get("model_files"),
        }
    )


def contract_fingerprint(contract: RuntimeContract) -> str:
    return fingerprint(contract.model_dump(mode="json"))


def compare_runtime(contract: RuntimeContract, observed: dict[str, Any]) -> list[Issue]:
    """Every way the installed software differs from the contract. Empty means it matches exactly."""

    issues: list[Issue] = []

    def mismatch(code: str, what: str, expected, found, fix: str) -> None:
        issues.append(Issue(code=code, message=f"{what}: the package needs {expected}, this environment has {found}.", fix=fix))

    if observed.get("python_version") != contract.python_version:
        mismatch("E_RUNTIME_PYTHON", "Python", contract.python_version, observed.get("python_version"), "Use the environment named in the profile.")
    if observed.get("application_build_sha256") != contract.application_build_sha256:
        mismatch(
            "E_RUNTIME_BUILD",
            "CellQuant build",
            contract.application_build_sha256[:12],
            str(observed.get("application_build_sha256"))[:12],
            "Install the CellQuant release the runtime contract was made from.",
        )
    if observed.get("engine") != contract.engine or observed.get("cellpose_version") != contract.cellpose_version:
        mismatch(
            "E_RUNTIME_ENGINE",
            "Cellpose",
            f"{contract.engine} {contract.cellpose_version}",
            f"{observed.get('engine')} {observed.get('cellpose_version')}",
            "Use the environment made for this engine.",
        )
    if observed.get("model") != contract.model:
        mismatch("E_RUNTIME_MODEL", "Model", contract.model, observed.get("model"), "Prepare the package for the model the runtime provides.")
    for name in observed.get("missing_model_files") or []:
        issues.append(
            Issue(
                code="E_RUNTIME_MODEL_MISSING",
                message=f"Model file {name} is not in {observed.get('model_directory')}.",
                fix="Copy the model weights into the model folder during setup. Jobs never download models.",
            )
        )
    for name, digest in contract.model_files.items():
        found = (observed.get("model_files") or {}).get(name)
        if found is not None and found != digest:
            mismatch("E_RUNTIME_MODEL_HASH", f"Model file {name}", digest[:12], found[:12], "Restore the exact weights the contract lists.")
    expected_packages = contract.packages
    found_packages = observed.get("packages") or {}
    differing = sorted(
        name for name in set(expected_packages) | set(found_packages) if expected_packages.get(name) != found_packages.get(name)
    )
    if differing:
        shown = ", ".join(f"{name} ({expected_packages.get(name) or 'absent'} vs {found_packages.get(name) or 'absent'})" for name in differing[:8])
        more = f" and {len(differing) - 8} more" if len(differing) > 8 else ""
        issues.append(
            Issue(
                code="E_RUNTIME_PACKAGES",
                message=f"Installed packages differ from the contract: {shown}{more}.",
                fix="Rebuild the environment from the dependency lock, or export a new runtime contract and prepare a new package.",
            )
        )
    return issues


def inspect_runtime(
    *,
    engine: str,
    model: str,
    runtime_id: str,
    lock_file: str | Path,
    supported_modes: list[str] | None = None,
    models_dir: str | Path | None = None,
    validation_date: str | None = None,
) -> RuntimeContract:
    """Describe this environment as a runtime contract. Records what is installed; validates nothing."""

    observed = observed_runtime(engine, model, models_dir=models_dir)
    if observed["missing_model_files"]:
        raise FileNotFoundError(
            f"Model files missing from {observed['model_directory']}: {', '.join(observed['missing_model_files'])}. "
            "Download the weights into the model folder first."
        )
    if not observed["cellpose_version"]:
        raise RuntimeError("Cellpose is not installed in this environment.")
    from cellquant.engines import engine_key_for_version

    if engine_key_for_version(observed["cellpose_version"]) != engine:
        raise RuntimeError(f"This environment has Cellpose {observed['cellpose_version']}, not {engine}.")
    lock = Path(lock_file)
    if not lock.is_file():
        raise FileNotFoundError(f"Dependency lock file not found: {lock}")
    extra: dict[str, Any] = {"platform": observed["platform"], "executable": observed["executable"], "inspected_at": utc_now()}
    try:  # PyTorch details are informative; the contract does not depend on a GPU being visible here
        import torch

        extra["torch_version"] = torch.__version__
        extra["torch_cuda"] = getattr(torch.version, "cuda", None)
        extra["torch_arch_list"] = list(torch.cuda.get_arch_list()) if hasattr(torch.cuda, "get_arch_list") else []
    except Exception as exc:  # noqa: BLE001
        extra["torch_error"] = str(exc)
    return RuntimeContract(
        runtime_id=runtime_id,
        python_version=observed["python_version"],
        application_build_sha256=observed["application_build_sha256"],
        dependency_lock_sha256=sha256_file(lock),
        packages=observed["packages"],
        engine=engine,  # type: ignore[arg-type]
        cellpose_version=observed["cellpose_version"],
        model=model,
        model_files=observed["model_files"],
        model_directory=Path(observed["model_directory"]).as_posix(),
        supported_modes=list(supported_modes or []),  # type: ignore[arg-type]
        runtime_validation_date=validation_date,
        observed=extra,
    )


def gpu_report() -> dict[str, Any]:
    """The GPU PyTorch sees, for the preflight record. Imports PyTorch."""

    info: dict[str, Any] = {"available": False}
    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"PyTorch could not be loaded: {exc}"
        return info
    info["torch_version"] = torch.__version__
    info["cuda_build"] = getattr(torch.version, "cuda", None)
    try:
        info["available"] = bool(torch.cuda.is_available())
        if info["available"]:
            properties = torch.cuda.get_device_properties(0)
            info["name"] = torch.cuda.get_device_name(0)
            info["memory_gib"] = round(properties.total_memory / 1024**3, 1)
            info["capability"] = ".".join(str(value) for value in torch.cuda.get_device_capability(0))
            info["arch_list"] = list(torch.cuda.get_arch_list())
            info["device_count"] = torch.cuda.device_count()
    except Exception as exc:  # noqa: BLE001
        info["available"] = False
        info["error"] = str(exc)
    driver = _nvidia_driver()
    if driver:
        info["driver"] = driver
    info["cuda_visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    return info


def _nvidia_driver() -> str:
    import shutil
    import subprocess

    tool = shutil.which("nvidia-smi")
    if not tool:
        return ""
    try:
        output = subprocess.run(
            [tool, "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=20, check=False
        )
    except Exception:  # noqa: BLE001
        return ""
    return output.stdout.strip().splitlines()[0] if output.stdout.strip() else ""
