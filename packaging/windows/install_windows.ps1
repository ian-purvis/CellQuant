# CellQuant v2 installer for Windows. Started by "Install CellQuant.bat".
#
# Builds one conda environment per Cellpose engine the user chooses:
#   Cellpose-SAM (Cellpose 4)       most accurate; best with an NVIDIA GPU
#   Classic Cellpose (Cellpose 3)   lighter; faster on computers without a GPU
# "Open CellQuant.bat" then asks which engine to start.
#
# Modeled on the CellQuant v1 installer. Differences: the user chooses which
# engines to install, GPU PyTorch is installed before Cellpose (so the CPU
# build is never downloaded and replaced), the default model is downloaded
# during install, and each environment is checked with a test analysis.

param(
    [string]$Parent = '',
    [ValidateSet('ask', 'both', 'cellpose4', 'cellpose3')]
    [string]$Engines = 'ask',
    [switch]$Dev,
    [switch]$NonInteractive,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
Set-Location -LiteralPath $ProjectRoot

if (-not $LogPath) {
    $LogPath = Join-Path $ProjectRoot 'install_last.log'
}
try {
    Start-Transcript -LiteralPath $LogPath -Force | Out-Null
} catch {
    Write-Host "Warning: could not start the install log at $LogPath ($($_.Exception.Message))" -ForegroundColor Yellow
}

. (Join-Path $ScriptDir 'resolve_conda.ps1')
. (Join-Path $ScriptDir 'cellquant_env.ps1')
. (Join-Path $ScriptDir 'hardware.ps1')

function Show-CellQuantInstallMessage {
    param(
        [Parameter(Mandatory)][string]$Message,
        [ValidateSet('Info', 'Warning', 'Error')][string]$Kind = 'Warning'
    )

    $Color = 'White'
    if ($Kind -eq 'Error') { $Color = 'Red' }
    if ($Kind -eq 'Warning') { $Color = 'Yellow' }
    Write-Host ''
    foreach ($Line in ($Message -split "`r?`n")) {
        Write-Host $Line -ForegroundColor $Color
    }
    if ($NonInteractive) {
        return
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $Icon = [System.Windows.Forms.MessageBoxIcon]::Warning
        if ($Kind -eq 'Error') { $Icon = [System.Windows.Forms.MessageBoxIcon]::Error }
        if ($Kind -eq 'Info') { $Icon = [System.Windows.Forms.MessageBoxIcon]::Information }
        [void][System.Windows.Forms.MessageBox]::Show($Message, 'CellQuant install', [System.Windows.Forms.MessageBoxButtons]::OK, $Icon)
    } catch {
        # No desktop (remote session). The console text is enough.
    }
}

function Read-CellQuantChoice {
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string[]]$Allowed,
        [Parameter(Mandatory)][string]$Default
    )

    while ($true) {
        $Answer = Read-Host $Prompt
        if (-not $Answer -or -not $Answer.Trim()) {
            return $Default
        }
        $Clean = $Answer.Trim().ToUpperInvariant()
        if ($Allowed -contains $Clean) {
            return $Clean
        }
        Write-Host "Please type one of: $($Allowed -join ', ')" -ForegroundColor Yellow
    }
}

function Write-CellQuantStep {
    param([Parameter(Mandatory)][string]$Text)
    Write-Host ''
    Write-Host $Text -ForegroundColor Cyan
}

function Invoke-CondaChecked {
    param(
        [Parameter(Mandatory)][string]$FailureMessage,
        # Pass conda arguments as one array. Bare flags such as -p would otherwise
        # bind to PowerShell's own common parameters.
        [Parameter(Mandatory)][string[]]$CondaArgs
    )

    # conda writes warnings to stderr. Keep them from stopping the script.
    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Out-Host shows the output live and keeps it out of the function's return value.
        & $script:CondaExe @CondaArgs | Out-Host
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    if ($Code -ne 0) {
        throw "$FailureMessage (exit code $Code)"
    }
}

function Invoke-EnvPython {
    param(
        [Parameter(Mandatory)][string]$Prefix,
        [Parameter(Mandatory)][string]$FailureMessage,
        [Parameter(Mandatory)][string[]]$PythonArgs
    )
    Invoke-CondaChecked -FailureMessage $FailureMessage -CondaArgs (@('run', '--no-capture-output', '--prefix', $Prefix, 'python') + $PythonArgs)
}

