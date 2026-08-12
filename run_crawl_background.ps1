$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$logs = Join-Path $root "logs"
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$log = Join-Path $logs "pixiv-nai-supervisor.log"
$crawlerFile = Join-Path $root "pixiv_nai_crawler.py"
$pythonExe = Join-Path $root "runtime\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = Join-Path $root ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = "python"
}
$restartDelaySec = 5
$maxRestartDelaySec = 60
$env:PYTHONUNBUFFERED = "1"

function Write-SupervisorLog([string]$Message) {
    $line = "[$(Get-Date -Format o)] $Message"
    $line | Out-File -FilePath $log -Append -Encoding utf8
    Write-Output $line
}

while ($true) {
    Write-SupervisorLog "Pixiv direct NAI intake start"
    & $pythonExe -u $crawlerFile --watch *>> $log
    $code = $LASTEXITCODE
    Write-SupervisorLog "Pixiv intake exited with code=$code; restart in ${restartDelaySec}s"
    Start-Sleep -Seconds $restartDelaySec
    $restartDelaySec = [Math]::Min($maxRestartDelaySec, $restartDelaySec * 2)
}
