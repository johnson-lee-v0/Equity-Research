#!/usr/bin/env bash
# Validate and restore a private ResearchCouncil backup archive.

set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

python_bin="${ROAD2M_ROOT}/.venv/bin/python"
if [[ ! -x "${python_bin}" ]]; then
  python_bin="$(road2m_select_python)"
fi
road2m_check_python_version "${python_bin}" >/dev/null
exec "${python_bin}" "${ROAD2M_ROOT}/scripts/road2m_data.py" restore "$@"
