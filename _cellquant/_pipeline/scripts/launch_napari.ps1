param(
    [string]$Prefix = '',
    [ValidateSet('v4', 'v3', 'default', 'ask')]
    [string]$Engine = 'ask'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

. (Join-Path $PSScriptRoot 'resolve_conda.ps1')
. (Join-Path $PSScriptRoot 'resolve_env_location.ps1')

try {
    $CondaExe = Resolve-CondaExecutable
} catch {
    Write-Host ''
    Write-Host 'Conda was not found.' -ForegroundColor Red
    Write-Host 'Install Miniconda or Miniforge, then run Install CellQuant.bat.'
    Write-Host "Details: $($_.Exception.Message)"
    exit 1
}

function Test-EnvReady {
    param([string]$Candidate)
    return ($Candidate -and (Test-Path -LiteralPath $Candidate))
}

$PrefixV4 = if ($Prefix -and $Engine -eq 'v4') {
    [System.IO.Path]::GetFullPath($Prefix.Trim().Trim('"'))
} else {
    Resolve-CellQuantEnvPrefix -ProjectRoot $ProjectRoot -Prefix '' -CondaExe $CondaExe -Engine v4
}
$PrefixV3 = if ($Prefix -and $Engine -eq 'v3') {
    [System.IO.Path]::GetFullPath($Prefix.Trim().Trim('"'))
} else {
    Resolve-CellQuantEnvPrefix -ProjectRoot $ProjectRoot -Prefix '' -CondaExe $CondaExe -Engine v3
}

$HasV4 = Test-EnvReady $PrefixV4
$HasV3 = Test-EnvReady $PrefixV3

if ($Engine -eq 'ask') {
    if ($HasV4 -and $HasV3) {
        Write-Host ''
        Write-Host 'CellQuant engine'
        Write-Host '---------------'
        Write-Host '  1) Cellpose-SAM v4  (heavier; current scientific default)'
        Write-Host '  2) Cellpose classic v3 (lighter — prefer if v4 is too slow or runs out of memory)'
        Write-Host ''
        $Choice = Read-Host 'Choose engine [1=v4, 2=v3, Enter=v4]'
        if ($Choice -eq '2') {
            $Engine = 'v3'
        } else {
            $Engine = 'v4'
        }
    } elseif ($HasV3 -and -not $HasV4) {
        $Engine = 'v3'
    } else {
        $Engine = 'v4'
    }
}

if ($Engine -eq 'default') {
    $Engine = 'v4'
    $Record = Read-CellQuantEnvRecord -ProjectRoot $ProjectRoot
    if ($Record -and $Record.default_engine -eq 'v3') {
        $Engine = 'v3'
    }
}

if ($Prefix) {
    $ResolvedPrefix = [System.IO.Path]::GetFullPath($Prefix.Trim().Trim('"'))
} else {
    $ResolvedPrefix = if ($Engine -eq 'v3') { $PrefixV3 } else { $PrefixV4 }
}

if (-not $ResolvedPrefix) {
    Write-Host ''
    Write-Host 'No CellQuant install location is recorded yet.' -ForegroundColor Red
    Write-Host 'Run Install CellQuant.bat once and choose (or accept) an install folder.'
    exit 1
}

if (-not (Test-Path -LiteralPath $ResolvedPrefix)) {
    Write-Host ''
    Write-Host "Install folder not found: $ResolvedPrefix" -ForegroundColor Red
    if ($Engine -eq 'v3') {
        Write-Host 'Run Install CellQuant.bat again (it now installs both v3 and v4).'
    } else {
        Write-Host 'Run Install CellQuant.bat again, or pick a different folder.'
    }
    exit 1
}

Write-Host "Launching napari ($Engine) from:"
Write-Host "  $ResolvedPrefix"
# Launch napari through the environment's Python module.  Calling the generated
# ``napari.exe`` console shim through ``conda run`` can return conda error 127
# on Windows after the GUI creates its Qt window, which then surfaces as the
# misleading ``. was unexpected at this time`` message in the .bat wrapper.
& $CondaExe run --no-capture-output -p $ResolvedPrefix python -m napari
exit $LASTEXITCODE
