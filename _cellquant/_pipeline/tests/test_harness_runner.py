from types import SimpleNamespace

from cellquant.harness import runner


def test_environment_provenance_is_complete_when_gpu_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi absent")

    monkeypatch.setattr(runner.subprocess, "run", unavailable)
    provenance = runner._environment_provenance()
    assert provenance["cpu"]["logical_cores"] >= 1
    assert provenance["ram"]["total_bytes"] > 0
    assert provenance["gpu"]["available"] is False
    assert "torch_version" in provenance
    assert "cellpose_version" in provenance
    assert isinstance(provenance["packages"], dict)


def test_gpu_inventory_parses_driver_cuda_and_memory(monkeypatch):
    responses = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout="Example GPU, 555.42, 24576\n",
            ),
            SimpleNamespace(returncode=0, stdout="NVIDIA-SMI 555  CUDA Version: 12.5"),
        ]
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: next(responses))
    gpu = runner._gpu_environment()
    assert gpu["available"] is True
    assert gpu["driver_version"] == "555.42"
    assert gpu["cuda_runtime_version"] == "12.5"
    assert gpu["devices"][0]["memory_total_mib"] == 24576


def test_source_and_installed_versions_are_distinguished(monkeypatch):
    import cellquant

    monkeypatch.setattr(cellquant, "__version__", "9.9.9")
    original = runner._distribution_version

    def version(name):
        return "0.1.0" if name == "cellquant" else original(name)

    monkeypatch.setattr(runner, "_distribution_version", version)
    source = runner._source_provenance()
    assert source["source_version"] == "9.9.9"
    assert source["installed_distribution_version"] == "0.1.0"
    assert source["version_mismatch"] is True
    assert source["warnings"]
    assert len(source["source_fingerprint_sha256"]) == 64
    assert source["source_files_hashed"] > 0
    assert source["exact_reproducibility_claimed"] is False
