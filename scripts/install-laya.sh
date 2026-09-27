#!/bin/sh
# Install the optional Laya runtime and the pinned English checkpoint.
#
# The normal backend never runs this script implicitly.  It creates an ignored
# .runtime/laya environment, downloads only the five approved checkpoint files,
# and records hashes so the worker can refuse an altered or unpinned model.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
if [ -n "${LAYA_PYTHON:-}" ]; then
    PYTHON="$LAYA_PYTHON"
elif [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    # The main setup creates a supported interpreter here.  Prefer it over a
    # macOS system python3 that may still be 3.9.
    PYTHON="$PROJECT_ROOT/.venv/bin/python"
else
    PYTHON=python3
fi
RUNTIME_DIR="$PROJECT_ROOT/.runtime/laya"
VENV_DIR="$RUNTIME_DIR/venv"
MODEL_DIR="$RUNTIME_DIR/model"
REQUIREMENTS="$PROJECT_ROOT/backend/requirements-laya.txt"

MODEL_ID="convaiinnovations/laya"
MODEL_REVISION="1c5edc17a7acd8701df6fc341c0d179f1c62c982"
SOURCE_REVISION="573e5b62696ba441230cd6be71d593331b5d23af"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Laya installation unavailable: Python executable was not found." >&2
    exit 2
fi

"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Laya requires Python 3.10 or newer.")
PY

mkdir -p "$RUNTIME_DIR"
if [ ! -x "$VENV_DIR/bin/python" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi

# Credentials are deliberately removed from this process.  The checkpoint is
# public and the worker is offline, so no token is needed at install or runtime.
env -u HF_TOKEN -u HUGGINGFACE_HUB_TOKEN -u HUGGINGFACE_TOKEN -u HF_ACCESS_TOKEN -u HF_API_TOKEN \
    "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --upgrade --requirement "$REQUIREMENTS"

MODEL_ID="$MODEL_ID" MODEL_REVISION="$MODEL_REVISION" SOURCE_REVISION="$SOURCE_REVISION" \
MODEL_DIR="$MODEL_DIR" env -u HF_TOKEN -u HUGGINGFACE_HUB_TOKEN -u HUGGINGFACE_TOKEN -u HF_ACCESS_TOKEN -u HF_API_TOKEN \
    "$VENV_DIR/bin/python" - <<'PY'
import hashlib
import json
import os
import shutil
from pathlib import Path

from huggingface_hub import snapshot_download

model_id = os.environ["MODEL_ID"]
revision = os.environ["MODEL_REVISION"]
source_revision = os.environ["SOURCE_REVISION"]
target = Path(os.environ["MODEL_DIR"])
staging = target.with_name(target.name + ".staging")

allowed = (
    "rl_agent_config.json",
    "model.safetensors",
    "encoder/config.json",
    "tokenizer/tokenizer.json",
    "tokenizer/tokenizer_config.json",
)
expected_sha256 = {
    "rl_agent_config.json": "ae287b56bbcf5f8c4f4541ae9dfd00c914c4c48b940b8398c3058af37ba92bbd",
    "encoder/config.json": "bf3ab80598fdccf414855a2ce80f22859e4492d06ca8a62ddd1cfb63972f8979",
    "tokenizer/tokenizer.json": "6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30",
    "tokenizer/tokenizer_config.json": "50044de60daaa73df97d262e15a40d4faf0160e7d742df64b377877a1320dd12",
    "model.safetensors": "891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c",
}

if staging.exists():
    shutil.rmtree(staging)
staging.mkdir(parents=True)
snapshot_download(
    repo_id=model_id,
    revision=revision,
    token=False,
    allow_patterns=list(allowed),
    local_dir=str(staging),
)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

for relative, expected in expected_sha256.items():
    path = staging / relative
    if not path.is_file() or path.is_symlink() or sha256(path) != expected:
        raise SystemExit("Downloaded Laya checkpoint failed the pinned file check.")

manifest = {
    "model": model_id,
    "revision": revision,
    "source_revision": source_revision,
    "model_sha256": expected_sha256["model.safetensors"],
    "files": expected_sha256,
}
(staging / "laya_runtime_manifest.json").write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)

if target.exists():
    shutil.rmtree(target)
staging.replace(target)
print("Laya runtime installed with the pinned local English checkpoint.")
PY

echo "Laya installation complete. No cloud inference is enabled by this setup."