function Get-EnvPythonOutput {
    param(
        [Parameter(Mandatory)][string]$Prefix,
        [Parameter(Mandatory)][string[]]$PythonArgs
    )

    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $Output = & $script:CondaExe run --prefix $Prefix python @PythonArgs 2>&1
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    return @{ code = $Code; lines = @($Output | ForEach-Object { "$_" }) }
}

function Remove-CellQuantEnvPrefix {
    param([Parameter(Mandatory)][string]$Prefix)

    if (-not (Test-Path -LiteralPath $Prefix)) {
        return
    }
    Write-Host "Deleting environment: $Prefix"
    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $script:CondaExe env remove --prefix $Prefix --yes | Out-Host
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    # conda on Windows often leaves the folder behind.
    if (Test-Path -LiteralPath $Prefix) {
        Remove-Item -LiteralPath $Prefix -Recurse -Force -ErrorAction Stop
    }
    if (Test-Path -LiteralPath $Prefix) {
        throw "Could not delete $Prefix. Close CellQuant and any Python windows using it, then try again."
    }
}

function Install-CellQuantEngine {
    param(
        [Parameter(Mandatory)][string]$Engine,
        [Parameter(Mandatory)][string]$Prefix,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Hardware
    )

    $Label = Get-CellQuantEngineLabel -Engine $Engine
    $Extra = $script:CellQuantEngineExtras[$Engine]
    $ExpectedMajor = '4'
    if ($Engine -eq 'cellpose3') { $ExpectedMajor = '3' }

    Write-CellQuantStep "Installing $Label"
    Write-Host "  Folder: $Prefix"
    Write-Host '  This can take 10 to 30 minutes, depending on the connection.'

    # 1. Python
    if (-not (Test-Path -LiteralPath (Join-Path $Prefix 'python.exe') -PathType Leaf)) {
        if (Test-Path -LiteralPath $Prefix) {
            # A folder without python.exe is left over from a failed install.
            Remove-CellQuantEnvPrefix -Prefix $Prefix
        }
        Write-CellQuantStep '  Creating the Python 3.11 environment...'
        # conda-forge only: the Anaconda "defaults" channel can stop the install
        # to ask for terms-of-service acceptance.
        Invoke-CondaChecked -FailureMessage "Could not create the Python environment for $Label" -CondaArgs @(
            'create', '--yes', '--prefix', $Prefix, '--override-channels', '--channel', 'conda-forge', 'python=3.11', 'pip'
        )
    }
    Invoke-EnvPython -Prefix $Prefix -FailureMessage 'Could not update pip' -PythonArgs @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip'
    )

    # 2. GPU PyTorch first, so pip keeps it when Cellpose is installed.
    $TorchPackages = @('torch')
    if ($Engine -eq 'cellpose4') { $TorchPackages = @('torch', 'torchvision') }
    $CudaTag = $Hardware.cuda_tag
    if ($CudaTag) {
        $IndexUrl = Get-CellQuantTorchCudaIndexUrl -CudaTag $CudaTag
        Write-CellQuantStep "  Installing PyTorch with GPU support ($CudaTag) for $($Hardware.gpu_name)..."
        $Probe = Test-CellQuantTorchCudaUsable -CondaExe $script:CondaExe -EnvPrefix $Prefix
        if ($Probe.ok) {
            Write-Host "  GPU PyTorch already works: $($Probe.version) on $($Probe.device_name)"
        } else {
            Invoke-EnvPython -Prefix $Prefix -FailureMessage "Could not install GPU PyTorch ($CudaTag)" -PythonArgs (
                @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade') + $TorchPackages + @('--index-url', $IndexUrl)
            )
        }
    } else {
        Write-Host '  No usable NVIDIA GPU: the standard (CPU) PyTorch will be installed.'
    }

    # 3. CellQuant, napari and this engine's Cellpose.
    Write-CellQuantStep "  Installing CellQuant, napari and $Label..."
    $Requirement = "${ProjectRoot}[gui,$Extra]"
    if ($Dev) {
        Invoke-EnvPython -Prefix $Prefix -FailureMessage 'Could not install CellQuant' -PythonArgs @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', '--editable', $Requirement
        )
    } else {
        Invoke-EnvPython -Prefix $Prefix -FailureMessage 'Could not install CellQuant' -PythonArgs @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', $Requirement
        )
        # Make sure the copy in the environment matches the project folder, even
        # when the version number did not change.
        Invoke-EnvPython -Prefix $Prefix -FailureMessage 'Could not refresh CellQuant' -PythonArgs @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '--force-reinstall', $ProjectRoot
        )
    }

    $Version = Get-EnvPythonOutput -Prefix $Prefix -PythonArgs @('-c', "import importlib.metadata as m; print(m.version('cellpose'))")
    $CellposeVersion = @($Version.lines | Where-Object { $_ -match '^\d+\.' }) | Select-Object -Last 1
    Write-Host "  Cellpose in this environment: $CellposeVersion"
    if (-not $CellposeVersion -or -not $CellposeVersion.StartsWith("$ExpectedMajor.")) {
        throw "Expected Cellpose $ExpectedMajor in $Prefix but found '$CellposeVersion'. Run the installer again and choose [R] to reinstall."
    }

    # 4. Confirm the GPU still works; repair if pip replaced PyTorch.
    $GpuName = $null
    if ($CudaTag) {
        $Probe = Test-CellQuantTorchCudaUsable -CondaExe $script:CondaExe -EnvPrefix $Prefix
        if (-not $Probe.ok) {
            Write-Host '  PyTorch cannot use the GPU yet. Reinstalling the GPU build...' -ForegroundColor Yellow
            Invoke-EnvPython -Prefix $Prefix -FailureMessage "Could not reinstall GPU PyTorch ($CudaTag)" -PythonArgs (
                # --no-deps: swap only the PyTorch packages. Reinstalling their dependencies from
                # the PyTorch index can replace numpy with a version other packages do not accept.
                @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', '--force-reinstall', '--no-deps') + $TorchPackages + @('--index-url', (Get-CellQuantTorchCudaIndexUrl -CudaTag $CudaTag))
            )
            $Probe = Test-CellQuantTorchCudaUsable -CondaExe $script:CondaExe -EnvPrefix $Prefix
        }
        if (-not $Probe.ok) {
            throw "GPU PyTorch ($CudaTag) is installed but cannot use $($Hardware.gpu_name). Probe: $($Probe.raw). Update the NVIDIA driver and run the installer again."
        }
        $GpuName = $Probe.device_name
        Write-Host "  GPU ready: PyTorch $($Probe.version) on $($Probe.device_name)"
    }

    Write-CellQuantStep '  Checking installed packages for conflicts...'
    $Check = Get-EnvPythonOutput -Prefix $Prefix -PythonArgs @('-m', 'pip', 'check')
    if ($Check.code -ne 0) {
        throw "Package conflict in $Prefix`n$($Check.lines -join [Environment]::NewLine)"
    }
    Write-Host '  No conflicts.'

    # 5. Model weights now, not during the first analysis. A failure here is only a warning.
    Write-CellQuantStep '  Downloading the default Cellpose model (Cellpose-SAM is over 1 GB)...'
    $Download = Get-EnvPythonOutput -Prefix $Prefix -PythonArgs @((Join-Path $ScriptDir 'download_models.py'))
    $Download.lines | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
    $ModelWarning = $null
    if ($Download.code -ne 0) {
        $ModelWarning = 'The Cellpose model could not be downloaded now. CellQuant will download it the first time Cellpose runs, which needs an internet connection.'
        Write-Host "  $ModelWarning" -ForegroundColor Yellow
    }

    # 6. A small test analysis, then the fastest CPU thread count for Cellpose on this computer.
    Write-CellQuantStep '  Running a test analysis and measuring CPU speed...'
    $Smoke = Get-EnvPythonOutput -Prefix $Prefix -PythonArgs @((Join-Path $ScriptDir 'smoke_test.py'), $Engine, '--benchmark-threads')
    $SmokeLine = @($Smoke.lines | Where-Object { $_ -like 'CELLQUANT_SMOKE *' }) | Select-Object -Last 1
    $Report = $null
    if ($SmokeLine) {
        $Report = $SmokeLine.Substring('CELLQUANT_SMOKE '.Length) | ConvertFrom-Json
    }
    if ($Smoke.code -ne 0 -or -not $Report -or -not $Report.ok) {
        $Detail = if ($Report -and $Report.error) { $Report.error } else { ($Smoke.lines | Select-Object -Last 15) -join [Environment]::NewLine }
        throw "The test analysis failed in $Prefix`n$Detail"
    }
    Write-Host "  Test passed: $($Report.test_analysis). napari $($Report.napari), Qt $($Report.qt), default model $($Report.default_model)."
    if ($Report.cpu_threads -and $Report.cpu_threads.threads -and -not $Report.cpu_threads.error) {
        Write-Host "  Cellpose will use $($Report.cpu_threads.threads) CPU threads (fastest here)."
    } elseif ($Report.cpu_threads -and $Report.cpu_threads.error) {
        Write-Host "  CPU speed not measured; Cellpose uses PyTorch's default threads. $($Report.cpu_threads.error)" -ForegroundColor Yellow
    }

    return [ordered]@{
        prefix = $Prefix
        cellpose_version = [string]$CellposeVersion
        default_model = [string]$Report.default_model
        gpu = $GpuName
        cuda_tag = $CudaTag
        model_warning = $ModelWarning
        installed_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
}

