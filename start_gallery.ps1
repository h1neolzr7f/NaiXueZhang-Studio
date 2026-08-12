param(
    [ValidateSet("open", "restart", "watch")]
    [string]$Mode = "open"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$launcher = Join-Path $root "START_GALLERY.bat"

if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Error "Safe launcher is missing: $launcher"
    exit 2
}

# Keep one startup implementation. START_GALLERY.bat owns the project-aware
# health check, first-run bootstrap and process guard, so this PowerShell entry
# point cannot kill an unrelated listener or fork a second startup path.
& $env:ComSpec /d /c "`"$launcher`" $Mode"
exit $LASTEXITCODE
