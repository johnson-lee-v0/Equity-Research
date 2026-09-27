#!/usr/bin/env bash
# Finder-friendly setup command.  Double-click this file on macOS.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "${SCRIPT_DIR}/scripts/install.sh" "$@"
