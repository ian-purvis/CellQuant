function Resolve-CondaExecutable {
    $SearchedLocations = [System.Collections.Generic.List[string]]::new()

    if ($env:CONDA_EXE) {
        $SearchedLocations.Add("CONDA_EXE=$($env:CONDA_EXE)")
        if (Test-Path -LiteralPath $env:CONDA_EXE -PathType Leaf) {
            return $env:CONDA_EXE
        }
    }

    foreach ($CommandName in @('conda.exe', 'conda')) {
        $SearchedLocations.Add("application on PATH: $CommandName")
        $CondaCommand = Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($CondaCommand -and $CondaCommand.Path -and
            (Test-Path -LiteralPath $CondaCommand.Path -PathType Leaf)) {
            return $CondaCommand.Path
        }
    }

    if ($env:USERPROFILE) {
        foreach ($InstallName in @('Miniconda3', 'Miniforge3', 'Mambaforge', 'Anaconda3')) {
            $Candidate = Join-Path $env:USERPROFILE "$InstallName\Scripts\conda.exe"
            $SearchedLocations.Add($Candidate)
            if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
                return $Candidate
            }
        }
    } else {
        $SearchedLocations.Add('USERPROFILE was not set')
    }

    # All-users installs, and the folder Install-CellQuantMiniforge falls back to.
    $SharedCandidates = [System.Collections.Generic.List[string]]::new()
    $SharedCandidates.Add((Join-Path (Get-CellQuantSystemDrive) 'miniforge3\Scripts\conda.exe'))
    if ($env:ProgramData) {
        foreach ($InstallName in @('miniforge3', 'Miniconda3', 'Anaconda3')) {
            $SharedCandidates.Add((Join-Path $env:ProgramData "$InstallName\Scripts\conda.exe"))
        }
    }
    foreach ($Candidate in $SharedCandidates) {
        $SearchedLocations.Add($Candidate)
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            return $Candidate
        }
    }

    throw "Conda was not found. Searched: $($SearchedLocations -join '; ')"
}

# Miniforge (conda-forge's free conda) is installed for users who have no conda.
# The version is pinned so the download can be checked against a known SHA256.
# The environments are built fresh from conda-forge, so this version only
# needs updating if it stops working.
$script:CellQuantMiniforgeVersion = '26.7.2-0'
$script:CellQuantMiniforgeSha256 = '71cf9519087be74fa53021219ff292beb2fc05fa49e0bb6eb0e0b6b14fccbaab'

function Get-CellQuantSystemDrive {
    if ($env:SystemDrive) { return "$($env:SystemDrive)\" }
    return 'C:\'
}

function Get-CellQuantMiniforgeTarget {
    # The Miniforge installer refuses folders with spaces or non-ASCII
    # characters, which some user names have. C:\miniforge3 is the fallback.
    $Candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:USERPROFILE) {
        $Candidates.Add((Join-Path $env:USERPROFILE 'miniforge3'))
    }
    $Candidates.Add((Join-Path (Get-CellQuantSystemDrive) 'miniforge3'))
    foreach ($Candidate in $Candidates) {
        if ($Candidate -cmatch '[^\x21-\x7E]') {
            continue
        }
        if ((Test-Path -LiteralPath $Candidate) -and
            (Get-ChildItem -LiteralPath $Candidate -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            continue
        }
        return $Candidate
    }
    throw "No folder to install Miniforge into. Tried: $($Candidates -join '; ') (the folder must be empty and its path must have no spaces or accented letters)."
}

function Save-CellQuantDownload {
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$OutFile
    )

    try {
        # Windows PowerShell 5.1 may not offer TLS 1.2 by default; GitHub needs it.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch { }
    $PreviousProgress = $ProgressPreference
    # The progress bar makes large downloads many times slower in PowerShell 5.1.
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
    } catch {
        $FirstError = $_.Exception.Message
        $Curl = Get-Command 'curl.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $Curl) {
            throw "Download failed: $Url ($FirstError)"
        }
        & $Curl.Path --location --fail --silent --show-error --output $OutFile $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed: $Url ($FirstError; curl exit code $LASTEXITCODE)"
        }
    } finally {
        $ProgressPreference = $PreviousProgress
    }
}

function Get-CellQuantFileSha256 {
    # .NET rather than Get-FileHash, which a clean Windows PowerShell 5.1 did not find
    # inside the installer.
    param([Parameter(Mandatory)][string]$Path)

    $Sha = [System.Security.Cryptography.SHA256]::Create()
    $Stream = [System.IO.File]::OpenRead($Path)
    try {
        return ([System.BitConverter]::ToString($Sha.ComputeHash($Stream)) -replace '-', '').ToLowerInvariant()
    } finally {
        $Stream.Dispose()
        $Sha.Dispose()
    }
}

function Install-CellQuantMiniforge {
    param([Parameter(Mandatory)][string]$Target)

    $Version = $script:CellQuantMiniforgeVersion
    $FileName = "Miniforge3-$Version-Windows-x86_64.exe"
    $Url = "https://github.com/conda-forge/miniforge/releases/download/$Version/$FileName"
    $TempRoot = $env:TEMP
    if (-not $TempRoot) { $TempRoot = [System.IO.Path]::GetTempPath() }
    $DownloadDir = Join-Path $TempRoot 'CellQuant-miniforge'
    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    $Installer = Join-Path $DownloadDir $FileName

    try {
        Write-Host "  Downloading Miniforge $Version (about 150 MB)..."
        Save-CellQuantDownload -Url $Url -OutFile $Installer
        $Hash = Get-CellQuantFileSha256 -Path $Installer
        if ($Hash -ne $script:CellQuantMiniforgeSha256) {
            throw "The Miniforge download did not match its expected checksum (got $Hash). Try again; if it keeps failing, install Miniforge yourself."
        }

        Write-Host "  Installing Miniforge to $Target (a few minutes)..."
        # Just for this user, not added to PATH and not registered as the system
        # Python, so other software on the computer is unaffected. /D must be
        # last and unquoted.
        $Process = Start-Process -FilePath $Installer -Wait -PassThru -ArgumentList @(
            '/InstallationType=JustMe', '/RegisterPython=0', '/AddToPath=0', '/S', "/D=$Target"
        )
        $CondaExe = Join-Path $Target 'Scripts\conda.exe'
        if ($Process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $CondaExe -PathType Leaf)) {
            throw "The Miniforge installer did not finish (exit code $($Process.ExitCode)); $CondaExe was not created."
        }
        return $CondaExe
    } finally {
        Remove-Item -LiteralPath $Installer -Force -ErrorAction SilentlyContinue
    }
}
