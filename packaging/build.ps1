# Build Windows onedir + zip + (if Inno Setup exists) installer.
# Uses an isolated venv so global site-packages (torch 等) 不会打进安装包。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$VersionLine = Select-String -Path (Join-Path $Root "core\appinfo.py") -Pattern 'APP_VERSION = "([^"]+)"'
if (-not $VersionLine) { throw "APP_VERSION not found" }
$Version = $VersionLine.Matches[0].Groups[1].Value
$DistName = "PDFSeamlessEditor"
$ZipName = "PDFSeamlessEditor-$Version-windows-x64.zip"
$SetupName = "PDFSeamlessEditor-$Version-windows-x64-setup.exe"

$Venv = Join-Path $Root ".packaging-venv"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "==> venv"
    python -m venv $Venv
}
Write-Host "==> deps"
& $Py -m pip install -U pip
& $Py -m pip install -e ".[pack]"

Write-Host "==> icon"
& $Py (Join-Path $PSScriptRoot "generate_icon.py")

Write-Host "==> pyinstaller"
& $Py -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "pdf_seamless_editor.spec")

$DistDir = Join-Path $Root "dist\$DistName"
if (-not (Test-Path (Join-Path $DistDir "$DistName.exe"))) {
    throw "missing dist exe"
}

Write-Host "==> zip"
$ZipPath = Join-Path $Root "dist\$ZipName"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
tar.exe -a -c -f $ZipPath -C (Join-Path $Root "dist") $DistName
Write-Host "zip $ZipPath"

$Iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($Iscc) {
    Write-Host "==> inno $Iscc"
    & $Iscc (Join-Path $PSScriptRoot "windows\installer.iss")
    Write-Host "setup dist\$SetupName"
} else {
    Write-Host "==> Inno Setup not found; zip only. Install from https://jrsoftware.org/isinfo.php"
}

Write-Host "done $Version"
