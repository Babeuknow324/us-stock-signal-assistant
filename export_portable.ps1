$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectName = Split-Path -Leaf $ProjectRoot
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ZipPath = Join-Path (Split-Path -Parent $ProjectRoot) "$ProjectName`_$Timestamp.zip"

$TempDir = Join-Path $env:TEMP "$ProjectName`_portable_$Timestamp"
New-Item -ItemType Directory -Path $TempDir | Out-Null

Copy-Item -Recurse -Path $ProjectRoot\* -Destination $TempDir

$Excluded = @(".venv", "__pycache__", ".env", "data\signals.db")
foreach ($item in $Excluded) {
    $target = Join-Path $TempDir $item
    if (Test-Path $target) {
        Remove-Item -Recurse -Force $target
    }
}

if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
}

Compress-Archive -Path "$TempDir\*" -DestinationPath $ZipPath
Remove-Item -Recurse -Force $TempDir

Write-Output "Portable package created: $ZipPath"
