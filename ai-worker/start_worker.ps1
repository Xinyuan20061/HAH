$ErrorActionPreference = "Continue"
$WorkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $WorkerRoot
$VenvDir = Join-Path $WorkerRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

$PythonExe = $VenvPython
if (-not (Test-Path -LiteralPath $VenvPython)) {
    $PythonExe = "python"
}

# MediaPipe's native graph loader cannot open its .binarypb resources from a
# path that contains non-ASCII characters (e.g. a Chinese project folder):
# ValidatedGraphConfig.initialize() then reports a misleading FileNotFoundError.
# Work around it transparently by running the venv through an ASCII junction.
function Test-NonAscii([string]$Text) {
    foreach ($ch in $Text.ToCharArray()) {
        if ([int]$ch -gt 127) { return $true }
    }
    return $false
}

if ((Test-Path -LiteralPath $VenvPython) -and (Test-NonAscii $VenvPython)) {
    $LinkRoot = Join-Path $env:LOCALAPPDATA "HealthMate"
    $LinkPath = Join-Path $LinkRoot "venvlink"
    $recreate = $true
    if (Test-Path -LiteralPath $LinkPath) {
        $existing = (Get-Item -LiteralPath $LinkPath).Target
        if ($existing -and ($existing.ToString().TrimEnd('\') -ieq $VenvDir.TrimEnd('\'))) {
            $recreate = $false
        } else {
            (Get-Item -LiteralPath $LinkPath).Delete()
        }
    }
    if ($recreate) {
        New-Item -ItemType Directory -Force -Path $LinkRoot | Out-Null
        New-Item -ItemType Junction -Path $LinkPath -Target $VenvDir | Out-Null
    }
    $PythonExe = Join-Path $LinkPath "Scripts\python.exe"
    Write-Host "MediaPipe non-ASCII path workaround active: $PythonExe"
}

Write-Host "HealthMate Worker watchdog started. Press Ctrl+C to stop."
while ($true) {
    & $PythonExe "worker.py"
    $ExitCode = $LASTEXITCODE
    Write-Warning "Worker exited with code $ExitCode; restarting in 5 seconds."
    Start-Sleep -Seconds 5
}
