#!/usr/bin/env bash
# Install ResearchCouncil's local runtime and build the browser bundle.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/_common.sh"

road2m_build_frontend=1
for argument in "$@"; do
  case "${argument}" in
    --skip-build)
      road2m_build_frontend=0
      ;;
    --help|-h)
      cat <<'HELP'
Usage: ./scripts/install.sh [--skip-build]

Creates the local Python environment, installs the backend dependencies,
installs the locked frontend dependencies when a lockfile is present, and
builds frontend/dist.  --skip-build installs dependencies without building.
HELP
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "${argument}" >&2
      exit 2
      ;;
  esac
done

printf '%s\n' 'ResearchCouncil local setup'
printf '%s\n' "Project: ${ROAD2M_ROOT}"

system_python="$(road2m_select_python)"
python_version="$(road2m_check_python_version "${system_python}")"
printf '%s\n' "Python: ${python_version} (${system_python})"

venv_dir="${ROAD2M_ROOT}/.venv"
if [[ ! -x "${venv_dir}/bin/python" ]]; then
  "${system_python}" -m venv "${venv_dir}"
fi
venv_python="${venv_dir}/bin/python"
venv_version="$(road2m_check_python_version "${venv_python}")"
printf '%s\n' "Virtual environment: ${venv_version}"

export PIP_DISABLE_PIP_VERSION_CHECK=1
requirements_file=''
for candidate in \
  "${ROAD2M_ROOT}/backend/requirements.lock.txt" \
  "${ROAD2M_ROOT}/backend/requirements.txt" \
  "${ROAD2M_ROOT}/requirements.lock.txt" \
  "${ROAD2M_ROOT}/requirements.txt"; do
  if [[ -f "${candidate}" ]]; then
    requirements_file="${candidate}"
    break
  fi
done

if [[ -n "${requirements_file}" ]]; then
  printf '%s\n' "Installing backend dependencies from ${requirements_file#${ROAD2M_ROOT}/}"
  "${venv_python}" -m pip install --disable-pip-version-check -r "${requirements_file}"
elif [[ -f "${ROAD2M_ROOT}/backend/pyproject.toml" ]]; then
  printf '%s\n' 'Installing backend package from backend/pyproject.toml'
  "${venv_python}" -m pip install --disable-pip-version-check -e "${ROAD2M_ROOT}/backend"
else
  # This fallback keeps a freshly checked-out tree diagnosable while the
  # backend dependency manifest is being added.  A committed manifest takes
  # precedence as soon as it exists.
  printf '%s\n' 'No backend dependency manifest found; installing the FastAPI baseline.'
  "${venv_python}" -m pip install --disable-pip-version-check \
    'fastapi>=0.115,<1' 'uvicorn[standard]>=0.30,<1' 'pydantic>=2.7,<3'
fi

if [[ ! -f "${ROAD2M_ROOT}/frontend/package.json" ]]; then
  printf '%s\n' 'frontend/package.json is missing.' >&2
  exit 1
fi

node_bin="$(road2m_select_node)"
node_version="$(road2m_check_node_version "${node_bin}")"
printf '%s\n' "Node.js: ${node_version} (${node_bin})"

# If the selected Node is a bundled runtime, put its bin directory first so
# npm/pnpm scripts use the same Node major version.
node_dir="$(dirname -- "${node_bin}")"
export PATH="${node_dir}:${PATH}"

run_npm=()
if command -v npm >/dev/null 2>&1; then
  run_npm=("$(command -v npm)")
else
  npm_cli="$(dirname -- "${node_bin}")/../lib/node_modules/npm/bin/npm-cli.js"
  if [[ -f "${npm_cli}" ]]; then
    run_npm=("${node_bin}" "${npm_cli}")
  fi
fi

