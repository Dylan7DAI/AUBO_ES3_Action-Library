# Vendored AUBO Python SDK

This directory contains the official stable Linux x86-64 wheel for the Ubuntu
20.04 deployment's Conda environment (CPython 3.10):

`pyaubo_sdk-0.24.1-cp310-cp310-manylinux2014_x86_64.whl`

It was downloaded from the AUBO-maintained PyPI project. Verify it before
installation:

```bash
cd vendor/pyaubo_sdk
sha256sum --check SHA256SUMS
```

The `manylinux2014` wheel is compatible with Ubuntu 20.04's glibc 2.31. It cannot
load on macOS, Windows, ARM64, or a non-CPython-3.10 runtime.
Do not rename it to bypass those compatibility tags.
