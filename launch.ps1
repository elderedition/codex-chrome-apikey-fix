$ErrorActionPreference = 'Stop'

function Test-PythonCommand {
    param([string]$Executable, [string[]]$Prefix = @())
    if (-not $Executable -or $Executable -match '(?i)[\\/]WindowsApps[\\/]') { return $false }
    if (-not [System.IO.Path]::IsPathRooted($Executable)) { return $false }
    if (-not [System.IO.File]::Exists($Executable)) { return $false }
    try {
        $probe = & $Executable @Prefix -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 19)' 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

$chosen = $null
$chosenPrefix = @()
if (Test-Path Env:CHROME_COMPAT_PYTHON) {
    if (-not (Test-PythonCommand $env:CHROME_COMPAT_PYTHON)) {
        [Console]::Error.WriteLine('{"ok":false,"code":"python-override-invalid","runtimeLoaded":"not-checked"}')
        exit 1
    }
    $chosen = $env:CHROME_COMPAT_PYTHON
} else {
    foreach ($environmentRoot in @($env:VIRTUAL_ENV, $env:CONDA_PREFIX)) {
        if ($environmentRoot) {
            foreach ($relative in @('Scripts\python.exe', 'python.exe')) {
                $candidate = Join-Path $environmentRoot $relative
                if (Test-PythonCommand $candidate) { $chosen = $candidate; break }
            }
        }
        if ($chosen) { break }
    }
    if (-not $chosen) {
        foreach ($command in @(Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue)) {
            if (Test-PythonCommand $command.Source @('-3')) {
                $chosen = $command.Source
                $chosenPrefix = @('-3')
                break
            }
        }
    }
    if (-not $chosen) {
        foreach ($name in @('python.exe', 'python3.exe')) {
            foreach ($command in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
                if (Test-PythonCommand $command.Source) { $chosen = $command.Source; break }
            }
            if ($chosen) { break }
        }
    }
    if (-not $chosen) {
        foreach ($command in @(Get-Command conda.bat -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $condaRoot = Split-Path (Split-Path $command.Source -Parent) -Parent
            $candidate = Join-Path $condaRoot 'python.exe'
            if (Test-PythonCommand $candidate) { $chosen = $candidate; break }
        }
    }
}
if (-not $chosen) {
    [Console]::Error.WriteLine('{"ok":false,"code":"python-not-found","runtimeLoaded":"not-checked"}')
    exit 1
}
& $chosen @chosenPrefix (Join-Path $PSScriptRoot 'patch.py') @args
exit $LASTEXITCODE
