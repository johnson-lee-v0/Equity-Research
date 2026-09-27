#!/usr/bin/env bash
# Create a clean, executable-preserving source archive without local state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_PARENT="$(dirname "${ROOT_DIR}")"
LOCAL_PROJECT_NAME="$(basename "${ROOT_DIR}")"
PUBLIC_PROJECT_NAME="${ROAD2M_PUBLIC_PROJECT_NAME:-ResearchCouncil}"

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/package-source.sh OUTPUT.zip [options]

Options:
  --project-name NAME             top-level archive directory (default: ResearchCouncil)
  --replace-release               permit replacing runtime/release/NAME-source.zip
  --allow-empty-private-inputs    permit a clean checkout with no local DB/config inputs

The output archive contains the source tree under NAME/. Runtime state, private
data, local credentials, generated caches, prototypes, and the original private
brief are excluded. The value-blind privacy gate scans the manifest, archive,
and reachable publication Git history before the archive is accepted.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

OUTPUT_INPUT=""
REPLACE_RELEASE=0
ALLOW_EMPTY_PRIVATE_INPUTS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --replace-release)
      REPLACE_RELEASE=1
      shift
      ;;
    --allow-empty-private-inputs)
      ALLOW_EMPTY_PRIVATE_INPUTS=1
      shift
      ;;
    --project-name)
      if [[ $# -lt 2 ]]; then
        usage
        exit 2
      fi
      PUBLIC_PROJECT_NAME="$2"
      shift 2
      ;;
    --project-name=*)
      PUBLIC_PROJECT_NAME="${1#*=}"
      shift
      ;;
    --help|-h)
      usage >&1
      exit 0
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      if [[ -n "${OUTPUT_INPUT}" ]]; then
        usage
        exit 2
      fi
      OUTPUT_INPUT="$1"
      shift
      ;;
  esac
done

if [[ -z "${OUTPUT_INPUT}" ]]; then
  usage
  exit 2
