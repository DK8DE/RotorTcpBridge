# Installiert den Pre-Push-Hook (Firmware-Pins vor Push synchronisieren).
$ErrorActionPreference = "Stop"
$root = git rev-parse --show-toplevel
if (-not $root) { throw "Kein Git-Repo." }
$src = Join-Path $root "scripts\pre-push-sync-fw-versions.sh"
$dst = Join-Path $root ".git\hooks\pre-push"
if (-not (Test-Path $src)) { throw "Hook-Vorlage fehlt: $src" }
Copy-Item -Force $src $dst
# Unter Git for Windows reicht die .sh-Datei als Hook (bash).
Write-Host "Installiert: $dst"
Write-Host "Beim nächsten 'git push' werden FW-Versionen von GitHub geprüft."