# ---------------------------------------------------------------------------

Write-Host ''
Write-Host 'CellQuant installer'
Write-Host '-------------------'

try {
    $script:CondaExe = Resolve-CondaExecutable
} catch {
    Show-CellQuantInstallMessage -Kind Error -Message ((@(
        'Conda was not found on this computer.',
        '',
        'Install Miniforge (recommended) or Miniconda, then run Install CellQuant.bat again.',
        'Miniforge: https://conda-forge.org/download/',
        '',
        "Details: $($_.Exception.Message)"
    )) -join [Environment]::NewLine)
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
Write-Host "Using conda: $script:CondaExe"

Write-CellQuantStep 'Checking this computer...'
$Hardware = Get-CellQuantHardware
$Recommendation = Get-CellQuantEngineRecommendation -Hardware $Hardware
if ($Hardware.gpu_name) {
    $GpuText = "$($Hardware.gpu_name)"
    if ($null -ne $Hardware.gpu_memory_gb) { $GpuText += ", $($Hardware.gpu_memory_gb) GB" }
    Write-Host "  NVIDIA GPU: $GpuText"
    if ($Hardware.cuda_tag) {
        Write-Host "  GPU support: yes (PyTorch $($Hardware.cuda_tag), driver CUDA $($Hardware.driver_cuda))"
    } else {
        Write-Host "  GPU support: no. $($Hardware.cuda_problem)" -ForegroundColor Yellow
    }
} else {
    Write-Host '  NVIDIA GPU: none found. Cellpose will run on the CPU.'
}
if ($Hardware.ram_gb) {
    Write-Host "  Memory: $($Hardware.ram_gb) GB"
    if ($Hardware.ram_gb -lt 16) {
        Write-Host '  Less than 16 GB of memory: large Z-stacks may not fit. Use the 2D modes for them.' -ForegroundColor Yellow
    }
}
$RecommendedLabel = Get-CellQuantEngineLabel -Engine $Recommendation.engine
Write-Host "  Recommended engine: $RecommendedLabel, because $($Recommendation.reason)."

$Record = Read-CellQuantEnvRecord -ProjectRoot $ProjectRoot
$Existing = Get-InstalledCellQuantEngines -Record $Record
$Recorded = Get-CellQuantRecordedEngines -Record $Record
$Mode = 'install'
if ($Existing.Count -gt 0) {
    $Mode = 'update'
    if (-not $NonInteractive) {
        Write-Host ''
        Write-Host 'CellQuant is already installed on this computer:'
        foreach ($Engine in $Existing.Keys) {
            Write-Host "  $(Get-CellQuantEngineLabel -Engine $Engine): $($Existing[$Engine])"
        }
        Write-Host ''
        Write-Host '  [O] Open CellQuant (change nothing)'
        Write-Host '  [U] Update: install the current CellQuant, and add an engine if you choose one'
        Write-Host '  [R] Reinstall: delete the CellQuant environments and install again'
        $Choice = Read-CellQuantChoice -Prompt 'Choice [O/U/R] (Enter = O)' -Allowed @('O', 'U', 'R') -Default 'O'
        if ($Choice -eq 'O') {
            try { Stop-Transcript | Out-Null } catch { }
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ScriptDir 'launch_cellquant.ps1') -Engine ask
            exit $LASTEXITCODE
        }
        if ($Choice -eq 'R') {
            $Mode = 'reinstall'
        }
    }
}

