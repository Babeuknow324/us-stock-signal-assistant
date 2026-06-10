param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("us", "mix")]
    [string]$Profile
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Source = Join-Path $ProjectRoot (".env.{0}.example" -f $Profile)
$Target = Join-Path $ProjectRoot ".env"

if (!(Test-Path $Source)) {
    throw "Profile file not found: $Source"
}

Copy-Item -Force $Source $Target
Write-Output "Switched .env profile to: $Profile"
Write-Output "Source: $Source"
Write-Output "Target: $Target"
