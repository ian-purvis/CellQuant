# Shared helpers for CellQuant Windows env location (prefix-based conda envs).

$script:CellQuantEnvMarkerName = 'cellquant_env.json'

function Get-CellQuantEnvMarkerPath {
    param([Parameter(Mandatory)][string]$ProjectRoot)
    Join-Path $ProjectRoot $script:CellQuantEnvMarkerName
}

function Get-DefaultCellQuantEnvPrefix {
    param(
        [Parameter(Mandatory)][string]$CondaExe,
        [ValidateSet('v4', 'v3')]
        [string]$Engine = 'v4'
    )

    $Base = $null
    try {
        $InfoJson = & $CondaExe info --json 2>$null
        if ($LASTEXITCODE -eq 0 -and $InfoJson) {
            $Info = $InfoJson | ConvertFrom-Json
            if ($Info.root_prefix) {
                $Base = [string]$Info.root_prefix
            } elseif ($Info.conda_prefix) {
                $Base = [string]$Info.conda_prefix
            }
        }
    } catch {
        $Base = $null
    }

    $EnvName = if ($Engine -eq 'v3') { 'cellquant-napari-v3' } else { 'cellquant-napari' }
    if ($Base) {
        return (Join-Path $Base "envs\$EnvName")
    }
    if ($env:USERPROFILE) {
        return (Join-Path $env:USERPROFILE $EnvName)
    }
    return (Join-Path (Get-Location) $EnvName)
}

function Get-SiblingV3Prefix {
    param([Parameter(Mandatory)][string]$PrefixV4)

    $Resolved = [System.IO.Path]::GetFullPath($PrefixV4)
    $Leaf = Split-Path -Leaf $Resolved
    $Parent = Split-Path -Parent $Resolved
    if ($Leaf -match '(?i)-v3$') {
        return $Resolved
    }
    if ($Leaf -match '(?i)-v4$') {
        return (Join-Path $Parent ($Leaf -replace '(?i)-v4$', '-v3'))
    }
    if ($Leaf -match '(?i)cellquant-napari$') {
        return (Join-Path $Parent ($Leaf + '-v3'))
    }
    return (Join-Path $Parent ($Leaf + '-v3'))
}

function Read-CellQuantEnvRecord {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    $MarkerPath = Get-CellQuantEnvMarkerPath -ProjectRoot $ProjectRoot
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
        return $null
    }

    $Raw = Get-Content -LiteralPath $MarkerPath -Raw -ErrorAction Stop
    return ($Raw | ConvertFrom-Json)
}

function Read-CellQuantEnvPrefix {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [ValidateSet('v4', 'v3', 'default')]
        [string]$Engine = 'default'
    )

    $Data = Read-CellQuantEnvRecord -ProjectRoot $ProjectRoot
    if (-not $Data) {
        return $null
    }

    if ($Engine -eq 'v3') {
        if ($Data.prefix_v3) { return [string]$Data.prefix_v3 }
        if ($Data.prefix) { return Get-SiblingV3Prefix -PrefixV4 ([string]$Data.prefix) }
        return $null
    }

    if ($Engine -eq 'v4') {
        if ($Data.prefix_v4) { return [string]$Data.prefix_v4 }
        if ($Data.prefix) { return [string]$Data.prefix }
        return $null
    }

    # default: prefer explicit default_engine, else prefix / prefix_v4
    $DefaultEngine = 'v4'
    if ($Data.default_engine -eq 'v3' -or $Data.default_engine -eq 'v4') {
        $DefaultEngine = [string]$Data.default_engine
    }
    if ($DefaultEngine -eq 'v3') {
        if ($Data.prefix_v3) { return [string]$Data.prefix_v3 }
    }
    if ($Data.prefix_v4) { return [string]$Data.prefix_v4 }
    if ($Data.prefix) { return [string]$Data.prefix }
    if ($Data.prefix_v3) { return [string]$Data.prefix_v3 }
    return $null
}

