#!/usr/bin/env bash
# Shared, intentionally small helpers for the local ResearchCouncil commands.

set -euo pipefail

ROAD2M_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export ROAD2M_ROOT

road2m_user_home="${HOME:-}"

road2m_select_python() {
  local candidate
  local candidates=(
    "${ROAD2M_ROOT}/.runtime/python/bin/python3"
    "${road2m_user_home}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi

  printf '%s\n' "ResearchCouncil needs Python 3.11 or newer. Install Python and run ./install.command." >&2
  return 1
}

road2m_select_node() {
  local candidate
  local candidates=(
    "${ROAD2M_ROOT}/.runtime/node/bin/node"
    "${road2m_user_home}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  if command -v node >/dev/null 2>&1; then
    command -v node
    return 0
  fi

  printf '%s\n' "ResearchCouncil needs Node.js >=22.13.0. Install a supported Node.js version and run ./install.command." >&2
  return 1
}

road2m_select_pnpm() {
  local candidate
  local explicit="${ROAD2M_PNPM_BIN:-}"
  if [[ -n "${explicit}" ]]; then
    if [[ -x "${explicit}" ]]; then
      printf '%s\n' "${explicit}"
      return 0
    fi
    if command -v "${explicit}" >/dev/null 2>&1; then
      command -v "${explicit}"
      return 0
    fi
    printf '%s\n' "ROAD2M_PNPM_BIN does not point to an executable pnpm: ${explicit}" >&2
    return 2
  fi
  local candidates=(
    "${ROAD2M_ROOT}/.runtime/pnpm/node_modules/.bin/pnpm"
    "${ROAD2M_ROOT}/.runtime/pnpm/bin/pnpm"
    "${road2m_user_home}/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -n "${candidate}" && -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  if command -v pnpm >/dev/null 2>&1; then
    command -v pnpm
    return 0
  fi

  return 1
}

road2m_check_python_version() {
  local python_bin="$1"
  "${python_bin}" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit(
        f"ResearchCouncil needs Python 3.11 or newer; found {sys.version.split()[0]}"
    )
print(sys.version.split()[0])
PY
}

road2m_check_node_version() {
  local node_bin="$1"
  "${node_bin}" - <<'NODE'
const [major, minor] = process.versions.node.split('.').map(Number);
const supported = major > 22 || (major === 22 && minor >= 13);
if (!supported) {
  console.error(`ResearchCouncil needs Node.js >=22.13.0; found ${process.versions.node}`);
  process.exit(1);
}
console.log(process.versions.node);
NODE
}

road2m_check_pnpm_version() {
  local pnpm_bin="$1"
  local version
  version="$("${pnpm_bin}" --version)"
  if [[ "${version}" != "11.19.0" ]]; then
    printf '%s\n' "ResearchCouncil's frontend lockfile is installed with pnpm 11.19.0; found ${version}." >&2
    printf '%s\n' 'Install pnpm 11.19.0 or set ROAD2M_PNPM_BIN to that executable.' >&2
    return 1
  fi
  printf '%s\n' "${version}"
}

road2m_prepare_data_dirs() {
  local data_dir="${ROAD2M_DATA_DIR:-${ROAD2M_ROOT}/data}"
  mkdir -p "${data_dir}/evidence" "${data_dir}/backups" "${data_dir}/workers"
  chmod 700 "${data_dir}" "${data_dir}/evidence" "${data_dir}/backups" "${data_dir}/workers"
}

road2m_clear_paid_provider_environment() {
  # ResearchCouncil uses the authenticated Codex CLI subscription route.  These are
  # cleared in the child process so a shell-level API key cannot silently turn
  # a local run into paid API usage.
  unset OPENAI_API_KEY OPENAI_BASE_URL OPENAI_API_BASE OPENAI_ORG_ID
  unset AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT ANTHROPIC_API_KEY
  unset GOOGLE_API_KEY GEMINI_API_KEY OPENROUTER_API_KEY
  unset CODEX_API_KEY CODEX_ACCESS_TOKEN CODEX_API_BASE CODEX_ENDPOINT CODEX_PROVIDER
}