# Where the environments go.
$DefaultParent = Get-DefaultCellQuantEnvParent -CondaExe $script:CondaExe
$SavedParent = $null
if ($Record -and $Record.parent) { $SavedParent = [string]$Record.parent }
if (-not $Parent) {
    if ($Mode -ne 'install' -and $SavedParent) {
        $Parent = $SavedParent
    } elseif ($NonInteractive) {
        $Parent = $DefaultParent
    } else {
        Write-Host ''
        Write-Host 'Where should the CellQuant environments go?'
        Write-Host "  Default: $DefaultParent"
        Write-Host '  Use a folder on this computer, outside OneDrive.'
        $Answer = Read-Host 'Folder (Enter = default, or paste a full path)'
        if ($Answer -and $Answer.Trim()) { $Parent = $Answer.Trim().Trim('"') } else { $Parent = $DefaultParent }
    }
}
while ($true) {
    $Problem = Get-CellQuantInstallPrefixProblem -Prefix (Get-CellQuantEnginePrefix -Parent $Parent -Engine 'cellpose4')
    if (-not $Problem) {
        break
    }
    $Guidance = (@(
        'CellQuant cannot install into this folder:',
        "  $Parent",
        '',
        $Problem,
        '',
        "Press Enter at the next prompt to use the default: $DefaultParent"
    )) -join [Environment]::NewLine
    if ($NonInteractive) {
        if ($Parent -ne $DefaultParent) {
            Write-Host $Guidance -ForegroundColor Yellow
            $Parent = $DefaultParent
            continue
        }
        Show-CellQuantInstallMessage -Kind Error -Message $Guidance
        try { Stop-Transcript | Out-Null } catch { }
        exit 1
    }
    Show-CellQuantInstallMessage -Kind Warning -Message $Guidance
    $Answer = Read-Host 'Folder (Enter = default, or paste a full path)'
    if ($Answer -and $Answer.Trim()) { $Parent = $Answer.Trim().Trim('"') } else { $Parent = $DefaultParent }
}
$Parent = [System.IO.Path]::GetFullPath($Parent)

