"""Which Cellpose engine is installed in this environment.

CellQuant is installed once per engine: a Cellpose-SAM environment (Cellpose 4)
and a classic Cellpose environment (Cellpose 3). The two cannot share one
Python environment. This module reports which one is present without importing
PyTorch, so the app opens quickly. ``gpu_status`` imports PyTorch and should be
called off the interface thread.
"""

from __future__ import annotations

import functools
import importlib.metadata
from dataclasses import dataclass, field
from pathlib import Path

# Engine keys stored in recipes. They name the Cellpose major version.
CELLPOSE_SAM = "cellpose4"
CELLPOSE_CLASSIC = "cellpose3"

ENGINE_LABELS = {
    CELLPOSE_SAM: "Cellpose-SAM (Cellpose 4)",
    CELLPOSE_CLASSIC: "Classic Cellpose (Cellpose 3)",
}

# Built-in models offered in the interface. Cellpose 4 also accepts any name
# in its own MODEL_NAMES list or a path to a trained model file.
CLASSIC_MODELS = ("nuclei", "cyto3", "cyto2", "cyto")
CLASSIC_DEFAULT_MODEL = "nuclei"


@dataclass(frozen=True)
class CellposeEngine:
    installed: bool
    version: str | None = None
    key: str | None = None
    models: tuple[str, ...] = field(default_factory=tuple)
    default_model: str | None = None

    @property
    def label(self) -> str:
        if not self.installed or self.key is None:
            return "Cellpose is not installed in this environment"
        return f"{ENGINE_LABELS[self.key]}, version {self.version}"


def engine_key_for_version(version: str) -> str:
    major = int(str(version).split(".", 1)[0])
    if major >= 4:
        return CELLPOSE_SAM
    if major == 3:
        return CELLPOSE_CLASSIC
    raise ValueError(f"Cellpose {version} is not supported. Install Cellpose 3 or 4.")


@functools.lru_cache(maxsize=1)
def cellpose_engine() -> CellposeEngine:
    """Describe the installed Cellpose without importing it."""

    try:
        version = importlib.metadata.version("cellpose")
    except importlib.metadata.PackageNotFoundError:
        return CellposeEngine(installed=False)
    key = engine_key_for_version(version)
    if key == CELLPOSE_CLASSIC:
        return CellposeEngine(True, version, key, CLASSIC_MODELS, CLASSIC_DEFAULT_MODEL)
    models, default = _sam_models()
    return CellposeEngine(True, version, key, models, default)


def _sam_models() -> tuple[tuple[str, ...], str]:
    """Model names and the default model of the installed Cellpose 4.

    Read from the package source, so no PyTorch import is needed. The default
    changed within 4.x (``cpsam`` in early releases, ``cpsam_v2`` in 4.2), so it
    is looked up rather than assumed.
    """

    import ast

    # Locate the file from package metadata. Finding it through the import
    # system would run cellpose/__init__.py, which imports PyTorch.
    try:
        path = importlib.metadata.distribution("cellpose").locate_file("cellpose/models.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except (importlib.metadata.PackageNotFoundError, OSError, SyntaxError, TypeError):
        return ("cpsam",), "cpsam"
    names: tuple[str, ...] = ()
    default = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MODEL_NAMES" for target in node.targets
        ):
            try:
                names = tuple(str(item) for item in ast.literal_eval(node.value))
            except ValueError:
                names = ()
        if isinstance(node, ast.ClassDef) and node.name == "CellposeModel":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    arguments = item.args.args[1:]
                    defaults = item.args.defaults
                    offset = len(arguments) - len(defaults)
                    for index, argument in enumerate(arguments):
                        if argument.arg == "pretrained_model" and index >= offset:
                            value = defaults[index - offset]
                            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                                default = value.value
    if default is None:
        default = "cpsam" if "cpsam" in names or not names else names[0]
    if default not in names:
        names = (default, *names)
    # Offer the default first.
    ordered = (default, *[name for name in names if name != default])
    return ordered, default


_NVIDIA_SMI_FALLBACKS = (
    r"C:\Windows\System32\nvidia-smi.exe",
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
)
NO_GPU_BUILD_FIX = (
    "CellQuant's PyTorch was installed without GPU support. To add it, close CellQuant, run "
    "Install CellQuant.bat and choose [U] Update."
)


def nvidia_gpu_name() -> str:
    """The NVIDIA GPU nvidia-smi reports (the driver installs it), or "" when there is none."""

    import shutil
    import subprocess
    import sys

    candidates = [shutil.which("nvidia-smi")]
    if sys.platform.startswith("win"):
        candidates += list(_NVIDIA_SMI_FALLBACKS)
    for path in candidates:
        if not path or not Path(path).exists():
            continue
        try:
            output = subprocess.run(
                [path, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        names = [line.strip() for line in output.splitlines() if line.strip()]
        if names:
            return names[0]
    return ""


def gpu_status() -> dict[str, object]:
    """Whether PyTorch can use a CUDA GPU here. Imports PyTorch.

    When it cannot, ``nvidia_gpu`` names an NVIDIA GPU that is present anyway, and ``reason`` says how to use it.
    """

    status = _torch_gpu_status()
    if not status.get("available"):
        name = nvidia_gpu_name()
        if name:
            status["nvidia_gpu"] = name
            if "no GPU support" in str(status.get("reason", "")) or "could not be loaded" in str(status.get("reason", "")):
                status["reason"] = NO_GPU_BUILD_FIX
            else:
                status["reason"] = (
                    f"PyTorch cannot use it ({status.get('reason') or 'no CUDA device'}). Update the NVIDIA driver, "
                    "then run Install CellQuant.bat and choose [U] Update."
                )
    return status


def _torch_gpu_status() -> dict[str, object]:

    try:
        import torch
    except Exception as exc:  # noqa: BLE001 - reported to the user as text
        return {"available": False, "name": "", "reason": f"PyTorch could not be loaded: {exc}"}
    try:
        if torch.cuda.is_available():
            memory = None
            try:
                memory = torch.cuda.get_device_properties(0).total_memory / 1024**3
            except Exception:  # noqa: BLE001 - the name is enough to proceed
                memory = None
            return {"available": True, "name": torch.cuda.get_device_name(0), "memory_gb": memory, "reason": ""}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "name": "", "reason": str(exc)}
    build = getattr(torch.version, "cuda", None)
    reason = "This PyTorch build has no GPU support." if not build else "No usable NVIDIA GPU was found."
    return {"available": False, "name": "", "reason": reason}
