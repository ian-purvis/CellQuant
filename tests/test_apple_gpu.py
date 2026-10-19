"""Apple silicon Macs: Cellpose uses the Apple GPU (MPS), and the Mac launcher says what the Mac can do."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from cellquant import engines, hardware, segmentation
from cellquant.segmentation import segment_objects
from tests.test_cellpose_engines import _V4, _install_fake

ROOT = Path(__file__).resolve().parents[1]
UNIX = ROOT / "packaging" / "unix"

# The stand-in records the device it was given and, like a missing MPS operation, fails on "mps"
# when FAIL_ON_MPS is set.
_V4_DEVICE = _V4.replace(
    'CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, pretrained_model=pretrained_model, model_type=model_type)))\n'
    '        self.device = types.SimpleNamespace(type="cpu")',
    'CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, device=device)))\n'
    '        self.device = types.SimpleNamespace(type=str(device or "cpu"))',
).replace(
    '        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))',
    '        CALLS.append(("CellposeModel.eval", dict(diameter=diameter, channels=channels)))\n'
    '        if FAIL_ON_MPS and self.device.type == "mps":\n'
    '            raise NotImplementedError("aten::op is not implemented for MPS")',
).replace("CALLS = []", "CALLS = []\nFAIL_ON_MPS = False")


def _torch(cuda: bool, mps: bool):
    return types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
        device=lambda name: name,
        version=types.SimpleNamespace(cuda=None),
    )


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(segmentation, "_MODELS", {})
    monkeypatch.setattr(segmentation, "_APPLE_GPU_FAILED", False)
    monkeypatch.setattr(hardware, "_BF16", {})
    monkeypatch.setattr(hardware, "_THREADS_APPLIED", set())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    engines.cellpose_engine.cache_clear()
    yield
    engines.cellpose_engine.cache_clear()


def test_the_apple_gpu_counts_as_a_usable_gpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _torch(cuda=False, mps=True))
    monkeypatch.setattr(engines, "apple_chip_name", lambda: "Apple M2 Pro GPU")
    status = engines.gpu_status()
    assert status["available"] is True and status["kind"] == "mps" and status["name"] == "Apple M2 Pro GPU"
    assert engines.gpu_device(True) == "mps" and engines.gpu_device(False) is None


def test_an_intel_mac_is_told_why_there_is_no_gpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _torch(cuda=False, mps=False))
    monkeypatch.setattr(engines.sys, "platform", "darwin")
    monkeypatch.setattr(engines, "nvidia_gpu_name", lambda: "")
    status = engines.gpu_status()
    assert status["available"] is False and "Apple silicon" in status["reason"]


def test_cuda_is_left_to_cellpose(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _torch(cuda=True, mps=True))
    assert engines.gpu_device(True) is None  # Cellpose picks CUDA itself, as before


def test_cellpose_runs_on_the_apple_gpu_in_float32(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_DEVICE)
    monkeypatch.setattr(engines, "gpu_device", lambda gpu: "mps" if gpu else None)
    monkeypatch.setitem(sys.modules, "torch", _torch(cuda=False, mps=True))
    details: dict = {}
    segment_objects(np.zeros((20, 20), dtype=np.float32), "cellpose", {"gpu": True}, details=details)
    assert models.CALLS[0] == ("CellposeModel.__init__", {"gpu": True, "device": "mps"})
    assert details["engine"]["device"] == "mps" and "warning" not in details["engine"]
    assert details["engine"]["precision"] == "float32"  # bfloat16 only where CUDA or the CPU does it natively


def test_a_step_the_apple_gpu_cannot_do_runs_again_on_the_cpu(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_DEVICE)
    models.FAIL_ON_MPS = True
    monkeypatch.setattr(engines, "gpu_device", lambda gpu: "mps" if gpu else None)
    details: dict = {}
    labels = segment_objects(np.zeros((20, 20), dtype=np.float32), "cellpose", {"gpu": True}, details=details)
    assert labels.max() == 2
    assert [options["device"] for name, options in models.CALLS if name == "CellposeModel.__init__"] == ["mps", None]
    assert details["engine"]["device"] == "cpu" and "Apple GPU could not run" in details["engine"]["warning"]
    # The next image goes straight to the CPU instead of failing on the Apple GPU again.
    details = {}
    segment_objects(np.zeros((20, 20), dtype=np.float32), "cellpose", {"gpu": True}, details=details)
    assert [name for name, _options in models.CALLS].count("CellposeModel.__init__") == 2
    assert details["engine"]["device"] == "cpu" and "failed earlier" in details["engine"]["warning"]


def test_loading_the_model_onto_the_apple_gpu_can_fail_too(tmp_path, monkeypatch):
    from tests.test_3d import _recording

    source = _recording(_V4).replace(
        'CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, pretrained_model=pretrained_model, model_type=model_type)))',
        'CALLS.append(("CellposeModel.__init__", dict(gpu=gpu, device=device)))',
    )
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", source)
    monkeypatch.setattr(engines, "gpu_device", lambda gpu: "mps" if gpu else None)
    original = models.CellposeModel.__init__

    def init(self, *args, device=None, **kwargs):
        if device == "mps":
            raise RuntimeError("MPS backend out of memory")
        original(self, *args, device=device, **kwargs)

    monkeypatch.setattr(models.CellposeModel, "__init__", init)
    volume = np.random.default_rng(0).random((3, 20, 20)).astype(np.float32)
    details: dict = {}
    from cellquant.volume import segment_volume

    segment_volume(volume, "cellpose", {"gpu": True, "diameter_px": 10}, "full_3d", details=details)
    assert details["engine"]["device"] == "cpu" and "out of memory" in details["engine"]["warning"]


def test_intel_macs_keep_numpy_1_for_their_pytorch():
    installer = (UNIX / "install_unix.sh").read_text()
    assert "[ \"$(cq_platform)\" = 'MacOSX-x86_64' ] && pins='numpy<2'" in installer
    assert installer.count('"$requirement" $pins') == 2


def test_other_failures_are_not_retried(tmp_path, monkeypatch):
    models = _install_fake(tmp_path, monkeypatch, "4.2.1.1", _V4_DEVICE)
    models.FAIL_ON_MPS = True
    monkeypatch.setattr(engines, "gpu_device", lambda gpu: None)  # no Apple GPU here
    monkeypatch.setattr(models.CellposeModel, "eval", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(segmentation.SegmentationError, match="boom"):
        segment_objects(np.zeros((20, 20), dtype=np.float32), "cellpose", {"gpu": True})


def _notice(tmp_path: Path, chip: str, arm64: str, memory_gb: int, python_arch: str = "") -> str:
    """Run cq_mac_notice with stand-ins for the Mac's uname, sysctl and file."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "uname").write_text('#!/bin/sh\n[ "$1" = "-m" ] && echo arm64 || echo Darwin\n')
    (bin_dir / "sysctl").write_text(
        "#!/bin/sh\ncase \"$2\" in\n"
        f"  hw.optional.arm64) echo {arm64} ;;\n"
        f"  machdep.cpu.brand_string) echo '{chip}' ;;\n"
        f"  hw.memsize) echo {memory_gb * 1073741824} ;;\nesac\n"
    )
    (bin_dir / "file").write_text(f"#!/bin/sh\necho 'python: Mach-O 64-bit executable {python_arch}'\n")
    for name in ("uname", "sysctl", "file"):
        (bin_dir / name).chmod(0o755)
    prefix = tmp_path / "env"
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    (prefix / "bin" / "python").write_text("")
    script = f'. "{UNIX / "common.sh"}"; cq_mac_notice "{prefix if python_arch else ""}"'
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, check=True
    )
    return result.stdout


