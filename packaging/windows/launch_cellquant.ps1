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
    Write-Host 'Double-click Install CellQuant.bat first. It installs Miniforge if this computer has no conda.'
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
# CellQuant writes "software" here after a graphics driver error (cellquant/gui/graphics.py).
$env:CELLQUANT_GRAPHICS_FILE = Get-CellQuantGraphicsPath -ProjectRoot $ProjectRoot
# Exit codes that mean the viewer could not draw: CellQuant's own code for an OpenGL
# error at startup, and a Windows access violation (0xC0000005).
$GraphicsExitCodes = @(75, -1073741819, 3221225477)
$Software = Test-CellQuantSoftwareGraphics -ProjectRoot $ProjectRoot
while ($true) {
    if ($Software) {
        # Draw the viewer with the software OpenGL (Mesa) that ships with Qt. vispy must load
        # the same library as Qt, or it draws with the system driver anyway. Cellpose keeps the GPU.
        $env:QT_OPENGL = 'software'
        $env:CELLQUANT_SOFTWARE_GRAPHICS = '1'
        $SoftwareGl = Join-Path $Prefix 'Lib\site-packages\PyQt6\Qt6\bin\opengl32sw.dll'
        if (Test-Path -LiteralPath $SoftwareGl -PathType Leaf) {
            $env:VISPY_GL_LIB = $SoftwareGl
        }
        Write-Host '  Software graphics: on, after a graphics error on this computer.'
        Write-Host '  To switch back after updating the graphics driver, run Install CellQuant.bat and choose [U] Update.'
    }
    # Start through "python -m" rather than an .exe shim: conda run can report a
    # false error after a GUI window opens from a shim (seen in CellQuant v1).
    & $CondaExe @Arguments
    $Code = $LASTEXITCODE
    if ($Software -or -not ($GraphicsExitCodes -contains $Code)) {
        exit $Code
    }
    Set-CellQuantSoftwareGraphics -ProjectRoot $ProjectRoot
    $Software = $true
    Write-Host ''
    Write-Host 'The viewer hit a graphics error. Reopening CellQuant with software graphics...' -ForegroundColor Yellow
}