fi
if [[ ! "${PUBLIC_PROJECT_NAME}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ || "${PUBLIC_PROJECT_NAME}" == "." || "${PUBLIC_PROJECT_NAME}" == ".." ]]; then
  printf '%s\n' 'The public project name must be a single safe path component.' >&2
  exit 2
fi
RELEASE_ARCHIVE="${ROOT_DIR}/runtime/release/${PUBLIC_PROJECT_NAME}-source.zip"

if [[ "${OUTPUT_INPUT}" = /* ]]; then
  OUTPUT_PATH="${OUTPUT_INPUT}"
else
  OUTPUT_PATH="$(pwd)/${OUTPUT_INPUT}"
fi

mkdir -p "$(dirname "${OUTPUT_PATH}")"
OUTPUT_PATH="$(cd "$(dirname "${OUTPUT_PATH}")" && pwd)/$(basename "${OUTPUT_PATH}")"
if [[ "${OUTPUT_PATH}" == "${RELEASE_ARCHIVE}" && "${REPLACE_RELEASE}" != 1 ]]; then
  printf '%s\n' 'Refusing to replace the release archive without --replace-release.' >&2
  exit 2
fi

if ! command -v zip >/dev/null 2>&1; then
  printf '%s\n' 'The zip command is required to create a source archive.' >&2
  exit 1
fi

PYTHON_BIN="${ROAD2M_PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${ROOT_DIR}/.runtime/python/bin/python3" ]]; then
    PYTHON_BIN="${ROOT_DIR}/.runtime/python/bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    printf '%s\n' 'Python 3 is required for the release privacy gate.' >&2
    exit 1
  fi
fi

MANIFEST="$(mktemp "${TMPDIR:-/tmp}/road2m-source.XXXXXX")"
PACKAGE_MANIFEST="$(mktemp "${TMPDIR:-/tmp}/researchcouncil-source.XXXXXX")"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/researchcouncil-stage.XXXXXX")"
cleanup() {
  rm -f "${MANIFEST}" "${PACKAGE_MANIFEST}"
  rm -rf "${STAGE_DIR}"
}
trap cleanup EXIT

RELATIVE_OUTPUT=""
case "${OUTPUT_PATH}" in
  "${PROJECT_PARENT}/${LOCAL_PROJECT_NAME}/"*)
    RELATIVE_OUTPUT="${OUTPUT_PATH#${PROJECT_PARENT}/}"
    ;;
esac

# Build a source-only manifest from the local project. The staging copy below
# gives the public archive its selected top-level name without renaming local
# paths, environment variables, or runtime data directories.
(
  cd "${PROJECT_PARENT}"
  find "${LOCAL_PROJECT_NAME}" \
    \( -type d \( \
      -name .git -o \
      -name .venv -o \
      -name __pycache__ -o \
      -name .pytest_cache -o \
      -name .vite -o \
      -name .cache -o \
      -name .mypy_cache -o \
      -name .ruff_cache -o \
      -name .tox -o \
      -name .nox -o \
      -name .runtime -o \
      -name coverage -o \
      -name node_modules -o \
      -name dist -o \
      -name build -o \
      -name data -o \
      -name runtime -o \
      -name exp -o \
      -name tmp -o \
      -name backups -o \
      -name exports \
    \) -prune \) -o \
    \( -type f \
      ! -path "${LOCAL_PROJECT_NAME}/PRD.html" \
      ! -name .DS_Store \
      ! -name '*.pyc' \
      ! -name '*.pyo' \
      ! -name '*.log' \
      ! -name '*.sqlite3' \
      ! -name '*.sqlite3-*' \
      ! -name '*.sqlite' \
      ! -name '*.sqlite-*' \
      ! -name '*.db' \
      ! -name '*.db-*' \
      ! -name '*.wal' \
      ! -name '*.shm' \
      ! -name '*.tsbuildinfo' \
      ! -name '*.zip' \
      ! -name '*.tar' \
      ! -name '*.tar.gz' \
      ! -name '*.tgz' \
      ! -name '*.bak' \
      ! -name '.env' \
      ! -name '.env.*' \
      ! -name '.envrc' \
      ! -name 'praw.ini' \
      ! -name 'providers.json' \
      ! -name 'credentials.json' \
      ! -name 'private-data.local.*' \
      ! -path "${RELATIVE_OUTPUT}" \
      -print \
    \) \
  | LC_ALL=C sort
) >"${MANIFEST}"

# Keep the exclusion policy executable and reviewable if the tree gains a new
# local directory or private artifact before the next release.
if grep -E '(^|/)(\.git|\.venv|__pycache__|\.pytest_cache|\.vite|\.cache|\.mypy_cache|\.ruff_cache|\.tox|\.nox|\.runtime|coverage|node_modules|dist|build|data|runtime|exp|tmp|backups|exports)/|(^|/)PRD\.html$|(^|/)(\.DS_Store|.*\.(pyc|pyo|log|sqlite3(-.*)?|sqlite(-.*)?|db(-.*)?|wal|shm|tsbuildinfo|zip|tar|tar\.gz|tgz|bak)|\.env(rc|\..*)?|praw\.ini|providers\.json|credentials\.json|private-data.local\..*)$' "${MANIFEST}" >/dev/null; then
  printf '%s\n' 'Source manifest contains an excluded local artifact.' >&2
  exit 1
fi

mkdir -p "${STAGE_DIR}/${PUBLIC_PROJECT_NAME}"
while IFS= read -r source_entry; do
  case "${source_entry}" in
    "${LOCAL_PROJECT_NAME}/"*)
      relative_entry="${source_entry#${LOCAL_PROJECT_NAME}/}"
      ;;
    *)
      printf '%s\n' 'Source manifest contains an unexpected path.' >&2
      exit 1
      ;;
  esac
  source_path="${ROOT_DIR}/${relative_entry}"
  if [[ -L "${source_path}" || ! -f "${source_path}" ]]; then
    printf '%s\n' 'Source manifest contains a non-regular file.' >&2
    exit 1
  fi
  target_path="${STAGE_DIR}/${PUBLIC_PROJECT_NAME}/${relative_entry}"
  mkdir -p "$(dirname "${target_path}")"
  cp -p -- "${source_path}" "${target_path}"
done <"${MANIFEST}"

(
  cd "${STAGE_DIR}"
  find "${PUBLIC_PROJECT_NAME}" -type f -print | LC_ALL=C sort
) >"${PACKAGE_MANIFEST}"

rm -f "${OUTPUT_PATH}"
(
  cd "${STAGE_DIR}"
  # zip records Unix mode bits, retaining executable launch and script files.
  zip -X -q "${OUTPUT_PATH}" -@ <"${PACKAGE_MANIFEST}"
)

PRIVACY_ARGS=(
  --root "${STAGE_DIR}"
  --manifest "${PACKAGE_MANIFEST}"
  --archive "${OUTPUT_PATH}"
  --private-root "${ROOT_DIR}"
  --data-dir "${ROAD2M_DATA_DIR:-${ROOT_DIR}/data}"
  --git-root "${ROOT_DIR}"
)
if [[ "${ALLOW_EMPTY_PRIVATE_INPUTS}" == 1 ]]; then
  PRIVACY_ARGS+=(--allow-empty-private-inputs)
fi
if ! "${PYTHON_BIN}" "${ROOT_DIR}/scripts/release_privacy.py" "${PRIVACY_ARGS[@]}"; then
  rm -f "${OUTPUT_PATH}"
  printf '%s\n' 'Release privacy gate rejected the source archive.' >&2
  exit 1
fi

printf 'Created %s (%s files)\n' "${OUTPUT_PATH}" "$(wc -l <"${MANIFEST}" | tr -d ' ')"
