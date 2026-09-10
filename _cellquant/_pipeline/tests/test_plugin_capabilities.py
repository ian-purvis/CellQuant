from cellquant.plugin.capabilities import detect_runtime_capabilities


def _ready(version="2.14.0+cpu"):
    return lambda: (True, version)


def test_detects_v4_cpu_only_environment():
    caps = detect_runtime_capabilities(
        cellpose_version_fn=lambda: "4.2.1.1",
        cuda_fn=lambda: (False, "2.14.0+cpu", "PyTorch is a CPU-only build"),
        torch_ready_fn=_ready(),
    )
    assert caps.cellpose_major == 4
    assert caps.cuda_available is False
    assert caps.torch_version == "2.14.0+cpu"
    assert [engine.engine_id for engine in caps.available_engines] == ["v4"]
    assert [device.device_id for device in caps.available_devices] == ["auto", "cpu"]
    assert caps.default_device_id == "auto"
    assert "PyTorch 2.14.0+cpu" in caps.summary
    assert "CPU-only" in caps.summary or "CPU build" in caps.summary
    # Classic v3 needs a separate Cellpose 3.x environment / launcher.
    v3 = next(engine for engine in caps.engines if engine.engine_id == "v3")
    assert v3.available is False
    assert "Requires Cellpose 3" in (v3.unavailable_reason or "")


def test_detects_cuda_device_options_when_available():
    caps = detect_runtime_capabilities(
        cellpose_version_fn=lambda: "4.0.1",
        cuda_fn=lambda: (True, "2.4.0+cu121", "NVIDIA GeForce"),
        torch_ready_fn=_ready("2.4.0+cu121"),
    )
    assert [device.device_id for device in caps.available_devices] == ["auto", "cuda", "cpu"]
    assert caps.default_device_id == "auto"
    assert caps.system_gpu_detected is True
    assert "GPU usable" in caps.summary
    assert "PyTorch 2.4.0+cu121" in caps.summary


def test_system_gpu_with_cpu_torch_explains_missing_cuda_wheel():
    caps = detect_runtime_capabilities(
        cellpose_version_fn=lambda: "4.2.1.1",
        cuda_fn=lambda: (
            False,
            "2.14.0+cpu",
            None,
            "NVIDIA GeForce GTX 1660 Ti detected, but PyTorch is a CPU-only build "
            "(2.14.0+cpu); install a CUDA-enabled torch wheel",
            True,
            "NVIDIA GeForce GTX 1660 Ti",
        ),
        torch_ready_fn=_ready(),
    )
    assert caps.cuda_available is False
    assert caps.system_gpu_detected is True
    assert caps.system_gpu_name == "NVIDIA GeForce GTX 1660 Ti"
    assert caps.torch_version == "2.14.0+cpu"
    assert [device.device_id for device in caps.available_devices] == ["auto", "cpu"]
    auto = next(device for device in caps.devices if device.device_id == "auto")
    assert "GPU present" in auto.label
    cuda = next(device for device in caps.devices if device.device_id == "cuda")
    assert cuda.available is False
    assert "CPU-only" in (cuda.unavailable_reason or "")
    assert "GTX 1660 Ti" in caps.summary
    assert "PyTorch 2.14.0+cpu" in caps.summary


def test_v3_only_env_offers_classic_engine():
    caps = detect_runtime_capabilities(
        cellpose_version_fn=lambda: "3.1.0",
        cuda_fn=lambda: (True, "2.1.0", "GPU"),
        torch_ready_fn=_ready("2.1.0"),
    )
    assert [engine.engine_id for engine in caps.available_engines] == ["v3"]
    assert caps.default_engine_id == "v3"
    assert "Cellpose classic" in caps.summary
    v4 = next(engine for engine in caps.engines if engine.engine_id == "v4")
    assert v4.available is False
    assert "Requires Cellpose 4" in (v4.unavailable_reason or "")


def test_missing_torch_does_not_advertise_runnable_engine():
    caps = detect_runtime_capabilities(
        cellpose_version_fn=lambda: "4.2.1.1",
        cuda_fn=lambda: (False, None, "PyTorch not found"),
        torch_ready_fn=lambda: (False, "PyTorch not importable (missing DLL)"),
    )
    assert caps.available_engines == ()
    assert "Runnable here" not in caps.summary
    assert "PyTorch not importable" in caps.summary
    v4 = next(engine for engine in caps.engines if engine.engine_id == "v4")
    assert v4.available is False
    assert "PyTorch" in (v4.unavailable_reason or "")
