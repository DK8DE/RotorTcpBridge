<#
.SYNOPSIS
    RotorTcpBridge Build-Skript – PyInstaller + Inno Setup

.DESCRIPTION
    Optionale Parameter:
      -Version  "x.y"   Neue Versionsnummer setzen (z.B. "1.2").
                         Ohne Angabe wird die aktuelle Version aus version.py verwendet.
      -SkipInstaller     Nur PyInstaller, kein Inno-Setup-Lauf.

.EXAMPLE
    .\build.ps1                      # Build mit aktueller Version
    .\build.ps1 -Version "1.3"       # Version auf 1.3 setzen und bauen
    .\build.ps1 -Version "1.3" -SkipInstaller
#>
param(
    [string]$Version       = "",
    [switch]$SkipInstaller = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$VerFile    = Join-Path $ProjectDir "rotortcpbridge\version.py"

# ── Aktuelle Version aus version.py lesen ──────────────────────────────────
$content    = Get-Content $VerFile -Raw
$match      = [regex]::Match($content, 'APP_VERSION\s*=\s*"([^"]+)"')
if (-not $match.Success) { throw "APP_VERSION nicht in $VerFile gefunden." }
$CurrentVer = $match.Groups[1].Value

if ($Version -eq "") {
    $Version = $CurrentVer
    Write-Host "Version: $Version  (unveraendert)" -ForegroundColor Cyan
} else {
    # Versionsnummer und Datum in version.py aktualisieren
    $today      = (Get-Date).ToString("dd.MM.yyyy")
    $newContent = $content `
        -replace '(APP_VERSION\s*=\s*)"[^"]+"',  "`$1`"$Version`"" `
        -replace '(APP_DATE\s*=\s*)"[^"]+"',      "`$1`"$today`""
    Set-Content $VerFile $newContent -Encoding UTF8
    Write-Host "Version gesetzt: $CurrentVer  ->  $Version  ($today)" -ForegroundColor Green
}

# ── PyInstaller ────────────────────────────────────────────────────────────
Write-Host "`nStarte PyInstaller..." -ForegroundColor Cyan
$pyinstaller = Join-Path $ProjectDir ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $pyinstaller)) { $pyinstaller = "pyinstaller" }

& $pyinstaller --noconfirm --clean (Join-Path $ProjectDir "RotorTcpBridge.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen (Exit $LASTEXITCODE)." }

# ── Inno Setup ────────────────────────────────────────────────────────────
if (-not $SkipInstaller) {
    Write-Host "`nStarte Inno Setup..." -ForegroundColor Cyan

    $isccPaths = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
    )
    $iscc = $isccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        $iscc = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    }
    if (-not $iscc) { throw "ISCC.exe nicht gefunden. Inno Setup installiert?" }

    & $iscc /DMyAppVersion=$Version (Join-Path $ProjectDir "Installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup fehlgeschlagen (Exit $LASTEXITCODE)." }

    $installer = Join-Path $ProjectDir "dist\installer\RotorTcpBridge-Setup-$Version.exe"
    Write-Host "`nFertig: $installer" -ForegroundColor Green
} else {
    Write-Host "`nFertig (ohne Installer)." -ForegroundColor Green
}
