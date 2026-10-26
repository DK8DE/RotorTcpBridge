#!/usr/bin/env bash
# Pre-Push-Hook: Firmware-Pins aus GitHub nachziehen.
# Installation (einmalig):
#   cp scripts/pre-push-sync-fw-versions.sh .git/hooks/pre-push
#   chmod +x .git/hooks/pre-push
#
# Unter Windows (Git Bash) ebenfalls nutzbar. Für PowerShell siehe
# scripts/install_pre_push_fw_sync.ps1

set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

python scripts/sync_fw_versions_from_github.py
if ! git diff --quiet -- rotortcpbridge/version.py; then
  echo ""
  echo "version.py wurde aktualisiert (neuere Firmware auf GitHub)."
  echo "Bitte committen und den Push erneut ausführen:"
  echo "  git add rotortcpbridge/version.py"
  echo "  git commit -m \"chore: sync firmware pins from GitHub\""
  echo "  git push"
  exit 1
fi
exit 0
