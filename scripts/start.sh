#!/usr/bin/env bash
# Run the local FastAPI application that serves frontend/dist.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
Usage: ./scripts/start.sh

Starts FastAPI on http://127.0.0.1:8000.  Set ROAD2M_PORT to use another
loopback port, or ROAD2M_OPEN_BROWSER=0 to skip opening the browser on macOS.
Shutdown allows up to five seconds for active SSE/client cleanup. The
application must be stopped before running a restore.
HELP
  exit 0
fi

venv_python="${ROAD2M_ROOT}/.venv/bin/python"
if [[ ! -x "${venv_python}" ]]; then
  printf '%s\n' 'ResearchCouncil is not installed yet. Run ./install.command first.' >&2
  exit 1
fi
road2m_check_python_version "${venv_python}" >/dev/null

frontend_index="${ROAD2M_ROOT}/frontend/dist/index.html"
if [[ ! -f "${frontend_index}" ]]; then
  printf '%s\n' 'frontend/dist/index.html is missing. Run ./install.command to build it.' >&2
  exit 1
fi

port="${ROAD2M_PORT:-8000}"
if [[ ! "${port}" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  printf '%s\n' "Invalid ROAD2M_PORT: ${port}" >&2
  exit 2
fi

data_dir="${ROAD2M_DATA_DIR:-${ROAD2M_ROOT}/data}"
export ROAD2M_DATA_DIR="${data_dir}"
export ROAD2M_PROJECT_ROOT="${ROAD2M_ROOT}"
export PYTHONPATH="${ROAD2M_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"
road2m_prepare_data_dirs
road2m_clear_paid_provider_environment

pid_file="${data_dir}/.road2m.pid"
if [[ -f "${pid_file}" ]]; then
  existing_pid="$(sed -n '1p' "${pid_file}" || true)"
  if [[ "${existing_pid}" =~ ^[0-9]+$ ]] && kill -0 "${existing_pid}" 2>/dev/null; then
    printf '%s\n' "ResearchCouncil is already running (process ${existing_pid}) at http://127.0.0.1:${port}" >&2
    exit 1
  fi
  rm -f "${pid_file}"
fi

app_module="${ROAD2M_APP_MODULE:-app.main:app}"
printf '%s\n' "Starting ResearchCouncil at http://127.0.0.1:${port}"
printf '%s\n' 'Press Ctrl-C in this window to stop the local server.'

server_pid=''
"${venv_python}" -m uvicorn "${app_module}" \
  --host 127.0.0.1 \
  --port "${port}" \
  --timeout-graceful-shutdown 5 \
  --app-dir "${ROAD2M_ROOT}/backend" &
server_pid=$!
printf '%s\n' "${server_pid}" > "${pid_file}"
chmod 600 "${pid_file}"

road2m_cleanup() {
  local status=$?
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  rm -f "${pid_file}"
  trap - EXIT INT TERM HUP
  exit "${status}"
}
road2m_stop() {
  if [[ -n "${server_pid}" ]] && kill -0 "${server_pid}" 2>/dev/null; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  exit 130
}
trap road2m_cleanup EXIT
trap road2m_stop INT TERM HUP

server_ready=0
for attempt in $(seq 1 80); do
  if curl --silent --show-error --fail "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    server_ready=1
    break
  fi
  if ! kill -0 "${server_pid}" 2>/dev/null; then
    wait "${server_pid}" || true
    printf '%s\n' 'ResearchCouncil stopped during startup. Check the error above.' >&2
    exit 1
  fi
  sleep 0.25
done

if [[ "${server_ready}" -eq 0 ]]; then
  printf '%s\n' 'ResearchCouncil did not answer /api/health within 20 seconds.' >&2
  kill "${server_pid}" 2>/dev/null || true
  wait "${server_pid}" 2>/dev/null || true
  exit 1
fi

if [[ "${ROAD2M_OPEN_BROWSER:-1}" != "0" && "${OSTYPE:-}" == darwin* ]] && command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:${port}" >/dev/null 2>&1 || true
fi

wait "${server_pid}"