# Which engines.
$FreeGb = Get-CellQuantFreeDiskGb -Path $Parent
$EachGb = Get-CellQuantEngineSizeGb -Hardware $Hardware
$DefaultChoice = 'B'
if ($Mode -eq 'update' -and $Existing.Count -eq 1) {
    $DefaultChoice = 'S'
    if ($Existing.Contains('cellpose3')) { $DefaultChoice = 'C' }
} elseif ($null -ne $FreeGb -and $FreeGb -lt (2 * $EachGb + 5)) {
    $DefaultChoice = 'S'
    if ($Recommendation.engine -eq 'cellpose3') { $DefaultChoice = 'C' }
}
if ($Engines -eq 'ask') {
    if ($NonInteractive) {
        $Engines = 'both'
    } else {
        Write-Host ''
        Write-Host 'Which Cellpose engines should be installed?'
        Write-Host '  [B] Both. You choose one each time you open CellQuant.'
        Write-Host '  [S] Cellpose-SAM only (Cellpose 4). Most accurate. Best with an NVIDIA GPU.'
        Write-Host '  [C] Classic Cellpose only (Cellpose 3). Lighter. Faster without a GPU.'
        Write-Host "  Recommended for this computer: $RecommendedLabel."
        $SpaceText = "  Each engine needs roughly $EachGb GB."
        if ($null -ne $FreeGb) { $SpaceText += " Free space on that drive: $FreeGb GB." }
        Write-Host $SpaceText
        $Choice = Read-CellQuantChoice -Prompt "Choice [B/S/C] (Enter = $DefaultChoice)" -Allowed @('B', 'S', 'C') -Default $DefaultChoice
        $Engines = 'both'
        if ($Choice -eq 'S') { $Engines = 'cellpose4' }
        if ($Choice -eq 'C') { $Engines = 'cellpose3' }
    }
}
$Selected = @('cellpose4', 'cellpose3')
if ($Engines -ne 'both') { $Selected = @($Engines) }
if ($null -ne $FreeGb -and $FreeGb -lt ($Selected.Count * $EachGb)) {
    Write-Host "Warning: only $FreeGb GB is free on that drive. The install may run out of space." -ForegroundColor Yellow
}

