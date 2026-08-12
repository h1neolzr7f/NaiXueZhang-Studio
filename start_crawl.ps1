$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Path (Join-Path $root "logs") -Force | Out-Null
$pythonExe = Join-Path $root "runtime\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = Join-Path $root ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = "python"
}
& $pythonExe -u (Join-Path $root "pixiv_nai_crawler.py") --watch `
    *> (Join-Path $root "logs\pixiv-nai-crawl.log")
