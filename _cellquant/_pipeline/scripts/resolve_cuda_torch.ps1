# Resolve and install a driver-compatible CUDA PyTorch build for CellQuant.
# Prefer CUDA 12.8+ (cu128) wheels when the driver allows: one build covers
# Blackwell (sm_120) and older GPUs (e.g. Turing sm_75 / Ampere / Hopper).

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

function Get-NvidiaGpuComputeCapability {
    <#
    .SYNOPSIS
    First GPU compute capability as major.minor (e.g. 12.0), or $null.
    #>

    $NvidiaSmi = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $NvidiaSmi) {
        return $null
    }
    try {
        $Query = & $NvidiaSmi.Path --query-gpu=compute_cap --format=csv,noheader 2>$null
    } catch {
        return $null
    }
    $Caps = @($Query | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '^\d+\.\d+$' })
    if ($Caps.Count -eq 0) {
        return $null
    }
    return [version]$Caps[0]
}

function Get-CellQuantTorchCudaTag {
    param(
        [Parameter(Mandatory)][version]$DriverCudaVersion,
        [version]$GpuComputeCap = $null
    )

    # Prefer the newest PyTorch CUDA wheel the driver can load.
    # Tags must exist on https://download.pytorch.org/whl/<tag>/torch/ for win_amd64.
    # cu128+ is the preferred floor for Blackwell (sm_120) and still supports older GPUs.
    $Candidates = @(
        @{ Min = [version]'13.0'; Tag = 'cu130' },
        @{ Min = [version]'12.9'; Tag = 'cu129' },
        @{ Min = [version]'12.8'; Tag = 'cu128' },
        @{ Min = [version]'12.6'; Tag = 'cu126' },
        @{ Min = [version]'12.4'; Tag = 'cu124' },
        @{ Min = [version]'12.1'; Tag = 'cu121' },
        @{ Min = [version]'11.8'; Tag = 'cu118' }
    )
    $Tag = 'cu118'
    foreach ($Candidate in $Candidates) {
        if ($DriverCudaVersion -ge $Candidate.Min) {
            $Tag = [string]$Candidate.Tag
            break
        }
    }

    # Blackwell (compute 12.x) requires a cu128+ wheel; refuse older tags.
    if ($null -ne $GpuComputeCap -and $GpuComputeCap -ge [version]'12.0') {
        $BlackwellOk = @('cu128', 'cu129', 'cu130')
        if ($BlackwellOk -notcontains $Tag) {
            return $null
        }
    }
    return $Tag
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
            arch_ok = $false
            required_sm = $null
            arch_list = $null
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
            arch_ok = $false
            required_sm = $null
            arch_list = $null
            raw = [string]$Output
        }
    }
    $Parts = $Line.Split('|')
    $Version = if ($Parts.Count -ge 1) { $Parts[0] } else { $null }
    $CudaBuild = if ($Parts.Count -ge 2 -and $Parts[1]) { $Parts[1] } else { $null }
    $Available = if ($Parts.Count -ge 3) { $Parts[2] } else { '' }
    $DeviceName = if ($Parts.Count -ge 4) { $Parts[3] } else { '' }
    $ArchOk = if ($Parts.Count -ge 5) { $Parts[4] -eq 'True' } else { $Available -eq 'True' }
    $RequiredSm = if ($Parts.Count -ge 6) { $Parts[5] } else { $null }
    $ArchList = if ($Parts.Count -ge 7) { $Parts[6] } else { $null }
    $CudaReady = ($Available -eq 'True') -and $ArchOk
    return @{
        ok = $CudaReady
        version = $Version
        cuda_build = $CudaBuild
        device_name = $DeviceName
        arch_ok = $ArchOk
        required_sm = $RequiredSm
        arch_list = $ArchList
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
    $GpuCap = Get-NvidiaGpuComputeCapability
    if (-not $DriverCuda) {
        Write-Host ''
        Write-Host 'No NVIDIA GPU / nvidia-smi CUDA version detected.'
        Write-Host 'Leaving the default (CPU) PyTorch build in place.'
        return @{ installed = $false; reason = 'no_nvidia' }
    }

    $CudaTag = Get-CellQuantTorchCudaTag -DriverCudaVersion $DriverCuda -GpuComputeCap $GpuCap
    if (-not $CudaTag) {
        throw @(
            "GPU compute capability $GpuCap (Blackwell) needs a CUDA 12.8+ PyTorch wheel (cu128+),",
            "but nvidia-smi reports driver CUDA $DriverCuda.",
            'Update the NVIDIA driver, then re-run Install CellQuant.bat.'
        ) -join ' '
    }
    $IndexUrl = Get-CellQuantTorchCudaIndexUrl -CudaTag $CudaTag
    $GpuLabel = if ($GpuName) { $GpuName } else { 'NVIDIA GPU' }
    $CapLabel = if ($GpuCap) { " compute $GpuCap" } else { '' }

    Write-Host ''
    Write-Host "CUDA GPU detected: $GpuLabel$CapLabel (driver CUDA $DriverCuda)"

    $Existing = Test-CellQuantTorchCudaUsable -CondaExe $CondaExe -EnvPrefix $EnvPrefix
    if ($Existing.ok -and $Existing.version -and ($Existing.version -match [regex]::Escape("+$CudaTag") -or $Existing.cuda_build)) {
        Write-Host "Existing CUDA PyTorch already usable: $($Existing.version) on $($Existing.device_name) ($($Existing.required_sm))"
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
            required_sm = $Existing.required_sm
        }
    }

    if ($Existing.raw -and ($Existing.raw -match '\|True\|') -and -not $Existing.arch_ok) {
        Write-Host "Existing PyTorch reports CUDA but lacks GPU arch $($Existing.required_sm); reinstalling $CudaTag..."
    } else {
        Write-Host "Installing PyTorch CUDA build ($CudaTag) so Cellpose can use the GPU..."
    }
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
        $Hint = if (-not $Probe.arch_ok -and $Probe.required_sm) {
            "GPU needs $($Probe.required_sm); wheel arch list: $($Probe.arch_list). Prefer cu128+ with driver CUDA ≥ 12.8."
        } else {
            'Update the NVIDIA driver, then re-run Install CellQuant.bat.'
        }
        throw @(
            "CUDA PyTorch was installed ($CudaTag) but is not usable on this GPU.",
            "PyTorch probe: $($Probe.raw)",
            $Hint
        ) -join ' '
    }

    Write-Host "PyTorch CUDA ready: $($Probe.version) (CUDA $($Probe.cuda_build)) on $($Probe.device_name) ($($Probe.required_sm))"
    return @{
        installed = $true
        cuda_tag = $CudaTag
        version = $Probe.version
        cuda_build = $Probe.cuda_build
        device_name = $Probe.device_name
        gpu_name = $GpuName
        driver_cuda = "$DriverCuda"
        required_sm = $Probe.required_sm
    }
}
