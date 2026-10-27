#!/usr/bin/env bash
# Fetches the Linux wheels NudeNet needs, for the python:3.11-slim image, into
# ./wheels. Run on a machine with internet; the server has none.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf wheels
pip download nudenet==3.4.2 -d wheels --only-binary=:all: \
    --python-version 3.11 --implementation cp --abi cp311 \
    --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_27_x86_64 --platform manylinux_2_28_x86_64
ls -la wheels
