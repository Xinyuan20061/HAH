param(
    [string]$OutputPath = "healthmate-delivery.zip"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
$externalData = "D:\HealthMateData"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())

if ($resolvedOutput -notlike "$projectRoot\*") {
    throw "OutputPath must stay inside the project directory."
}
if ([System.IO.Path]::GetExtension($resolvedOutput) -ne ".zip") {
    throw "OutputPath must use the .zip extension."
}

$excludedDirectoryNames = @(".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".review-previews", "node_modules", "uploads", "dist", "cache")
$excludedExtensions = @(".db", ".pyc", ".pyo", ".zip", ".log")
$excludedFiles = @(".env")

Write-Host "External dataset directory is never packaged: $externalData"
Write-Host "Excluded directories: $($excludedDirectoryNames -join ', ') (plus any '.venv*' variant)"
Write-Host "Excluded extensions: $($excludedExtensions -join ', ')"
Write-Host "Excluded local secret files: $($excludedFiles -join ', ')"

if (Test-Path -LiteralPath $resolvedOutput) {
    throw "Output already exists; choose a new path or remove it explicitly: $resolvedOutput"
}

$stagingRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $tempRoot ("healthmate-delivery-" + [guid]::NewGuid().ToString("N")))
)
if ($stagingRoot -notlike "$tempRoot*") {
    throw "Refusing to use a staging directory outside the system temporary directory."
}
$stagingProject = Join-Path $stagingRoot "health-assistant"
New-Item -ItemType Directory -Path $stagingProject | Out-Null

try {
    Get-ChildItem -LiteralPath $projectRoot -Recurse -File | Where-Object {
        $relative = $_.FullName.Substring($projectRoot.Length).TrimStart('\')
        $segments = $relative -split '[\\/]'
        $directoryBlocked = @($segments | Where-Object { $_ -like ".venv*" -or ($excludedDirectoryNames -contains $_) }).Count -gt 0
        $fileBlocked = ($excludedFiles -contains $_.Name) -or ($excludedExtensions -contains $_.Extension)
        $outputBlocked = $_.FullName -eq $resolvedOutput
        -not ($directoryBlocked -or $fileBlocked -or $outputBlocked)
    } | ForEach-Object {
        $relative = $_.FullName.Substring($projectRoot.Length).TrimStart('\')
        $destination = Join-Path $stagingProject $relative
        $destinationDirectory = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
    Compress-Archive -LiteralPath $stagingProject -DestinationPath $resolvedOutput -CompressionLevel Optimal
    Write-Host "Created: $resolvedOutput"
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
