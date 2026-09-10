$ErrorActionPreference = 'Stop'
$OriginalUserProfile = $env:USERPROFILE
$OriginalCondaExe = $env:CONDA_EXE
$OriginalPath = $env:PATH
$FixtureRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("cellquant-conda-test-" + [guid]::NewGuid())
$FixtureConda = Join-Path $FixtureRoot 'miniconda3\Scripts\conda.exe'

try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $FixtureConda) -Force | Out-Null
    New-Item -ItemType File -Path $FixtureConda -Force | Out-Null
    $env:USERPROFILE = $FixtureRoot
    $env:CONDA_EXE = $null
    $env:PATH = ''

    function conda { throw 'The conda function must not be invoked.' }

    . (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\resolve_conda.ps1')
    $Resolved = Resolve-CondaExecutable

    if ($Resolved -ne $FixtureConda) {
        throw "Expected '$FixtureConda', but resolver returned '$Resolved'."
    }

    Write-Host 'PASS: ignored the conda function and selected the Miniconda executable fallback.'
} finally {
    Remove-Item Function:\conda -ErrorAction SilentlyContinue
    $env:USERPROFILE = $OriginalUserProfile
    $env:CONDA_EXE = $OriginalCondaExe
    $env:PATH = $OriginalPath
    if (Test-Path -LiteralPath $FixtureRoot) {
        Remove-Item -LiteralPath $FixtureRoot -Recurse -Force
    }
}
