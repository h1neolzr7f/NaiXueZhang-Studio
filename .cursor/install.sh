#!/usr/bin/env bash
# Cloud Agent bootstrap for Nai学长工作室 / Pixiv NAI Gallery.
#
# The project is a Windows-first, local-only FastAPI service that the README
# and the pinned lock file target at Python 3.13 (requirements.lock.txt pins
# audioop-lts, which only ships wheels for Python >= 3.13). Ubuntu 24.04 ships
# Python 3.12 by default, so we provision 3.13 from deadsnakes, then build the
# project virtualenv and install the reproducible locked dependency set.
#
# This script is idempotent: it re-runs cleanly against a cached snapshot and
# only performs work that is still missing.
set -euo pipefail

# Always operate from the repository root, regardless of the caller's CWD.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1. Ensure a Python 3.13 interpreter exists (default noble interpreter is 3.12).
if ! command -v python3.13 >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -qq
  sudo apt-get install -y -qq software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3.13 python3.13-venv python3.13-dev
fi

# 2. Create the project virtualenv if it does not already exist.
if [ ! -x ".venv/bin/python" ]; then
  python3.13 -m venv .venv
fi

# 3. Install the reproducible, fully-pinned dependency set. Reusing the same
#    lock the project ships keeps the server and full pytest suite runnable.
#    pip skips packages that are already satisfied, so re-runs are fast.
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock.txt

echo "Nai学长工作室 environment ready: $(.venv/bin/python --version)"