function Save-CellQuantEnvPrefixes {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$PrefixV4,
        [Parameter(Mandatory)][string]$PrefixV3,
        [ValidateSet('v4', 'v3')]
        [string]$DefaultEngine = 'v4'
    )

    $ResolvedV4 = [System.IO.Path]::GetFullPath($PrefixV4)
    $ResolvedV3 = [System.IO.Path]::GetFullPath($PrefixV3)
    $MarkerPath = Get-CellQuantEnvMarkerPath -ProjectRoot $ProjectRoot
    $Payload = @{
        prefix = $ResolvedV4
        prefix_v4 = $ResolvedV4
        prefix_v3 = $ResolvedV3
        default_engine = $DefaultEngine
        updated_utc = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json
    Set-Content -LiteralPath $MarkerPath -Value $Payload -Encoding UTF8
    return @{
        prefix_v4 = $ResolvedV4
        prefix_v3 = $ResolvedV3
    }
}

function Save-CellQuantEnvPrefix {
    # Backward-compatible wrapper: treat single prefix as v4 and derive v3 sibling.
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$Prefix
    )

    $ResolvedV4 = [System.IO.Path]::GetFullPath($Prefix)
    $ResolvedV3 = Get-SiblingV3Prefix -PrefixV4 $ResolvedV4
    $Saved = Save-CellQuantEnvPrefixes `
        -ProjectRoot $ProjectRoot `
        -PrefixV4 $ResolvedV4 `
        -PrefixV3 $ResolvedV3 `
        -DefaultEngine v4
    return $Saved.prefix_v4
}

function Resolve-CellQuantEnvPrefix {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [string]$Prefix,
        [Parameter(Mandatory)][string]$CondaExe,
        [switch]$AllowDefault,
        [ValidateSet('v4', 'v3', 'default')]
        [string]$Engine = 'default'
    )

    if ($Prefix) {
        return [System.IO.Path]::GetFullPath($Prefix.Trim().Trim('"'))
    }

    $Saved = Read-CellQuantEnvPrefix -ProjectRoot $ProjectRoot -Engine $Engine
    if ($Saved) {
        return [System.IO.Path]::GetFullPath($Saved)
    }

    if ($AllowDefault) {
        $DefaultEngine = if ($Engine -eq 'default') { 'v4' } else { $Engine }
        return Get-DefaultCellQuantEnvPrefix -CondaExe $CondaExe -Engine $DefaultEngine
    }

    return $null
}

function Get-CellQuantInstallPrefixProblem {
    <#
    .SYNOPSIS
    Return a human-readable reason the install prefix cannot be used, or $null if OK.
    Detects other users' profile paths and folders this account cannot write to.
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

    # Paths under C:\Users\<other>\... almost always fail after copying the project
    # (or cellquant_env.json) from another machine/account.
    $UsersRoot = Join-Path $env:SystemDrive 'Users'
    if ($env:USERNAME -and (Test-Path -LiteralPath $UsersRoot)) {
        $UsersRootFull = [System.IO.Path]::GetFullPath($UsersRoot)
        $PrefixWithSep = $UsersRootFull.TrimEnd('\') + '\'
        if ($Resolved.StartsWith($PrefixWithSep, [StringComparison]::OrdinalIgnoreCase)) {
            $Relative = $Resolved.Substring($PrefixWithSep.Length)
            $ProfileName = ($Relative -split '[\\/]', 2)[0]
            if (
                $ProfileName -and
                $ProfileName -ne $env:USERNAME -and
                $ProfileName -ne 'Public' -and
                $ProfileName -ne 'Default' -and
                $ProfileName -ne 'Default User' -and
                $ProfileName -ne 'All Users'
            ) {
                return "This path is under another Windows user profile ('$ProfileName'). Install into a folder on this account instead."
            }
        }
    }

    $Parent = Split-Path -Parent $Resolved
    if (-not $Parent) {
        return 'The path has no parent folder.'
    }

    $Ancestor = $Parent
    while ($Ancestor -and -not (Test-Path -LiteralPath $Ancestor)) {
        $Next = Split-Path -Parent $Ancestor
        if (-not $Next -or $Next -eq $Ancestor) {
            break
        }
        $Ancestor = $Next
    }

    if (-not $Ancestor -or -not (Test-Path -LiteralPath $Ancestor)) {
        return "The drive or parent folder does not exist: $Parent"
    }

    $TestFile = Join-Path $Ancestor ("cellquant_write_test_{0}.tmp" -f [guid]::NewGuid().ToString('N'))
    try {
        [System.IO.File]::WriteAllText($TestFile, 'ok')
        Remove-Item -LiteralPath $TestFile -Force -ErrorAction SilentlyContinue
    } catch {
        return "This Windows account cannot write to: $Ancestor"
    }

    return $null
}
