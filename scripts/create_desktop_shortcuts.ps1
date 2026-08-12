[CmdletBinding()]
param(
    [string]$InstallRoot = $env:GALLERY_INSTALL_ROOT
)

# ASCII-only source: CJK strings are decoded from base64 so PowerShell 5.1
# (which reads .ps1 as ANSI/GBK) never sees raw UTF-8 bytes.
function Decode-B64([string]$Value) {
    return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Value))
}
$brand = Decode-B64 "TmFp5a2m6ZW/5bel5L2c5a6k"
$manualName = Decode-B64 "5L2/55So6K+05piOLnR4dA=="
$brandManual = Decode-B64 "TmFp5a2m6ZW/5bel5L2c5a6kIOS9v+eUqOivtOaYjg=="

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    throw "InstallRoot is required."
}
$root = (Resolve-Path -LiteralPath $InstallRoot).Path
$launcher = Join-Path $root "START_GALLERY.bat"
if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Gallery launcher is missing: $launcher"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path $desktop ($brand + ".lnk")))
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $root
$shortcut.Description = $brand
$favicon = Join-Path $root "web\favicon.ico"
if (Test-Path -LiteralPath $favicon) {
    $shortcut.IconLocation = $favicon
} else {
    $shortcut.IconLocation = (Join-Path $env:SystemRoot "System32\shell32.dll") + ",165"
}
$shortcut.Save()

$manual = Join-Path $root $manualName
if (Test-Path -LiteralPath $manual) {
    $manualShortcut = $shell.CreateShortcut((Join-Path $desktop ($brandManual + ".lnk")))
    $manualShortcut.TargetPath = "notepad.exe"
    $manualShortcut.Arguments = '"' + $manual + '"'
    $manualShortcut.WorkingDirectory = $root
    $manualShortcut.Description = $brandManual
    $manualShortcut.Save()
}
