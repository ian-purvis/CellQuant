param(
    [string]$Prefix = '',
    [switch]$NonInteractive,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not $LogPath) {
    $LogPath = Join-Path $ProjectRoot 'install_last.log'
}
try {
    Start-Transcript -LiteralPath $LogPath -Force | Out-Null
} catch {
    Write-Host "Warning: could not start install log at $LogPath ($($_.Exception.Message))" -ForegroundColor Yellow
}

. (Join-Path $PSScriptRoot 'resolve_conda.ps1')
. (Join-Path $PSScriptRoot 'resolve_env_location.ps1')
. (Join-Path $PSScriptRoot 'resolve_cuda_torch.ps1')

function Show-CellQuantInstallMessage {
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$Title = 'CellQuant install',
        [ValidateSet('Info', 'Warning', 'Error')][string]$Kind = 'Warning',
        [switch]$NoPopup
    )

    $Color = switch ($Kind) {
        'Error' { 'Red' }
        'Warning' { 'Yellow' }
        default { 'White' }
    }
    Write-Host ''
    foreach ($Line in ($Message -split "`r?`n")) {
        Write-Host $Line -ForegroundColor $Color
    }

    if ($NoPopup -or $NonInteractive) {
        return
    }

    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $Icon = switch ($Kind) {
            'Error' { [System.Windows.Forms.MessageBoxIcon]::Error }
            'Info' { [System.Windows.Forms.MessageBoxIcon]::Information }
            default { [System.Windows.Forms.MessageBoxIcon]::Warning }
        }
        [void][System.Windows.Forms.MessageBox]::Show(
            $Message,
            $Title,
            [System.Windows.Forms.MessageBoxButtons]::OK,
            $Icon
        )
    } catch {
        # No GUI available (remote/CI); console text above is enough.
    }
}

function Read-CellQuantInstallPrefixAnswer {
    param([Parameter(Mandatory)][string]$DefaultPrefix)

    $Answer = Read-Host 'Install folder for v4 (Enter = default, or paste a full path)'
    if ($Answer -and $Answer.Trim()) {
        return $Answer.Trim().Trim('"')
    }
    return $DefaultPrefix
}

try {
    $CondaExe = Resolve-CondaExecutable
} catch {
    Show-CellQuantInstallMessage -Kind Error -Message ((@(
        'Conda was not found on this PC.',
        '',
        'Install Miniconda or Miniforge, then run Install CellQuant.bat again.',
        '',
        "Details: $($_.Exception.Message)"
    ) -join [Environment]::NewLine))
    exit 1
}

$DefaultPrefix = Get-DefaultCellQuantEnvPrefix -CondaExe $CondaExe -Engine v4
$SavedPrefix = Read-CellQuantEnvPrefix -ProjectRoot $ProjectRoot -Engine v4
$SavedPrefixProblem = $null
if ($SavedPrefix) {
    $SavedPrefixProblem = Get-CellQuantInstallPrefixProblem -Prefix $SavedPrefix
}

if (-not $Prefix) {
    if (-not $NonInteractive) {
        Write-Host ''
        Write-Host 'CellQuant install location'
        Write-Host '--------------------------'
        Write-Host 'Two conda environments will be installed (sibling folders):'
        Write-Host '  • Cellpose-SAM v4  (heavier; current default)'
        Write-Host '  • Cellpose classic v3 (lighter — use if v4 is too slow or OOMs)'
        Write-Host ''
        Write-Host 'Choose the folder for the v4 environment. The v3 env is created'
        Write-Host 'next to it as <name>-v3 (for example cellquant-napari-v3).'
        Write-Host ''
        if ($SavedPrefix) {
            if ($SavedPrefixProblem) {
                Write-Host "Last used (v4):  $SavedPrefix" -ForegroundColor Yellow
                Write-Host "  (not usable on this PC: $SavedPrefixProblem)" -ForegroundColor Yellow
            } else {
                Write-Host "Last used (v4):  $SavedPrefix"
            }
        }
        Write-Host "Default (v4):    $DefaultPrefix"
        Write-Host ''
        $Prefix = Read-CellQuantInstallPrefixAnswer -DefaultPrefix $DefaultPrefix
    }
}

