# Fetches the third-party tools that get bundled into the installer:
#   bin\platform-tools\  (adb, from Google)
#   bin\tesseract\       (Tesseract OCR, copied from a system install)
# Safe to re-run: anything already present is left alone.
# Used by .github/workflows/release.yml and build_installer.bat.

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$bin  = Join-Path $root 'bin'
New-Item -ItemType Directory -Force $bin | Out-Null

# --- adb --------------------------------------------------------------------
if (-not (Test-Path (Join-Path $bin 'platform-tools\adb.exe'))) {
    Write-Host 'Downloading Android platform-tools...'
    $zip = Join-Path $env:TEMP 'platform-tools.zip'
    Invoke-WebRequest 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip' -OutFile $zip
    Expand-Archive $zip -DestinationPath $bin -Force   # zip's top-level folder is platform-tools\
    Remove-Item $zip
} else {
    Write-Host 'platform-tools already present, skipping.'
}

# --- Tesseract --------------------------------------------------------------
if (-not (Test-Path (Join-Path $bin 'tesseract\tesseract.exe'))) {
    $sys = 'C:\Program Files\Tesseract-OCR'
    if (-not (Test-Path (Join-Path $sys 'tesseract.exe'))) {
        Write-Host 'Installing Tesseract via Chocolatey...'
        choco install tesseract -y --no-progress
        if ($LASTEXITCODE -ne 0) { throw 'choco install tesseract failed' }
    }
    if (-not (Test-Path (Join-Path $sys 'tesseract.exe'))) { throw "Tesseract not found at $sys" }
    Write-Host 'Copying Tesseract into bin\tesseract...'
    Copy-Item $sys -Destination (Join-Path $bin 'tesseract') -Recurse
} else {
    Write-Host 'tesseract already present, skipping.'
}

Write-Host 'Bundled tools ready.'