if [[ ! -f "${ROAD2M_ROOT}/frontend/package-lock.json" \
   && ! -f "${ROAD2M_ROOT}/frontend/pnpm-lock.yaml" \
   && ! -f "${ROAD2M_ROOT}/frontend/yarn.lock" \
   && ${#run_npm[@]} -eq 0 ]]; then
  printf '%s\n' 'npm is unavailable for the frontend. Install npm with Node.js >=22.13.0.' >&2
  exit 1
fi

frontend_dir="${ROAD2M_ROOT}/frontend"
frontend_runner=()
if [[ -f "${frontend_dir}/pnpm-lock.yaml" ]]; then
  pnpm_bin=''
  pnpm_version=''
  if [[ -n "${ROAD2M_PNPM_BIN:-}" ]]; then
    # An explicit override is an operator choice: fail clearly if it is
    # missing or points at a different pnpm version instead of replacing it.
    pnpm_bin="$(road2m_select_pnpm)"
    pnpm_version="$(road2m_check_pnpm_version "${pnpm_bin}")"
  else
    discovered_pnpm="$(road2m_select_pnpm || true)"
    if [[ -n "${discovered_pnpm}" ]] && pnpm_version="$(road2m_check_pnpm_version "${discovered_pnpm}" 2>/dev/null)"; then
      pnpm_bin="${discovered_pnpm}"
    else
      printf '%s\n' 'No usable pnpm 11.19.0 found; installing it under .runtime/pnpm.'
    fi
  fi

  if [[ -z "${pnpm_bin}" ]]; then
    if [[ ${#run_npm[@]} -eq 0 ]]; then
      printf '%s\n' 'pnpm 11.19.0 is required, and npm is unavailable to install the local runtime.' >&2
      printf '%s\n' 'Install Node.js >=22.13.0 with npm, then rerun setup.' >&2
      exit 1
    fi
    pnpm_prefix="${ROAD2M_ROOT}/.runtime/pnpm"
    mkdir -p "${pnpm_prefix}"
    printf '%s\n' "Installing pnpm 11.19.0 into ${pnpm_prefix} with npm"
    "${run_npm[@]}" install --prefix "${pnpm_prefix}" \
      --no-audit --no-fund --ignore-scripts --no-save pnpm@11.19.0
    pnpm_bin="${pnpm_prefix}/node_modules/.bin/pnpm"
    pnpm_version="$(road2m_check_pnpm_version "${pnpm_bin}")"
  fi

  if [[ -n "${pnpm_bin}" ]]; then
    printf '%s\n' "pnpm: ${pnpm_version} (${pnpm_bin})"
    printf '%s\n' 'Installing frontend dependencies with pnpm (frozen lockfile)'
    (cd "${frontend_dir}" && "${pnpm_bin}" install --frozen-lockfile)
    frontend_runner=("${pnpm_bin}")
  fi
elif [[ -f "${frontend_dir}/package-lock.json" ]]; then
  if [[ ${#run_npm[@]} -eq 0 ]]; then
    printf '%s\n' 'frontend/package-lock.json is present but npm is unavailable.' >&2
    exit 1
  fi
  printf '%s\n' 'Installing frontend dependencies with npm ci (locked)'
  (cd "${frontend_dir}" && "${run_npm[@]}" ci --no-audit --no-fund)
  frontend_runner=("${run_npm[@]}")
elif [[ -f "${frontend_dir}/yarn.lock" ]]; then
  if ! command -v yarn >/dev/null 2>&1; then
    printf '%s\n' 'frontend/yarn.lock is present but yarn is unavailable.' >&2
    exit 1
  fi
  printf '%s\n' 'Installing frontend dependencies with yarn (frozen lockfile)'
  (cd "${frontend_dir}" && yarn install --frozen-lockfile)
  frontend_runner=(yarn)
else
  printf '%s\n' 'No frontend lockfile found; installing with npm and recording the lockfile locally.'
  (cd "${frontend_dir}" && "${run_npm[@]}" install --no-audit --no-fund)
  frontend_runner=("${run_npm[@]}")
fi

if [[ "${road2m_build_frontend}" -eq 1 ]]; then
  printf '%s\n' 'Building frontend/dist'
  if [[ ${#frontend_runner[@]} -eq 0 ]]; then
    printf '%s\n' 'No frontend package manager is available for the build.' >&2
    exit 1
  fi
  (cd "${frontend_dir}" && "${frontend_runner[@]}" run build)
else
  printf '%s\n' 'Skipping frontend build (--skip-build)'
fi

road2m_prepare_data_dirs

codex_bin="${ROAD2M_CODEX_BINARY:-}"
if [[ -z "${codex_bin}" ]] && command -v codex >/dev/null 2>&1; then
  codex_bin="$(command -v codex)"
fi
if [[ -n "${codex_bin}" && -x "${codex_bin}" ]]; then
  printf '%s\n' "Codex CLI: ${codex_bin}"
  printf '%s\n' 'Sign in when ready with: codex login (choose ChatGPT, not an API key).'
else
  printf '%s\n' 'Codex CLI: not found (saved local records remain available).'
  printf '%s\n' 'Install the Codex CLI, then run: codex login (choose ChatGPT).'
fi

printf '%s\n' ''
printf '%s\n' 'Setup complete. Start the local app with ./start.command or ./scripts/start.sh'
