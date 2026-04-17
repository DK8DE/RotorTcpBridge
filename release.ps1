<#
.SYNOPSIS
    Liest APP_VERSION aus rotortcpbridge/version.py und triggert per Git-Tag einen GitHub-Actions-Release-Build.

.DESCRIPTION
    Der Workflow (.github/workflows/build-windows.yml) startet den Release-Job nur bei einem Push eines Tags,
    der mit "v" beginnt (z. B. v1.8). Das Skript erzeugt den Tag v<APP_VERSION> und führt "git push origin <Tag>" aus.

.PARAMETER Remote
    Git-Remote-Name (Standard: origin).

.PARAMETER DryRun
    Zeigt nur Version, Tag und die geplanten Befehle — führt kein git tag / git push aus.

.PARAMETER Force
    Bei schmutzigem Arbeitsverzeichnis keine Rückfrage (für Skripte/CI).

.EXAMPLE
    .\release.ps1

.EXAMPLE
    .\release.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string] $Remote = "origin",
    [switch] $DryRun,
    [switch] $Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$VersionFile = Join-Path $ProjectRoot "rotortcpbridge\version.py"
if (-not (Test-Path $VersionFile)) {
    throw "version.py nicht gefunden: $VersionFile"
}

$content = Get-Content -Path $VersionFile -Raw -Encoding UTF8
if ($content -notmatch 'APP_VERSION\s*=\s*"([^"]+)"') {
    throw "APP_VERSION in version.py konnte nicht gelesen werden."
}
$appVersion = $Matches[1].Trim()
if ($appVersion -eq "") {
    throw "APP_VERSION ist leer."
}

# Workflow: tags 'v*' und refs/tags/v — konsistent mit build-windows.yml (Tag ohne v in Inno, mit v im Git-Tag)
$tag = "v$appVersion"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path (Join-Path $ProjectRoot ".git"))) {
        throw "Kein Git-Repository im Projektroot: $ProjectRoot"
    }

    $dirty = (git status --porcelain 2>$null)
    if ($dirty -and -not $Force -and -not $DryRun) {
        Write-Warning "Arbeitsverzeichnis ist nicht leer (uncommittete Änderungen). Der Tag zeigt nur auf den letzten Commit — nicht auf ungespeicherte Dateien."
        $null = Read-Host "Enter zum Fortfahren oder Strg+C zum Abbrechen"
    }
    elseif ($dirty -and $DryRun) {
        Write-Warning "Arbeitsverzeichnis ist nicht leer — vor echtem Release committen, sonst fehlen Änderungen im Tag."
    }

    $head = (git rev-parse --short HEAD 2>$null)
    Write-Host "Projekt:    $ProjectRoot" -ForegroundColor Cyan
    Write-Host "APP_VERSION: $appVersion" -ForegroundColor Cyan
    Write-Host "Git-Tag:    $tag  (HEAD: $head)" -ForegroundColor Cyan
    Write-Host "Remote:     $Remote" -ForegroundColor Cyan
    Write-Host ""

    $existingLocal = git tag -l $tag 2>$null
    if ($existingLocal) {
        $tip = git rev-parse "$tag^{}" 2>$null
        if ($tip -eq (git rev-parse HEAD 2>$null)) {
            Write-Host "Tag existiert lokal bereits und zeigt auf HEAD — nur Push nötig." -ForegroundColor Yellow
        }
        else {
            throw "Tag $tag existiert lokal auf einem anderen Commit. Entfernen mit: git tag -d $tag`nOder Version in version.py erhöhen."
        }
    }

    $remoteRef = "refs/tags/$tag"
    $onRemote = git ls-remote --tags $Remote $tag 2>$null
    if ($onRemote) {
        throw "Tag $tag existiert bereits auf $Remote. Für ein neues Release: version.py anheben oder Remote-Tag löschen (git push $Remote --delete $tag)."
    }

    if ($DryRun) {
        Write-Host "[DryRun] Geplante Befehle:" -ForegroundColor Magenta
        if (-not $existingLocal) {
            Write-Host "  git tag $tag" -ForegroundColor Magenta
        }
        Write-Host "  git push $Remote $tag" -ForegroundColor Magenta
        Write-Host "`nKeine Änderungen ausgeführt." -ForegroundColor Magenta
        return
    }

    if (-not $existingLocal) {
        Write-Host "git tag $tag ..." -ForegroundColor Green
        git tag $tag
        if ($LASTEXITCODE -ne 0) { throw "git tag fehlgeschlagen (Exit $LASTEXITCODE)." }
    }

    Write-Host "git push $Remote $tag ..." -ForegroundColor Green
    git push $Remote $tag
    if ($LASTEXITCODE -ne 0) { throw "git push fehlgeschlagen (Exit $LASTEXITCODE)." }

    Write-Host "`nFertig. GitHub Actions sollte jetzt den Release-Workflow starten (ZIP + Inno Setup)." -ForegroundColor Green
    Write-Host "Auf GitHub: Repository öffnen → Tab „Actions“, danach „Releases“ prüfen." -ForegroundColor Green
}
finally {
    Pop-Location
}