if (-not $Prefix) {
    # Non-interactive / no prompt answer: prefer a usable saved path, else default.
    if ($SavedPrefix -and -not $SavedPrefixProblem) {
        $Prefix = $SavedPrefix
        Write-Host "Using previously saved v4 location: $Prefix"
    } else {
        if ($SavedPrefix -and $SavedPrefixProblem) {
            Write-Host "Saved install path is not usable on this PC:" -ForegroundColor Yellow
            Write-Host "  $SavedPrefix" -ForegroundColor Yellow
            Write-Host "  $SavedPrefixProblem" -ForegroundColor Yellow
            Write-Host "Falling back to default: $DefaultPrefix" -ForegroundColor Yellow
        }
        $Prefix = $DefaultPrefix
        Write-Host "Using default v4 location: $Prefix"
    }
}

# Keep asking until the chosen folder is writable (or fail clearly in non-interactive).
while ($true) {
    $PrefixProblem = Get-CellQuantInstallPrefixProblem -Prefix $Prefix
    if (-not $PrefixProblem) {
        break
    }

    $Guidance = @(
        'CellQuant cannot install to this folder:',
        "  $Prefix",
        '',
        $PrefixProblem,
        '',
        'Choose a different install folder -- press Enter to use the default:',
        "  $DefaultPrefix",
        '',
        'Tip: paths under another user''s C:\Users\... folder will not work after',
        'copying CellQuant from a different PC or Windows account.'
    ) -join [Environment]::NewLine

    if ($NonInteractive) {
        if ($Prefix -ne $DefaultPrefix) {
            Write-Host $Guidance -ForegroundColor Yellow
            Write-Host "Falling back to default: $DefaultPrefix" -ForegroundColor Yellow
            $Prefix = $DefaultPrefix
            continue
        }
        Show-CellQuantInstallMessage -Kind Error -Message $Guidance
        exit 1
    }

    Show-CellQuantInstallMessage -Kind Warning -Message $Guidance
    $Prefix = Read-CellQuantInstallPrefixAnswer -DefaultPrefix $DefaultPrefix
}

$PrefixV4 = [System.IO.Path]::GetFullPath($Prefix)
$PrefixV3 = Get-SiblingV3Prefix -PrefixV4 $PrefixV4
$Saved = Save-CellQuantEnvPrefixes `
    -ProjectRoot $ProjectRoot `
    -PrefixV4 $PrefixV4 `
    -PrefixV3 $PrefixV3 `
    -DefaultEngine v4
$PrefixV4 = $Saved.prefix_v4
$PrefixV3 = $Saved.prefix_v3

$CleanReinstall = $false
$ExistingEnvPrefixes = @(
    @($PrefixV4, $PrefixV3) |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container }
)
if ($ExistingEnvPrefixes.Count -gt 0 -and -not $NonInteractive) {
    Write-Host ''
    Write-Host 'CellQuant environment folder(s) already exist:'
    foreach ($Existing in $ExistingEnvPrefixes) {
        Write-Host "  $Existing"
    }
    Write-Host ''
    Write-Host 'Choose what to do:'
    Write-Host '  [O] Open CellQuant (no install changes)'
    Write-Host '  [U] Update existing environments in place'
    Write-Host '  [R] Uninstall (delete env folders), then reinstall clean'
    Write-Host ''
    $Choice = Read-Host 'Choice [O/U/R] (Enter = O)'
    if (-not $Choice) {
        $Choice = 'O'
    }
    switch -Regex ($Choice.Trim()) {
        '^(?i)u(pdate)?$' {
            Write-Host 'Updating existing environments in place...'
        }
        '^(?i)r(einstall)?$' {
            $CleanReinstall = $true
            Write-Host 'Will uninstall existing environment folders, then reinstall clean.'
        }
        default {
            Write-Host 'Skipping installation and opening CellQuant.'
            $Launcher = Join-Path $PSScriptRoot 'launch_napari.ps1'
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher -Engine ask
            exit $LASTEXITCODE
        }
    }
}

