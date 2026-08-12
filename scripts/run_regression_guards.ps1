param(
  [string]$BaseUrl = "http://127.0.0.1:8797",
  [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$Manifest = Get-Content -Raw tests\regression_manifest.json | ConvertFrom-Json
$AppJsVersion = [string]$Manifest.app_js_version
# 入口脚本版本表（app.js / app-detail.js / app-online-remix.js 等），
# 由 scripts/asset_versions.py 刷新；通过环境变量传给浏览器探针校验。
$EntryVersionsJson = ""
if ($Manifest.PSObject.Properties.Name -contains "entry_versions" -and $Manifest.entry_versions) {
  $EntryVersionsJson = ($Manifest.entry_versions | ConvertTo-Json -Compress)
}

function Run-Step {
  param(
    [string]$Name,
    [scriptblock]$Body
  )
  Write-Host "==> $Name"
  & $Body
  if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    Write-Error "Step '$Name' failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
  }
}

Run-Step "JavaScript syntax" {
  Get-ChildItem web\plugins\char-swap\*.js | ForEach-Object {
    node --check $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "JS syntax check failed for $($_.Name)" }
  }
  node --check web\app.js
  if ($LASTEXITCODE -ne 0) { throw "JS syntax check failed for web/app.js" }
  node -e "const fs=require('fs'); const html=fs.readFileSync('web/generated.html','utf8'); const regex=/<script[^>]*>([\s\S]*?)<\/script>/g; let m; while ((m = regex.exec(html)) !== null) { if (m[1].trim()) new Function(m[1]); }"
  if ($LASTEXITCODE -ne 0) { throw "JS syntax check failed for web/generated.html scripts" }
}

Run-Step "Static regression guards" {
  node scripts\check_regression_guards.js
}

Run-Step "Python regression tests" {
  python -m unittest tests.test_architecture_upgrade
}

if (-not $SkipBrowser) {
  Run-Step "Server reachability" {
    Invoke-WebRequest -UseBasicParsing "$BaseUrl/" -TimeoutSec 5 | Out-Null
  }
  Run-Step "Char-swap current-page replacement probe" {
    node scripts\probe_char_swap_ui.js "--url=$BaseUrl/i/131437249" "--scenario=replace-female-current"
  }
  Run-Step "Token pool settings probe" {
    node scripts\probe_char_swap_ui.js "--url=$BaseUrl/i/131437249" "--scenario=token-settings"
  }
  Run-Step "Favorites page probe" {
    $previousEntryVersions = $env:EXPECTED_ENTRY_VERSIONS
    try {
      if ($EntryVersionsJson) {
        $env:EXPECTED_ENTRY_VERSIONS = $EntryVersionsJson
      }
      node scripts\probe_char_swap_ui.js "--url=$BaseUrl/favorites" "--scenario=favorites-smoke" "--expected-app-version=$AppJsVersion"
    } finally {
      if ($null -eq $previousEntryVersions) {
        Remove-Item Env:EXPECTED_ENTRY_VERSIONS -ErrorAction SilentlyContinue
      } else {
        $env:EXPECTED_ENTRY_VERSIONS = $previousEntryVersions
      }
    }
  }
  Run-Step "Generated gallery prompt probe" {
    node scripts\probe_char_swap_ui.js "--url=$BaseUrl/generated?g=145618559" "--scenario=generated-prompts"
  }
}

Write-Host "All regression guards passed."
