#!/usr/bin/env bash
# Run a disposable local launcher/backup/restore smoke check.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

venv_python="${ROAD2M_ROOT}/.venv/bin/python"
if [[ ! -x "${venv_python}" ]]; then
  printf '%s\n' 'Run ./install.command before the smoke check.' >&2
  exit 1
fi
if [[ ! -f "${ROAD2M_ROOT}/frontend/dist/index.html" ]]; then
  printf '%s\n' 'frontend/dist/index.html is missing; run ./install.command first.' >&2
  exit 1
fi

port="${ROAD2M_SMOKE_PORT:-18745}"
if [[ ! "${port}" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  printf '%s\n' "Invalid ROAD2M_SMOKE_PORT: ${port}" >&2
  exit 2
fi

smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/road2m-smoke.XXXXXX")"
data_dir="${smoke_root}/data"
restore_dir="${smoke_root}/restored"
mkdir -p "${data_dir}"

launcher_pid=''
cleanup() {
  if [[ -n "${launcher_pid}" ]] && kill -0 "${launcher_pid}" 2>/dev/null; then
    kill -TERM "${launcher_pid}" 2>/dev/null || true
    wait "${launcher_pid}" 2>/dev/null || true
  fi
  if [[ "${ROAD2M_SMOKE_KEEP:-0}" == "1" ]]; then
    printf '%s\n' "Smoke artifacts: ${smoke_root}"
  else
    rm -rf "${smoke_root}"
  fi
}
trap cleanup EXIT INT TERM HUP

ROAD2M_DATA_DIR="${data_dir}" \
ROAD2M_PORT="${port}" \
ROAD2M_OPEN_BROWSER=0 \
  "${ROAD2M_ROOT}/scripts/start.sh" >"${smoke_root}/start.log" 2>&1 &
launcher_pid=$!

ready=0
for ((attempt = 1; attempt <= 80; attempt += 1)); do
  if curl --silent --show-error --fail "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "${launcher_pid}" 2>/dev/null; then
    wait "${launcher_pid}" || true
    cat "${smoke_root}/start.log" >&2
    printf '%s\n' 'Launcher exited before the health check passed.' >&2
    exit 1
  fi
  sleep 0.25
done
if [[ "${ready}" -ne 1 ]]; then
  cat "${smoke_root}/start.log" >&2
  printf '%s\n' 'Launcher did not answer /api/health within 20 seconds.' >&2
  exit 1
fi

http_code="$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${port}/")"
if [[ "${http_code}" != "200" ]]; then
  printf '%s\n' "Expected the production frontend to return HTTP 200; received ${http_code}." >&2
  exit 1
fi

backup_path="${smoke_root}/road2m-smoke-backup.tar.gz"
ROAD2M_DATA_DIR="${data_dir}" "${ROAD2M_ROOT}/scripts/backup.sh" --output "${backup_path}" >/dev/null
ROAD2M_DATA_DIR="${data_dir}" "${ROAD2M_ROOT}/scripts/export.sh" \
  --namespace demo --format json --output "${smoke_root}/demo-export.json" >/dev/null
ROAD2M_DATA_DIR="${restore_dir}" "${ROAD2M_ROOT}/scripts/restore.sh" \
  "${backup_path}" --confirm-stopped >/dev/null

if [[ ! -f "${restore_dir}/road2m.sqlite3" || ! -d "${restore_dir}/evidence" ]]; then
  printf '%s\n' 'Restored database or evidence directory is missing.' >&2
  exit 1
fi
"${venv_python}" - "${smoke_root}/demo-export.json" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("format") != "road2m-export" or payload.get("namespace") != "demo":
    raise SystemExit("demo export envelope is invalid")
PY

kill -TERM "${launcher_pid}"
wait "${launcher_pid}" 2>/dev/null || true
launcher_pid=''
if [[ -e "${data_dir}/.road2m.pid" ]]; then
  printf '%s\n' 'Launcher left a PID marker after graceful shutdown.' >&2
  exit 1
fi
if curl --silent --show-error --fail "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
  printf '%s\n' 'Launcher still answers after shutdown.' >&2
  exit 1
fi

printf '%s\n' 'ResearchCouncil launcher/backup/restore smoke check passed.'
