# Resolve and install a driver-compatible CUDA PyTorch build for CellQuant.

function Get-NvidiaDriverCudaVersion {
    <#
    .SYNOPSIS
    Read the maximum CUDA toolkit version advertised by nvidia-smi, or $null.
    #>

    $NvidiaSmi = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $NvidiaSmi) {
        return $null
    }

    try {
        $Overview = & $NvidiaSmi.Path 2>$null | Out-String
    } catch {
        return $null
    }
    if (-not $Overview) {
        return $null
    }

    $Match = [regex]::Match($Overview, 'CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)')
    if (-not $Match.Success) {
        return $null
    }
    return [version]$Match.Groups[1].Value
}

function Get-NvidiaGpuName {
    $NvidiaSmi = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $NvidiaSmi) {
        return $null
    }
    try {
        $Query = & $NvidiaSmi.Path --query-gpu=name --format=csv,noheader 2>$null
    } catch {
        return $null
    }
    $Names = @($Query | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
    if ($Names.Count -eq 0) {
        return $null
    }
    return [string]$Names[0]
}

function Get-CellQuantTorchCudaTag {
    param([Parameter(Mandatory)][version]$DriverCudaVersion)

    # Prefer the newest PyTorch CUDA wheel the driver can load.
    # Tags must exist on https://download.pytorch.org/whl/<tag>/torch/ for win_amd64.
    $Candidates = @(
        @{ Min = [version]'13.0'; Tag = 'cu130' },
        @{ Min = [version]'12.9'; Tag = 'cu129' },
        @{ Min = [version]'12.8'; Tag = 'cu128' },
        @{ Min = [version]'12.6'; Tag = 'cu126' },
        @{ Min = [version]'12.4'; Tag = 'cu124' },
        @{ Min = [version]'12.1'; Tag = 'cu121' },
        @{ Min = [version]'11.8'; Tag = 'cu118' }
    )
    foreach ($Candidate in $Candidates) {
        if ($DriverCudaVersion -ge $Candidate.Min) {
            return [string]$Candidate.Tag
        }
    }
    return 'cu118'
}

function Get-CellQuantTorchCudaIndexUrl {
    param([Parameter(Mandatory)][string]$CudaTag)
    return "https://download.pytorch.org/whl/$CudaTag"
}

function Test-CellQuantTorchCudaUsable {
    param(
        [Parameter(Mandatory)][string]$CondaExe,
        [Parameter(Mandatory)][string]$EnvPrefix
    )

    $ProbeScript = Join-Path $PSScriptRoot 'probe_torch_cuda.py'
    if (-not (Test-Path -LiteralPath $ProbeScript -PathType Leaf)) {
        throw "Missing CUDA probe script: $ProbeScript"
    }

    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $Output = & $CondaExe run -p $EnvPrefix python $ProbeScript
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    if ($Code -ne 0 -or -not $Output) {
        return @{
            ok = $false
            version = $null
            cuda_build = $null
            device_name = $null
            raw = [string]$Output
        }
    }

    $Line = (@($Output | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '\|' }) | Select-Object -Last 1)
    if (-not $Line) {
        return @{
            ok = $false
            version = $null
            cuda_build = $null
            device_name = $null
            raw = [string]$Output
        }
    }
    $Parts = $Line.Split('|')
    $Version = if ($Parts.Count -ge 1) { $Parts[0] } else { $null }
    $CudaBuild = if ($Parts.Count -ge 2 -and $Parts[1]) { $Parts[1] } else { $null }
    $Available = if ($Parts.Count -ge 3) { $Parts[2] } else { '' }
    $DeviceName = if ($Parts.Count -ge 4) { $Parts[3] } else { '' }
    return @{
        ok = ($Available -eq 'True')
        version = $Version
        cuda_build = $CudaBuild
        device_name = $DeviceName
        raw = $Line
    }
}

function Install-CellQuantCudaTorch {
    param(
        [Parameter(Mandatory)][string]$CondaExe,
        [Parameter(Mandatory)][string]$EnvPrefix
    )

    $GpuName = Get-NvidiaGpuName
    $DriverCuda = Get-NvidiaDriverCudaVersion
    if (-not $DriverCuda) {
        Write-Host ''
        Write-Host 'No NVIDIA GPU / nvidia-smi CUDA version detected.'
        Write-Host 'Leaving the default (CPU) PyTorch build in place.'
        return @{ installed = $false; reason = 'no_nvidia' }
    }

    $CudaTag = Get-CellQuantTorchCudaTag -DriverCudaVersion $DriverCuda
    $IndexUrl = Get-CellQuantTorchCudaIndexUrl -CudaTag $CudaTag
    $GpuLabel = if ($GpuName) { $GpuName } else { 'NVIDIA GPU' }

    Write-Host ''
    Write-Host "CUDA GPU detected: $GpuLabel (driver CUDA $DriverCuda)"

    $Existing = Test-CellQuantTorchCudaUsable -CondaExe $CondaExe -EnvPrefix $EnvPrefix
    if ($Existing.ok -and $Existing.version -and ($Existing.version -match [regex]::Escape("+$CudaTag") -or $Existing.cuda_build)) {
        Write-Host "Existing CUDA PyTorch already usable: $($Existing.version) on $($Existing.device_name)"
        $PreviousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $CondaExe run --no-capture-output -p $EnvPrefix `
                python -m pip install --disable-pip-version-check "numpy==2.0.2"
        } finally {
            $ErrorActionPreference = $PreviousEap
        }
        return @{
            installed = $false
            already_ready = $true
            cuda_tag = $CudaTag
            version = $Existing.version
            cuda_build = $Existing.cuda_build
            device_name = $Existing.device_name
            gpu_name = $GpuName
            driver_cuda = "$DriverCuda"
        }
    }

    Write-Host "Installing PyTorch CUDA build ($CudaTag) so Cellpose can use the GPU..."
    Write-Host "  index: $IndexUrl"
    Write-Host ''

    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # Install torch only from the CUDA index. Avoid pulling an unrelated
        # torchvision/numpy stack that fights CellQuant's pinned numpy.
        & $CondaExe run --no-capture-output -p $EnvPrefix `
            python -m pip install --upgrade --force-reinstall `
            torch `
            --index-url $IndexUrl
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    if ($Code -ne 0) {
        throw "Failed to install CUDA PyTorch ($CudaTag) into $EnvPrefix (exit code $Code)"
    }

    # CUDA wheels may bump numpy; restore the CellQuant pin when present.
    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $CondaExe run --no-capture-output -p $EnvPrefix `
            python -m pip install --disable-pip-version-check "numpy==2.0.2"
    } finally {
        $ErrorActionPreference = $PreviousEap
    }

    $Probe = Test-CellQuantTorchCudaUsable -CondaExe $CondaExe -EnvPrefix $EnvPrefix
    if (-not $Probe.ok) {
        throw @(
            "CUDA PyTorch was installed ($CudaTag) but torch.cuda.is_available() is False.",
            "PyTorch probe: $($Probe.raw)",
            'Update the NVIDIA driver, then re-run Install CellQuant.bat.'
        ) -join ' '
    }

    Write-Host "PyTorch CUDA ready: $($Probe.version) (CUDA $($Probe.cuda_build)) on $($Probe.device_name)"
    return @{
        installed = $true
        cuda_tag = $CudaTag
        version = $Probe.version
        cuda_build = $Probe.cuda_build
        device_name = $Probe.device_name
        gpu_name = $GpuName
        driver_cuda = "$DriverCuda"
    }
}
