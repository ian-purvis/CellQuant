"""Probe whether this env's PyTorch can use the visible CUDA GPU.

Prints: version|cuda|available|name|arch_ok|required_sm|arch_list

``arch_ok`` is True only when the device compute capability appears in
``torch.cuda.get_arch_list()`` (Blackwell sm_120 needs a cu128+ wheel).
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
    except Exception:  # noqa: BLE001
        name = ""
    try:
        from cellquant.runtime.cuda_compat import check_cuda_device

        status = check_cuda_device(torch_module=torch, allocate_smoke=False, platform="windows")
        arch_ok = "True" if status.ok else "False"
        required_sm = status.required_sm or ""
        arch_list = " ".join(status.arch_list)
        if not status.ok and status.device_name:
            name = status.device_name
    except Exception:  # noqa: BLE001
        # Fallback without the helper (partial installs during bootstrap).
        try:
            major, minor = torch.cuda.get_device_capability(0)
            required_sm = f"sm_{major}{minor}"
            arches = list(torch.cuda.get_arch_list())
            arch_list = " ".join(str(a) for a in arches)
            arch_ok = "True" if required_sm in arches else "False"
        except Exception:  # noqa: BLE001
            arch_ok = "False"

print("|".join([version, cuda_build, str(available), name, arch_ok, required_sm, arch_list]))
