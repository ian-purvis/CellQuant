# Start CellQuant with one of the installed Cellpose engines.
# Started by "Open CellQuant.bat". Asks which engine to use when both are installed.

param(
    [ValidateSet('ask', 'default', 'cellpose4', 'cellpose3')]
    [string]$Engine = 'ask',
    [string]$Experiment = ''
)

$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
Set-Location -LiteralPath $ProjectRoot

. (Join-Path $ScriptDir 'resolve_conda.ps1')
. (Join-Path $ScriptDir 'cellquant_env.ps1')

try {
    $CondaExe = Resolve-CondaExecutable
} catch {
    Write-Host ''
    Write-Host 'Conda was not found.' -ForegroundColor Red
    Write-Host 'Install Miniforge or Miniconda, then run Install CellQuant.bat.'
    Write-Host "Details: $($_.Exception.Message)"
    exit 1
}

$Record = Read-CellQuantEnvRecord -ProjectRoot $ProjectRoot
$Installed = Get-InstalledCellQuantEngines -Record $Record
if ($Installed.Count -eq 0) {
    Write-Host ''
    Write-Host 'CellQuant is not installed on this computer yet.' -ForegroundColor Red
    Write-Host 'Double-click Install CellQuant.bat first.'
    exit 1
}

$DefaultEngine = @($Installed.Keys)[0]
if ($Record.default_engine -and $Installed.Contains([string]$Record.default_engine)) {
    $DefaultEngine = [string]$Record.default_engine
}

if ($Engine -eq 'ask' -and $Installed.Count -gt 1) {
    $Keys = @($Installed.Keys)
    Write-Host ''
    Write-Host 'Which Cellpose engine?'
    for ($Index = 0; $Index -lt $Keys.Count; $Index++) {
        $Note = ''
        if ($Keys[$Index] -eq 'cellpose4') { $Note = ' - most accurate; best with an NVIDIA GPU' }
        if ($Keys[$Index] -eq 'cellpose3') { $Note = ' - lighter; faster without a GPU' }
        if ($Keys[$Index] -eq $Record.recommended_engine) { $Note += ' (recommended for this computer)' }
        Write-Host "  $($Index + 1)) $(Get-CellQuantEngineLabel -Engine $Keys[$Index])$Note"
    }
    $DefaultNumber = [array]::IndexOf($Keys, $DefaultEngine) + 1
    $Answer = Read-Host "Choose [1-$($Keys.Count)] (Enter = $DefaultNumber)"
    $Engine = $DefaultEngine
    $Number = 0
    if ($Answer -and [int]::TryParse($Answer.Trim(), [ref]$Number) -and $Number -ge 1 -and $Number -le $Keys.Count) {
        $Engine = $Keys[$Number - 1]
    }
} elseif ($Engine -eq 'ask' -or $Engine -eq 'default') {
    $Engine = $DefaultEngine
}

if (-not $Installed.Contains($Engine)) {
    Write-Host ''
    Write-Host "$(Get-CellQuantEngineLabel -Engine $Engine) is not installed on this computer." -ForegroundColor Red
    Write-Host 'Run Install CellQuant.bat, choose [U] Update, and select it.'
    exit 1
}

$Prefix = $Installed[$Engine]
Write-Host ''
Write-Host "Starting CellQuant with $(Get-CellQuantEngineLabel -Engine $Engine)..."
Write-Host "  Environment: $Prefix"
Write-Host '  The napari window can take a minute to appear. Keep this window open while you work.'

$Arguments = @('run', '--no-capture-output', '--prefix', $Prefix, 'python', '-m', 'cellquant')
if ($Experiment) {
    $Arguments += $Experiment
}
# Start through "python -m" rather than an .exe shim: conda run can report a
# false error after a GUI window opens from a shim (seen in CellQuant v1).
& $CondaExe @Arguments
exit $LASTEXITCODE