try {
    function Invoke-CondaChecked {
        param(
            [Parameter(Mandatory)][string]$FailureMessage,
            # Pass conda argv as an explicit array. Do not use ValueFromRemainingArguments:
            # bare flags like -p bind to PowerShell common parameters (e.g. -PipelineVariable).
            [Parameter(Mandatory)][string[]]$CondaArgs
        )

        # Keep conda stderr (warnings) from becoming terminating errors under
        # $ErrorActionPreference = 'Stop' when streams are redirected by a host.
        $PreviousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $CondaExe @CondaArgs
            $Code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $PreviousEap
        }
        if ($Code -ne 0) {
            throw "$FailureMessage (exit code $Code)"
        }
    }

    function Remove-CellQuantEnvPrefix {
        param([Parameter(Mandatory)][string]$EnvPrefix)

        if (-not (Test-Path -LiteralPath $EnvPrefix)) {
            return
        }

        Write-Host "Uninstalling environment folder:"
        Write-Host "  $EnvPrefix"
        $PreviousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $CondaExe env remove --prefix $EnvPrefix --yes | Out-Host
        } finally {
            $ErrorActionPreference = $PreviousEap
        }
        # Conda on Windows often leaves the prefix folder behind; force-delete leftovers.
        if (Test-Path -LiteralPath $EnvPrefix) {
            Remove-Item -LiteralPath $EnvPrefix -Recurse -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $EnvPrefix) {
            throw "Could not delete environment folder: $EnvPrefix. Close Napari/Python using it and retry."
        }
        Write-Host "Removed: $EnvPrefix"
    }

    foreach ($Target in @($PrefixV4, $PrefixV3)) {
        $parent = Split-Path -Parent $Target
        if ($parent -and -not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
    }

    if ($CleanReinstall) {
        Write-Host ''
        Write-Host 'Uninstalling existing CellQuant environments before clean reinstall...'
        foreach ($Target in @($PrefixV4, $PrefixV3)) {
            Remove-CellQuantEnvPrefix -EnvPrefix $Target
        }
        Write-Host ''
    }

    $EnvFile = Join-Path $ProjectRoot 'environment.yml'

    function Install-OneCellQuantEnv {
        param(
            [Parameter(Mandatory)][string]$EnvPrefix,
            [Parameter(Mandatory)][ValidateSet('v4', 'v3')][string]$Engine
        )

        Write-Host ''
        Write-Host "Installing CellQuant ($Engine) environment at:"
        Write-Host "  $EnvPrefix"
        Write-Host 'This can take several minutes...'
        Write-Host ''

        if (Test-Path -LiteralPath $EnvPrefix) {
            Invoke-CondaChecked `
                -FailureMessage "Conda environment install/update failed for $Engine" `
                -CondaArgs @('env', 'update', '--prefix', $EnvPrefix, '--file', $EnvFile, '--prune')
        } else {
            Invoke-CondaChecked `
                -FailureMessage "Conda environment install/update failed for $Engine" `
                -CondaArgs @('env', 'create', '--prefix', $EnvPrefix, '--file', $EnvFile)
        }

        if ($Engine -eq 'v3') {
            Write-Host ''
            Write-Host 'Installing Cellpose classic 3.x extras into the v3 environment...'
            # Shared environment.yml starts from the v4 extra. Force classic 3.x
            # before the editable reinstall so a prior Cellpose 4.x cannot linger.
            Invoke-CondaChecked `
                -FailureMessage "Failed to remove Cellpose 4.x before installing classic 3.x into $EnvPrefix" `
                -CondaArgs @(
                    'run', '--no-capture-output', '--prefix', $EnvPrefix,
                    'python', '-m', 'pip', 'uninstall', '-y', 'cellpose'
                )
            # Reinstall the editable package against the v3 extra so METADATA
            # requires cellpose 3.x (pip check stays clean).
            Invoke-CondaChecked `
                -FailureMessage "Failed to install CellQuant with cellpose-v3 into $EnvPrefix" `
                -CondaArgs @(
                    'run', '--no-capture-output', '--prefix', $EnvPrefix,
                    'python', '-m', 'pip', 'install', '-e', "${ProjectRoot}[gui,cellpose-v3,test]"
                )
            $PreviousEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $Version = & $CondaExe run --prefix $EnvPrefix python -c "import importlib.metadata as m; print(m.version('cellpose'))"
            } finally {
                $ErrorActionPreference = $PreviousEap
            }
            Write-Host "Cellpose in v3 env: $Version"
            if (-not ($Version -match '^3\.')) {
                throw "Expected Cellpose 3.x in v3 env; found $Version. Delete the v3 folder and re-run Install CellQuant.bat."
            }
        } else {
            $PreviousEap = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $Version = & $CondaExe run --prefix $EnvPrefix python -c "import importlib.metadata as m; print(m.version('cellpose'))"
            } finally {
                $ErrorActionPreference = $PreviousEap
            }
            Write-Host "Cellpose in v4 env: $Version"
            if (-not ($Version -match '^4\.')) {
                throw "Expected Cellpose 4.x in v4 env; found $Version"
            }
        }

        Write-Host "Running pip check for $Engine..."
        $PreviousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $PipCheck = & $CondaExe run --no-capture-output --prefix $EnvPrefix python -m pip check 2>&1
            $PipCheckCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $PreviousEap
        }
        if ($PipCheckCode -ne 0) {
            throw "pip check failed for $Engine environment at $EnvPrefix`n$PipCheck"
        }
        Write-Host "pip check ok for $Engine."

        # Cellpose/PyPI often pull a CPU-only torch wheel. On NVIDIA machines,
        # replace it with a driver-compatible CUDA build so Cellpose can use the GPU.
        $null = Install-CellQuantCudaTorch -CondaExe $CondaExe -EnvPrefix $EnvPrefix
    }

    Install-OneCellQuantEnv -EnvPrefix $PrefixV4 -Engine v4
    Install-OneCellQuantEnv -EnvPrefix $PrefixV3 -Engine v3

    Write-Host ''
    Write-Host 'Installed both engines:'
    Write-Host "  v4 (Cellpose-SAM):     $PrefixV4"
    Write-Host "  v3 (Cellpose classic): $PrefixV3"
    $GpuName = Get-NvidiaGpuName
    $DriverCuda = Get-NvidiaDriverCudaVersion
    if ($DriverCuda) {
        $GpuLabel = if ($GpuName) { $GpuName } else { 'NVIDIA GPU' }
        Write-Host "CUDA PyTorch was installed for $GpuLabel (driver CUDA $DriverCuda)."
    } else {
        Write-Host 'No NVIDIA GPU detected; CPU PyTorch was left in place.'
    }
    Write-Host 'Locations saved to cellquant_env.json.'
    Write-Host 'Use Open CellQuant.bat and pick v3 or v4.'
    Write-Host 'If v4 is too heavy on this machine, choose v3 at the prompt.'
} catch {
    $FailMessage = @(
        'CellQuant install failed.',
        '',
        $_.Exception.Message,
        '',
        "Log file: $LogPath",
        '',
        'Common fix: run Install CellQuant.bat again and press Enter to use the',
        'default folder for this PC (or paste a folder you can write to).',
        'Do not use a path under another user''s C:\Users\... folder.',
        'On GPU PCs, a working NVIDIA driver is required for CUDA PyTorch.'
    ) -join [Environment]::NewLine
    Show-CellQuantInstallMessage -Kind Error -Message $FailMessage
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

try { Stop-Transcript | Out-Null } catch { }
