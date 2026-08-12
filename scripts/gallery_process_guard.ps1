[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [ValidateRange(1, 65535)]
    [int]$Port = 8797,

    [ValidateSet("Check", "Stop")]
    [string]$Action = "Check"
)

$ErrorActionPreference = "Stop"

try {
    $canonicalRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd("\", "/")
} catch {
    [Console]::Error.WriteLine("Gallery project root does not exist: $ProjectRoot")
    exit 2
}

$serverPath = Join-Path $canonicalRoot "server.py"
$pythonPrefixPattern = '(?i)^\s*(?:"[^"]*\\(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?\.exe"|[^\s"]*(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?\.exe)'
$pythonFlagsPattern = '(?:\s+-(?:u|B|E|I|O|OO|s|S|v|V))*'
# Accept both absolute (E:\...\server.py) and relative (server.py / .\server.py) launches:
# START_GALLERY.bat passes absolute; manual wscript/other callers may pass relative.
$serverFileName = [Regex]::Escape((Split-Path -Leaf $serverPath))
$serverArgumentPattern = '\s+"?(?:' + [Regex]::Escape($serverPath) + '|(?:\.\\\\)?' + $serverFileName + ')"?(?=\s|$)'
$serverCommandPattern = $pythonPrefixPattern + $pythonFlagsPattern + $serverArgumentPattern
$connections = @(
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Sort-Object -Property OwningProcess -Unique
)

if ($connections.Count -eq 0) {
    Write-Output "No listener is using port $Port."
    exit 0
}

$ownedProcesses = @()
foreach ($connection in $connections) {
    $listenerPid = [int]$connection.OwningProcess
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    $commandLine = if ($null -eq $processInfo) { "" } else { [string]$processInfo.CommandLine }

    $runsProjectServer = $commandLine -match $serverCommandPattern
    if (-not $runsProjectServer) {
        $displayCommand = if ([string]::IsNullOrWhiteSpace($commandLine)) { "<unavailable>" } else { $commandLine }
        [Console]::Error.WriteLine("Refusing to stop non-gallery process PID $listenerPid on port $Port. CommandLine: $displayCommand")
        exit 3
    }

    $ownedProcesses += [PSCustomObject]@{
        Pid = $listenerPid
        CreationDate = if ($null -eq $processInfo) { $null } else { $processInfo.CreationDate }
    }
}

if ($Action -eq "Check") {
    Write-Output "Port $Port is owned by this gallery project (PID $((@($ownedProcesses | ForEach-Object { $_.Pid })) -join ', '))."
    exit 0
}

foreach ($ownedProcess in $ownedProcesses) {
    $listenerPid = [int]$ownedProcess.Pid
    $currentConnection = Get-NetTCPConnection -State Listen -LocalPort $Port -OwningProcess $listenerPid -ErrorAction SilentlyContinue
    $currentProcessInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    $sameProcess = (
        $null -ne $currentProcessInfo -and
        $null -ne $currentConnection -and
        $currentProcessInfo.CreationDate -eq $ownedProcess.CreationDate -and
        ([string]$currentProcessInfo.CommandLine) -match $serverCommandPattern
    )
    if (-not $sameProcess) {
        [Console]::Error.WriteLine("Refusing to stop PID $listenerPid because its listener identity changed.")
        exit 4
    }
    Write-Output "Stopping gallery PID $listenerPid on port $Port..."
    try {
        Stop-Process -Id $listenerPid -Force -ErrorAction Stop
    } catch {
        [Console]::Error.WriteLine("Failed to stop gallery PID $listenerPid on port ${Port}: $($_.Exception.Message)")
        exit 4
    }
}

exit 0
