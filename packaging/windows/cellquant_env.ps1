# Where CellQuant's Python environments live on this computer.
#
# One conda environment is created per Cellpose engine:
#   cellquant2-cellpose4   Cellpose-SAM (Cellpose 4)
#   cellquant2-cellpose3   Classic Cellpose (Cellpose 3)
#
# The record of what is installed is kept per computer, under
# %LOCALAPPDATA%\CellQuant, not in the project folder. The project folder is
# often shared through OneDrive, and paths from one person's computer do not
# work on another's.

$script:CellQuantEngineOrder = @('cellpose4', 'cellpose3')
$script:CellQuantEnvNames = @{
    cellpose4 = 'cellquant2-cellpose4'
    cellpose3 = 'cellquant2-cellpose3'
}
$script:CellQuantEngineLabels = @{
    cellpose4 = 'Cellpose-SAM (Cellpose 4)'
    cellpose3 = 'Classic Cellpose (Cellpose 3)'
}
$script:CellQuantEngineExtras = @{
    cellpose4 = 'cellpose-v4'
    cellpose3 = 'cellpose-v3'
}

function Get-CellQuantEngineLabel {
    param([Parameter(Mandatory)][string]$Engine)
    return [string]$script:CellQuantEngineLabels[$Engine]
}

function Get-CellQuantRecordPath {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    if ($env:LOCALAPPDATA) {
        $Folder = Join-Path $env:LOCALAPPDATA 'CellQuant'
        if (-not (Test-Path -LiteralPath $Folder)) {
            New-Item -ItemType Directory -Path $Folder -Force | Out-Null
        }
        return (Join-Path $Folder 'cellquant_env.json')
    }
    return (Join-Path $ProjectRoot 'cellquant_env.json')
}

function Read-CellQuantEnvRecord {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    $Path = Get-CellQuantRecordPath -ProjectRoot $ProjectRoot
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json)
    } catch {
        Write-Host "Ignoring an unreadable install record: $Path" -ForegroundColor Yellow
        return $null
    }
}

function Get-CellQuantRecordedEngines {
    <#
    .SYNOPSIS
    Engines listed in the record, as an ordered table of engine -> prefix.
    #>
    param($Record)

    $Table = [ordered]@{}
    if (-not $Record -or -not $Record.engines) {
        return $Table
    }
    foreach ($Engine in $script:CellQuantEngineOrder) {
        $Entry = $Record.engines.$Engine
        if ($Entry -and $Entry.prefix) {
            $Table[$Engine] = [string]$Entry.prefix
        }
    }
    return $Table
}

function Get-InstalledCellQuantEngines {
    <#
    .SYNOPSIS
    Recorded engines whose environment folder still exists.
    #>
    param($Record)

    $Installed = [ordered]@{}
    $Recorded = Get-CellQuantRecordedEngines -Record $Record
    foreach ($Engine in $Recorded.Keys) {
        $Prefix = $Recorded[$Engine]
        if (Test-Path -LiteralPath (Join-Path $Prefix 'python.exe') -PathType Leaf) {
            $Installed[$Engine] = $Prefix
        }
    }
    return $Installed
}

function Save-CellQuantEnvRecord {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Engines,
        [string]$DefaultEngine = '',
        [string]$RecommendedEngine = '',
        [string]$Parent = '',
        [System.Collections.IDictionary]$Hardware = $null
    )

    $Payload = [ordered]@{
        project_root = $ProjectRoot
        parent = $Parent
        default_engine = $DefaultEngine
        recommended_engine = $RecommendedEngine
        engines = $Engines
        hardware = $Hardware
        updated_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $Path = Get-CellQuantRecordPath -ProjectRoot $ProjectRoot
    $Payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Path -Encoding UTF8
    return $Path
}

function Get-DefaultCellQuantEnvParent {
    param([Parameter(Mandatory)][string]$CondaExe)

    $Base = $null
    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $InfoJson = & $CondaExe info --json 2>$null
        if ($LASTEXITCODE -eq 0 -and $InfoJson) {
            $Info = ($InfoJson | Out-String) | ConvertFrom-Json
            if ($Info.root_prefix) {
                $Base = [string]$Info.root_prefix
            }
        }
    } catch {
        $Base = $null
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    if ($Base) {
        return (Join-Path $Base 'envs')
    }
    if ($env:USERPROFILE) {
        return (Join-Path $env:USERPROFILE 'cellquant-envs')
    }
    return (Join-Path (Get-Location) 'cellquant-envs')
}

function Get-CellQuantEnginePrefix {
    param(
        [Parameter(Mandatory)][string]$Parent,
        [Parameter(Mandatory)][string]$Engine
    )
    return [System.IO.Path]::GetFullPath((Join-Path $Parent $script:CellQuantEnvNames[$Engine]))
}

function Get-CellQuantInstallPrefixProblem {
    <#
    .SYNOPSIS
    A plain-language reason this folder cannot hold an environment, or $null.
    #>
    param([Parameter(Mandatory)][string]$Prefix)

    if (-not $Prefix -or -not $Prefix.Trim()) {
        return 'No install folder was provided.'
    }
    try {
        $Resolved = [System.IO.Path]::GetFullPath($Prefix.Trim().Trim('"'))
    } catch {
        return 'The path is not a valid Windows folder path.'
    }

    if ($Resolved -match '(?i)\\OneDrive[^\\]*\\' -or ($env:OneDrive -and $Resolved.StartsWith($env:OneDrive, [StringComparison]::OrdinalIgnoreCase))) {
        return 'This folder is inside OneDrive. Syncing breaks Python environments. Choose a folder outside OneDrive.'
    }
    if ($Resolved.Length -gt 120) {
        return 'This path is too long. Windows path limits break some packages. Choose a shorter folder, such as C:\CellQuant.'
    }

    # Paths under another Windows user's profile fail after copying files between computers.
    $UsersRoot = Join-Path $env:SystemDrive 'Users'
    if ($env:USERNAME -and (Test-Path -LiteralPath $UsersRoot)) {
        $UsersPrefix = ([System.IO.Path]::GetFullPath($UsersRoot)).TrimEnd('\') + '\'
        if ($Resolved.StartsWith($UsersPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            $ProfileName = ($Resolved.Substring($UsersPrefix.Length) -split '[\\/]', 2)[0]
            $Shared = @('Public', 'Default', 'Default User', 'All Users')
            if ($ProfileName -and $ProfileName -ne $env:USERNAME -and $Shared -notcontains $ProfileName) {
                return "This path is under another Windows user ('$ProfileName'). Choose a folder in your own account."
            }
        }
    }

    $Ancestor = Split-Path -Parent $Resolved
    while ($Ancestor -and -not (Test-Path -LiteralPath $Ancestor)) {
        $Next = Split-Path -Parent $Ancestor
        if (-not $Next -or $Next -eq $Ancestor) {
            break
        }
        $Ancestor = $Next
    }
    if (-not $Ancestor -or -not (Test-Path -LiteralPath $Ancestor)) {
        return "The drive or folder does not exist: $Resolved"
    }
    $TestFile = Join-Path $Ancestor ('cellquant_write_test_{0}.tmp' -f [guid]::NewGuid().ToString('N'))
    try {
        [System.IO.File]::WriteAllText($TestFile, 'ok')
        Remove-Item -LiteralPath $TestFile -Force -ErrorAction SilentlyContinue
    } catch {
        return "This Windows account cannot write to: $Ancestor"
    }
    return $null
}
