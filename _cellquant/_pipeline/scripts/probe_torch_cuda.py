"""Probe whether this env's PyTorch can use CUDA. Prints version|cuda|available|name."""

from __future__ import annotations

import torch

fields = [
    torch.__version__,
    str(torch.version.cuda or ""),
    str(torch.cuda.is_available()),
    torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
]
print("|".join(fields))
