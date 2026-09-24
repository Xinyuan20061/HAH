$ErrorActionPreference = "Continue"
$WorkerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $WorkerRoot
$PythonExe = Join-Path $WorkerRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = "python"
}

Write-Host "HealthMate Worker watchdog started. Press Ctrl+C to stop."
while ($true) {
    & $PythonExe "worker.py"
    $ExitCode = $LASTEXITCODE
    Write-Warning "Worker exited with code $ExitCode; restarting in 5 seconds."
    Start-Sleep -Seconds 5
}
