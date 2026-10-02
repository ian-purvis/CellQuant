# What this computer has, and which Cellpose engine suits it.
#
# NVIDIA details come from nvidia-smi, which the NVIDIA driver installs.
# The CUDA wheel choice is the same as in CellQuant v1 (resolve_cuda_torch.ps1
# there), which has been used on the lab's computers.

function Get-NvidiaSmiPath {
    $Command = Get-Command nvidia-smi.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($Command) {
        return $Command.Path
    }
    # Not on PATH: the driver also puts it in System32, older drivers in NVSMI.
    foreach ($Candidate in @(
            (Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'),
            (Join-Path $env:ProgramFiles 'NVIDIA Corporation\NVSMI\nvidia-smi.exe')
        )) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return $Candidate
        }
    }
    return $null
}

function Invoke-NvidiaSmiQuery {
    param([Parameter(Mandatory)][string]$Field)

    $NvidiaSmi = Get-NvidiaSmiPath
    if (-not $NvidiaSmi) {
        return @()
    }
    try {
        $Output = & $NvidiaSmi "--query-gpu=$Field" '--format=csv,noheader,nounits' 2>$null
    } catch {
        return @()
    }
    return @($Output | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
}

function Get-NvidiaDriverCudaVersion {
    <#
    .SYNOPSIS
    Highest CUDA version the installed NVIDIA driver supports, or $null.
    #>
    $NvidiaSmi = Get-NvidiaSmiPath
    if (-not $NvidiaSmi) {
        return $null
    }
    try {
        $Overview = & $NvidiaSmi 2>$null | Out-String
    } catch {
        return $null
    }
    $Match = [regex]::Match([string]$Overview, 'CUDA Version:\s*([0-9]+(?:\.[0-9]+)?)')
    if (-not $Match.Success) {
        return $null
    }
    return [version]$Match.Groups[1].Value
}

function Get-CellQuantTorchCudaTag {
    param(
        [Parameter(Mandatory)][version]$DriverCudaVersion,
        [version]$GpuComputeCap = $null
    )

    # Newest PyTorch CUDA wheel the driver can load. The tags must exist at
    # https://download.pytorch.org/whl/<tag>/torch/ for win_amd64.
    $Candidates = @(
        @{ Min = [version]'13.0'; Tag = 'cu130' },
        @{ Min = [version]'12.9'; Tag = 'cu129' },
        @{ Min = [version]'12.8'; Tag = 'cu128' },
        @{ Min = [version]'12.6'; Tag = 'cu126' },
        @{ Min = [version]'12.4'; Tag = 'cu124' },
        @{ Min = [version]'12.1'; Tag = 'cu121' },
        @{ Min = [version]'11.8'; Tag = 'cu118' }
    )
    $Tag = $null
    foreach ($Candidate in $Candidates) {
        if ($DriverCudaVersion -ge $Candidate.Min) {
            $Tag = [string]$Candidate.Tag
            break
        }
    }
    if (-not $Tag) {
        return $null
    }
    # Blackwell GPUs (compute capability 12.x) need cu128 or newer.
    if ($null -ne $GpuComputeCap -and $GpuComputeCap -ge [version]'12.0') {
        if (@('cu128', 'cu129', 'cu130') -notcontains $Tag) {
            return $null
        }
    }
    return $Tag
}

function Get-CellQuantTorchCudaIndexUrl {
    param([Parameter(Mandatory)][string]$CudaTag)
    return "https://download.pytorch.org/whl/$CudaTag"
}

function Get-CellQuantHardware {
    <#
    .SYNOPSIS
    GPU, memory, and the PyTorch CUDA build to install. Never throws.
    #>
    $Hardware = [ordered]@{
        gpu_name = $null
        gpu_memory_gb = $null
        driver_cuda = $null
        compute_cap = $null
        cuda_tag = $null
        cuda_problem = $null
        ram_gb = $null
    }

    $Names = Invoke-NvidiaSmiQuery -Field 'name'
    if ($Names.Count -gt 0) {
        $Hardware.gpu_name = [string]$Names[0]
        $Memory = Invoke-NvidiaSmiQuery -Field 'memory.total'
        if ($Memory.Count -gt 0 -and $Memory[0] -match '^\d+') {
            $Hardware.gpu_memory_gb = [math]::Round(([double]$Matches[0]) / 1024, 1)
        }
        $Caps = Invoke-NvidiaSmiQuery -Field 'compute_cap'
        $Cap = $null
        if ($Caps.Count -gt 0 -and $Caps[0] -match '^\d+\.\d+$') {
            $Cap = [version]$Caps[0]
            $Hardware.compute_cap = [string]$Cap
        }
        $DriverCuda = Get-NvidiaDriverCudaVersion
        if ($DriverCuda) {
            $Hardware.driver_cuda = [string]$DriverCuda
            $Tag = Get-CellQuantTorchCudaTag -DriverCudaVersion $DriverCuda -GpuComputeCap $Cap
            if ($Tag) {
                $Hardware.cuda_tag = $Tag
            } else {
                $Hardware.cuda_problem = "The NVIDIA driver is too old for this GPU (driver CUDA $DriverCuda). Update the NVIDIA driver, then run Install CellQuant.bat again to add GPU support."
            }
        } else {
            $Hardware.cuda_problem = 'nvidia-smi did not report a CUDA version. Update the NVIDIA driver to use the GPU.'
        }
    }

    try {
        $Bytes = (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
        $Hardware.ram_gb = [math]::Round(([double]$Bytes) / 1GB, 0)
    } catch {
        $Hardware.ram_gb = $null
    }
    return $Hardware
}

function Get-CellQuantEngineRecommendation {
    <#
    .SYNOPSIS
    Which engine to open by default on this computer, and why, in plain words.
    #>
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Hardware)

    # Rule of thumb, not a measurement: Cellpose-SAM is a large model. Below
    # about 6 GB of GPU memory it may run out of memory on big images.
    $MinimumGpuGb = 6
    if ($Hardware.cuda_tag) {
        if ($null -ne $Hardware.gpu_memory_gb -and $Hardware.gpu_memory_gb -lt $MinimumGpuGb) {
            return @{
                engine = 'cellpose3'
                reason = "the GPU has $($Hardware.gpu_memory_gb) GB of memory, and Cellpose-SAM may run out of GPU memory"
            }
        }
        return @{
            engine = 'cellpose4'
            reason = "this computer has an NVIDIA GPU ($($Hardware.gpu_name)) that Cellpose-SAM can use"
        }
    }
    if ($Hardware.gpu_name) {
        return @{
            engine = 'cellpose3'
            reason = 'the NVIDIA GPU cannot be used until its driver is updated, and Cellpose-SAM is slow without a GPU'
        }
    }
    return @{
        engine = 'cellpose3'
        reason = 'there is no NVIDIA GPU, and Cellpose-SAM is slow without one, especially for Z-stacks'
    }
}

function Get-CellQuantFreeDiskGb {
    param([Parameter(Mandatory)][string]$Path)

    try {
        $Root = [System.IO.Path]::GetPathRoot([System.IO.Path]::GetFullPath($Path))
        $Drive = New-Object System.IO.DriveInfo($Root)
        return [math]::Round($Drive.AvailableFreeSpace / 1GB, 0)
    } catch {
        return $null
    }
}

function Get-CellQuantEngineSizeGb {
    <#
    .SYNOPSIS
    Rough disk space per engine: GPU PyTorch is several GB larger than CPU PyTorch.
    #>
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Hardware)

    if ($Hardware.cuda_tag) {
        return 8
    }
    return 4
}