def test_mac_launcher_notice_on_apple_silicon(tmp_path):
    text = _notice(tmp_path, "Apple M2 Pro", "1", 32, "arm64")
    assert "This Mac: Apple M2 Pro, 32 GB memory." in text
    assert "Cellpose will use the Apple GPU" in text and "Classic Cellpose (faster)" in text
    assert "may not fit" not in text


def test_mac_launcher_notice_on_an_intel_mac(tmp_path):
    text = _notice(tmp_path, "Intel(R) Core(TM) i7-8850H CPU @ 2.60GHz", "0", 8)
    assert "no GPU that Cellpose can use" in text and "Classic Cellpose (faster without a GPU)" in text
    assert "With 8 GB of memory" in text


def test_mac_launcher_notice_for_an_intel_install_on_apple_silicon(tmp_path):
    text = _notice(tmp_path, "Apple M1", "1", 16, "x86_64")
    assert "installed for Intel Macs" in text and "[R] Reinstall" in text


def test_rosetta_terminal_still_gets_the_native_miniforge(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uname").write_text('#!/bin/sh\n[ "$1" = "-m" ] && echo x86_64 || echo Darwin\n')
    (bin_dir / "sysctl").write_text("#!/bin/sh\necho 1\n")
    for name in ("uname", "sysctl"):
        (bin_dir / name).chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", f'. "{UNIX / "common.sh"}"; cq_platform'],
        capture_output=True, text=True, env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, check=True,
    )
    assert result.stdout.strip() == "MacOSX-arm64"
