from pathlib import Path


def test_windows_launcher_uses_python_module_for_napari_gui():
    script = Path(__file__).parents[1] / "scripts" / "launch_napari.ps1"
    text = script.read_text(encoding="utf-8")
    assert "python -m napari" in text
    assert "run --no-capture-output -p $ResolvedPrefix napari\n" not in text


def test_installer_offers_open_instead_of_reinstalling_existing_pair():
    script = Path(__file__).parents[1] / "scripts" / "install_windows.ps1"
    text = script.read_text(encoding="utf-8")
    assert "$BothEnvironmentsExist" in text
    assert "Reinstall/update both environments?" in text
    assert "Skipping installation and opening CellQuant." in text
    assert "-File $Launcher -Engine ask" in text
    assert "-not $NonInteractive" in text


def test_installer_warns_and_reprompts_when_prefix_unusable():
    script = Path(__file__).parents[1] / "scripts" / "install_windows.ps1"
    text = script.read_text(encoding="utf-8")
    assert "Get-CellQuantInstallPrefixProblem" in text
    assert "Show-CellQuantInstallMessage" in text
    assert "CellQuant cannot install to this folder:" in text
    assert "Falling back to default:" in text
    assert "MessageBox" in text

    helpers = Path(__file__).parents[1] / "scripts" / "resolve_env_location.ps1"
    helper_text = helpers.read_text(encoding="utf-8")
    assert "function Get-CellQuantInstallPrefixProblem" in helper_text
    assert "another Windows user profile" in helper_text


def test_installer_installs_cuda_torch_on_nvidia_gpus():
    script = Path(__file__).parents[1] / "scripts" / "install_windows.ps1"
    text = script.read_text(encoding="utf-8")
    assert "resolve_cuda_torch.ps1" in text
    assert "Install-CellQuantCudaTorch" in text

    helpers = Path(__file__).parents[1] / "scripts" / "resolve_cuda_torch.ps1"
    helper_text = helpers.read_text(encoding="utf-8")
    assert "function Get-CellQuantTorchCudaTag" in helper_text
    assert "download.pytorch.org/whl" in helper_text
    assert "cu130" in helper_text
    assert "torch.cuda.is_available()" in helper_text
    assert "force-reinstall" in helper_text
    assert "probe_torch_cuda.py" in helper_text
    assert "numpy==2.0.2" in helper_text
