"""Can this environment's PyTorch use the NVIDIA GPU?

Prints one line: version|cuda_build|available|device_name|arch_ok|required_sm|arch_list

``arch_ok`` is True only when the GPU's compute capability is in
``torch.cuda.get_arch_list()``. Blackwell GPUs (sm_120) need a cu128+ build.
Adapted from CellQuant v1.
"""

from __future__ import annotations

try:
    import torch
except Exception as exc:  # noqa: BLE001
    print(f"ERROR|{exc}")
    raise SystemExit(1) from exc

version = torch.__version__
cuda_build = str(torch.version.cuda or "")
try:
    available = bool(torch.cuda.is_available())
except Exception:  # noqa: BLE001
    available = False

name = ""
arch_ok = "False"
required_sm = ""
arch_list = ""
if available:
    try:
        name = torch.cuda.get_device_name(0)
        major, minor = torch.cuda.get_device_capability(0)
        required_sm = f"sm_{major}{minor}"
        arches = [str(item) for item in torch.cuda.get_arch_list()]
        arch_list = " ".join(arches)
        arch_ok = "True" if required_sm in arches else "False"
    except Exception:  # noqa: BLE001
        arch_ok = "False"

print("|".join([version, cuda_build, str(available), name.replace("|", "/"), arch_ok, required_sm, arch_list]))
