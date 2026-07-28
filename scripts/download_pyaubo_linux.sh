#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
destination="${project_root}/vendor/pyaubo_sdk"
wheel="${destination}/pyaubo_sdk-0.24.1-cp310-cp310-manylinux2014_x86_64.whl"
expected="35aec6843ea0c46807a6c29c91e3ebf039be306d31b5f25f0995f55f8c6f18bd"

mkdir -p "${destination}"
python3 -m pip download pyaubo-sdk==0.24.1 \
  --only-binary=:all: \
  --no-deps \
  --platform manylinux2014_x86_64 \
  --python-version 310 \
  --implementation cp \
  --abi cp310 \
  --dest "${destination}"

actual="$(sha256sum "${wheel}" | cut -d ' ' -f 1)"
if [[ "${actual}" != "${expected}" ]]; then
  echo "Checksum mismatch for ${wheel}" >&2
  exit 1
fi
echo "Verified ${wheel}"

