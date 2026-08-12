param(
    [switch]$SkipBrowserTests
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$portablePython = Join-Path $root "runtime\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } elseif (Test-Path -LiteralPath $portablePython) { $portablePython } else { "" }
$usingPortableRuntime = $python -eq $portablePython

if (-not $python) {
    throw "Python runtime is missing. Run INSTALL.bat or use a portable one-click package."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js is required for JavaScript and regression verification."
}
node -e "const major=Number(process.versions.node.split('.')[0]); process.exit(major >= 20 ? 0 : 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Node.js 20 or newer is required for repository verification."
}

Set-Location $root
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:GALLERY_NO_BROWSER = "1"
$env:GALLERY_NONINTERACTIVE = "1"

function Invoke-Checked {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments
    )

    Write-Host "==> $Name"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

if ($usingPortableRuntime) {
    # The embeddable runtime intentionally has no pip/ensurepip. Verify the
    # locked runtime by importing the packages needed by the server and tests.
    Invoke-Checked "Portable dependency imports" $python @(
        "-B",
        "-c",
        "import fastapi, httpx, PIL, pytest, uvicorn; print('portable dependencies import successfully')"
    )
} else {
    Invoke-Checked "Dependency integrity" $python @("-m", "pip", "check")
}

Write-Host "==> JavaScript syntax"
$jsFiles = @(
    Get-ChildItem -LiteralPath (Join-Path $root "web") -Recurse -File -Filter "*.js"
    Get-ChildItem -LiteralPath (Join-Path $root "scripts") -Recurse -File -Filter "*.js"
)
foreach ($file in $jsFiles) {
    node --check $file.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "JavaScript syntax failed for $($file.FullName)"
    }
}

$testTargets = @(
    Join-Path $root "tests"
    Get-ChildItem -LiteralPath $root -File -Filter "test_*.py" |
        Select-Object -ExpandProperty FullName
)
$pytestArgs = @("-B", "-m", "pytest") + $testTargets
if ($usingPortableRuntime) {
    # The embeddable CPython runtime intentionally omits venv; that repository-
    # setup test is covered by source checkouts using .venv.
    $pytestArgs += "--ignore=$(Join-Path $root 'tests\test_startup_safety.py')"
}
if ($SkipBrowserTests) {
    $pytestArgs += "--ignore=$(Join-Path $root 'tests\test_pixiv_selector_probe.py')"
}
$pytestArgs += @("-W", "error", "-p", "no:cacheprovider", "-q")
Invoke-Checked "Python tests" $python $pytestArgs

Invoke-Checked "Regression guard" "node" @(
    (Join-Path $root "scripts\check_regression_guards.js")
)
Invoke-Checked "Product quality gate" $python @(
    "-B",
    (Join-Path $root "scripts\product_quality_gate.py"),
    "--fail-on", "p2"
)

Write-Host "All repository verification steps passed."
