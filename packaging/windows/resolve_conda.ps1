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

    throw "Conda was not found. Install Miniconda/Miniforge or add conda to PATH. Searched: $($SearchedLocations -join '; ')"
}
