"""Unit tests for CUDA arch compatibility (no real GPU required)."""

from __future__ import annotations

from types import SimpleNamespace

from cellquant.runtime.cuda_compat import (
    PREFERRED_TORCH_CUDA_INDEX,
    alpine_cu128_remediation,
    check_cuda_device,
    sm_tag_from_capability,
    windows_cu128_remediation,
)


class _FakeCuda:
    def __init__(
        self,
        *,
        available: bool = True,
        name: str = "Fake GPU",
        capability: tuple[int, int] = (8, 0),
        arch_list: list[str] | None = None,
        allocate_error: Exception | None = None,
    ):
        self._available = available
        self._name = name
        self._capability = capability
        self._arch_list = arch_list if arch_list is not None else ["sm_50", "sm_80", "sm_90"]
        self._allocate_error = allocate_error

    def is_available(self) -> bool:
        return self._available

    def get_device_name(self, _index: int = 0) -> str:
        return self._name

    def get_device_capability(self, _index: int = 0) -> tuple[int, int]:
        return self._capability

    def get_arch_list(self) -> list[str]:
        return list(self._arch_list)


class _FakeTorch:
    def __init__(self, cuda: _FakeCuda):
        self.cuda = cuda

    def zeros(self, *_args, device=None, **_kwargs):
        if device == "cuda" and self.cuda._allocate_error is not None:
            raise self.cuda._allocate_error
        return SimpleNamespace(device=device)


def test_sm_tag_from_capability():
    assert sm_tag_from_capability(12, 0) == "sm_120"
    assert sm_tag_from_capability(8, 0) == "sm_80"
    assert sm_tag_from_capability(7, 5) == "sm_75"


def test_check_ok_when_arch_present():
    torch = _FakeTorch(
        _FakeCuda(
            name="NVIDIA H200",
            capability=(9, 0),
            arch_list=["sm_80", "sm_90", "sm_120"],
        )
    )
    status = check_cuda_device(torch_module=torch, platform="alpine")
    assert status.ok is True
    assert status.required_sm == "sm_90"
    assert "H200" in status.detail
    assert status.remediation == ""


def test_check_fails_blackwell_without_sm_120():
    torch = _FakeTorch(
        _FakeCuda(
            name="NVIDIA RTX PRO 6000 Blackwell Server Edition",
            capability=(12, 0),
            arch_list=["sm_50", "sm_80", "sm_90"],
        )
    )
    status = check_cuda_device(torch_module=torch, platform="alpine", env_prefix="/proj/env")
    assert status.ok is False
    assert status.required_sm == "sm_120"
    assert "sm_120" in status.detail
    assert "sm_90" in status.detail
    assert "cu128" in status.remediation
    assert PREFERRED_TORCH_CUDA_INDEX in status.remediation
    assert "/proj/env/bin/python" in status.remediation


def test_check_accepts_compute_tag_alias():
    torch = _FakeTorch(
        _FakeCuda(
            capability=(12, 0),
            arch_list=["compute_120"],
        )
    )
    status = check_cuda_device(torch_module=torch)
    assert status.ok is True
    assert status.required_sm == "sm_120"


def test_check_fails_when_cuda_unavailable():
    torch = _FakeTorch(_FakeCuda(available=False))
    status = check_cuda_device(torch_module=torch, platform="windows")
    assert status.ok is False
    assert "is_available" in status.detail
    assert "Install CellQuant.bat" in status.remediation


def test_allocate_smoke_failure():
    torch = _FakeTorch(
        _FakeCuda(
            capability=(8, 0),
            arch_list=["sm_80"],
            allocate_error=RuntimeError("cuda fail"),
        )
    )
    status = check_cuda_device(torch_module=torch, allocate_smoke=True)
    assert status.ok is False
    assert "allocate failed" in status.detail


def test_remediation_helpers():
    assert "cu128" in windows_cu128_remediation()
    alpine = alpine_cu128_remediation("/projects/u/cellquant/envs/cellquant-hpc")
    assert "pip install --upgrade torch" in alpine
    assert PREFERRED_TORCH_CUDA_INDEX in alpine
