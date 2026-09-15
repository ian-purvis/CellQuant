from pathlib import Path


def test_windows_launcher_uses_python_module_for_napari_gui():
    script = Path(__file__).parents[1] / "scripts" / "launch_napari.ps1"
    text = script.read_text(encoding="utf-8")
    assert "python -m napari" in text
    assert "run --no-capture-output -p $ResolvedPrefix napari\n" not in text


def test_installer_offers_open_update_or_clean_reinstall_for_existing_envs():
    script = Path(__file__).parents[1] / "scripts" / "install_windows.ps1"
    text = script.read_text(encoding="utf-8")
    assert "$ExistingEnvPrefixes" in text
    assert "[O] Open CellQuant (no install changes)" in text
    assert "[U] Update existing environments in place" in text
    assert "[R] Uninstall (delete env folders), then reinstall clean" in text
    assert "Choice [O/U/R] (Enter = O)" in text
    assert "Skipping installation and opening CellQuant." in text
    assert "Remove-CellQuantEnvPrefix" in text
    assert "env remove --prefix" in text
    assert "$CleanReinstall" in text
    assert "-File $Launcher -Engine ask" in text
    assert "-not $NonInteractive" in text
    assert "Reinstall/update both environments?" not in text
    assert "$BothEnvironmentsExist" not in text


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


def test_install_bat_documents_open_update_and_clean_reinstall_choices():
    bat = Path(__file__).parents[1] / "Install CellQuant.bat"
    text = bat.read_text(encoding="utf-8")
    assert "install_windows.ps1" in text
    assert "[O] Open CellQuant" in text
    assert "[U] Update existing environments in place" in text
    assert "[R] Uninstall" in text
    assert "reinstall clean" in text


def test_installer_passes_conda_args_as_array_not_remaining_arguments():
    """Bare -p after Invoke-CondaChecked binds to -PipelineVariable in Windows PowerShell."""

    script = Path(__file__).parents[1] / "scripts" / "install_windows.ps1"
    text = script.read_text(encoding="utf-8")
    assert "ValueFromRemainingArguments = $true" not in text
    assert "[string[]]$CondaArgs" in text
    assert "-CondaArgs @(" in text
    assert "run --no-capture-output -p $EnvPrefix" not in text
    assert "'--prefix', $EnvPrefix" in text
