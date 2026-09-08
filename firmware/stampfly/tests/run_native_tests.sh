#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BUILD=${TMPDIR:-/tmp}/camfly-cf1-native-$$
trap 'rm -rf "$BUILD"' EXIT INT TERM
mkdir -p "$BUILD"

c++ -std=c++17 -Wall -Wextra -Werror \
  -I"$ROOT/src" \
  "$ROOT/src/cf1_protocol.cpp" \
  "$ROOT/tests/test_cf1_protocol.cpp" \
  -o "$BUILD/test_cf1_protocol"
"$BUILD/test_cf1_protocol"
echo "CF1 native protocol tests passed"
