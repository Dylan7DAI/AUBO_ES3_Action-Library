#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
conda_env_name="aubo"
conda_spec_path="${project_root}/environment.yml"
wheel_path="${project_root}/vendor/pyaubo_sdk/pyaubo_sdk-0.24.1-cp310-cp310-manylinux2014_x86_64.whl"

if [[ "$(uname -s)" != "Linux" ]] || [[ "$(uname -m)" != "x86_64" ]]; then
  echo "This real-robot deployment bundle requires Linux x86-64." >&2
  exit 1
fi

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]] || [[ "${VERSION_ID:-}" != "20.04" ]]; then
    echo "Expected Ubuntu 20.04; found ${PRETTY_NAME:-unknown}." >&2
    exit 1
  fi
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "Conda is required. Install Miniconda or Anaconda, reopen the shell, and rerun this script." >&2
  exit 1
fi

if [[ ! -f "${wheel_path}" ]]; then
  echo "Missing vendored pyaubo-sdk wheel. Run scripts/download_pyaubo_linux.sh." >&2
  exit 1
fi

(cd "${project_root}/vendor/pyaubo_sdk" && sha256sum --check SHA256SUMS)
if conda run --name "${conda_env_name}" python -c 'import sys' >/dev/null 2>&1; then
  conda env update --name "${conda_env_name}" --file "${conda_spec_path}" --prune
else
  conda env create --name "${conda_env_name}" --file "${conda_spec_path}" --yes
fi

python_version="$(conda run --name "${conda_env_name}" python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${python_version}" != "3.10" ]]; then
  echo "The Conda environment requires CPython 3.10; found ${python_version}." >&2
  exit 1
fi

conda run --name "${conda_env_name}" python -m pip install --upgrade pip
conda run --name "${conda_env_name}" python -m pip install "${wheel_path}"
conda run --name "${conda_env_name}" python -m pip install -r "${project_root}/requirements.txt"
conda run --name "${conda_env_name}" python "${project_root}/scripts/verify_install.py"

echo
echo "Ubuntu 20.04 Conda environment ready: ${conda_env_name}"
echo "Activate it with: conda activate ${conda_env_name}"
echo "Copy config/robot.example.json and config/study.example.json to *.local.json before real use."