# Install.
$EngineTable = [ordered]@{}
if ($Record -and $Record.engines -and $Mode -ne 'reinstall') {
    foreach ($Engine in $script:CellQuantEngineOrder) {
        if ($Existing.Contains($Engine)) {
            $Entry = [ordered]@{}
            foreach ($Property in $Record.engines.$Engine.PSObject.Properties) {
                $Entry[$Property.Name] = $Property.Value
            }
            $EngineTable[$Engine] = $Entry
        }
    }
}

$Failures = @()
$Warnings = @()
try {
    if ($Mode -eq 'reinstall') {
        Write-CellQuantStep 'Deleting the existing CellQuant environments...'
        foreach ($Engine in $Recorded.Keys) {
            Remove-CellQuantEnvPrefix -Prefix $Recorded[$Engine]
        }
    }
    foreach ($Engine in $Selected) {
        $Prefix = Get-CellQuantEnginePrefix -Parent $Parent -Engine $Engine
        if ($Mode -eq 'update' -and $Existing.Contains($Engine)) {
            $Prefix = $Existing[$Engine]
        }
        try {
            $Info = Install-CellQuantEngine -Engine $Engine -Prefix $Prefix -Hardware $Hardware
            $EngineTable[$Engine] = $Info
            if ($Info.model_warning) { $Warnings += $Info.model_warning }
        } catch {
            $Failures += "$(Get-CellQuantEngineLabel -Engine $Engine): $($_.Exception.Message)"
            Write-Host ''
            Write-Host "$(Get-CellQuantEngineLabel -Engine $Engine) was not installed:" -ForegroundColor Red
            Write-Host $_.Exception.Message -ForegroundColor Red
        }
        # Save after each engine so a later failure does not lose a working one.
        $DefaultEngine = $Recommendation.engine
        if (-not $EngineTable.Contains($DefaultEngine)) {
            $DefaultEngine = @($EngineTable.Keys)[0]
        }
        if ($EngineTable.Count -gt 0) {
            [void](Save-CellQuantEnvRecord -ProjectRoot $ProjectRoot -Engines $EngineTable -DefaultEngine $DefaultEngine -RecommendedEngine $Recommendation.engine -Parent $Parent -Hardware $Hardware)
        }
    }
} catch {
    $Failures += $_.Exception.Message
}

Write-Host ''
if ($EngineTable.Count -gt 0) {
    Write-Host 'Installed:' -ForegroundColor Green
    foreach ($Engine in $EngineTable.Keys) {
        $Where = 'CPU'
        if ($EngineTable[$Engine].gpu) { $Where = "GPU: $($EngineTable[$Engine].gpu)" }
        Write-Host "  $(Get-CellQuantEngineLabel -Engine $Engine), Cellpose $($EngineTable[$Engine].cellpose_version), $Where"
    }
    Write-Host "Install record: $(Get-CellQuantRecordPath -ProjectRoot $ProjectRoot)"
}
foreach ($Text in $Warnings) {
    Write-Host "Note: $Text" -ForegroundColor Yellow
}

if ($Failures.Count -gt 0) {
    $Lines = @('CellQuant install did not finish.', '') + $Failures + @(
        '',
        "Full log: $LogPath",
        '',
        'Common fixes: check the internet connection; run Install CellQuant.bat again',
        'and press Enter for the default folder; on GPU computers, update the NVIDIA driver.'
    )
    if ($EngineTable.Count -gt 0) {
        $Lines += @('', 'The engines listed as installed above can already be used.')
    }
    Show-CellQuantInstallMessage -Kind Error -Message ($Lines -join [Environment]::NewLine)
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

Write-Host 'Double-click Open CellQuant.bat to start.' -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch { }
exit 0