function Test-CellQuantTorchCudaUsable {
    param(
        [Parameter(Mandatory)][string]$CondaExe,
        [Parameter(Mandatory)][string]$EnvPrefix
    )

    $ProbeScript = Join-Path $PSScriptRoot 'probe_torch_cuda.py'
    $PreviousEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $Output = & $CondaExe run -p $EnvPrefix python $ProbeScript 2>$null
        $Code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousEap
    }
    $Line = @($Output | ForEach-Object { "$_".Trim() } | Where-Object { $_ -match '\|' }) | Select-Object -Last 1
    if ($Code -ne 0 -or -not $Line) {
        return @{ ok = $false; raw = [string]($Output | Out-String) }
    }
    # version|cuda_build|available|device_name|arch_ok|required_sm|arch_list
    $Parts = $Line.Split('|')
    $Available = ($Parts.Count -ge 3 -and $Parts[2] -eq 'True')
    $ArchOk = ($Parts.Count -ge 5 -and $Parts[4] -eq 'True')
    return @{
        ok = ($Available -and $ArchOk)
        version = $Parts[0]
        cuda_build = $(if ($Parts.Count -ge 2) { $Parts[1] } else { '' })
        device_name = $(if ($Parts.Count -ge 4) { $Parts[3] } else { '' })
        required_sm = $(if ($Parts.Count -ge 6) { $Parts[5] } else { '' })
        arch_list = $(if ($Parts.Count -ge 7) { $Parts[6] } else { '' })
        raw = $Line
    }
}
