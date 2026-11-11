#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

set -euo pipefail

VERSION="${1:?usage: build_release.sh <release_version> [python]}"
PYTHON="${2:-python3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/dist/${VERSION}"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

mkdir -p "${OUT}" "${WORK}/lambda" "${WORK}/training"

"${PYTHON}" -m pip install --quiet \
  --platform manylinux2014_aarch64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target "${WORK}/lambda" \
  "numpy==2.2.6" "pypdf==6.21.2"

rsync -a --exclude '__pycache__' --exclude '*.pyc' "${ROOT}/docclass" "${WORK}/lambda/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "${ROOT}/service" "${WORK}/lambda/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "${ROOT}/training" "${WORK}/lambda/"
rm -f "${OUT}/service.zip"
(cd "${WORK}/lambda" && find . -exec touch -t 202001010000 {} + && zip -qrX "${OUT}/service.zip" .)

rsync -a --exclude '__pycache__' --exclude '*.pyc' "${ROOT}/docclass" "${WORK}/training/"
cp "${ROOT}/service/training/ngram_entry.py" "${WORK}/training/ngram_entry.py"
echo "scikit-learn==1.7.2" > "${WORK}/training/requirements.txt"
tar -C "${WORK}/training" -czf "${OUT}/training-source.tar.gz" .

(cd "${OUT}" && shasum -a 256 service.zip training-source.tar.gz > SHA256SUMS)
echo "release ${VERSION} written to ${OUT}"
ls -l "${OUT}"
