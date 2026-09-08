#!/bin/bash

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=${CAMFLY_SD_ROOT_DIR:-$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)}
SOURCE_DIR=${CAMFLY_SD_SOURCE_DIR:-$ROOT_DIR/vendor/atomcam_tools}
PATCH_DIR=${CAMFLY_SD_PATCH_DIR:-$SCRIPT_DIR/patches}
SOURCE_PATCH="$PATCH_DIR/0001-source-read-only.patch"
KERNEL_PATCH="$PATCH_DIR/0002-kernel-jz-sfc-read-only.patch"

EXPECTED_SOURCE_COMMIT=${CAMFLY_SD_EXPECTED_SOURCE_COMMIT:-313048b4d652b0058271ffa42de1289e9d5d08ee}
LIMA_HOME=${LIMA_HOME:-$ROOT_DIR/.lima-home}
LIMACTL=${LIMACTL:-/opt/homebrew/bin/limactl}
LIMA_INSTANCE=${LIMA_INSTANCE:-lima-docker}
BUILDER_CONTAINER=${BUILDER_CONTAINER:-atomcam_tools-builder-1}
DOCKER_HOST_ADDR=${DOCKER_HOST_ADDR:-tcp://127.0.0.1:2375}

OUTPUT_DIR=${CAMFLY_SD_OUTPUT_DIR:-$ROOT_DIR/artifacts}
RELEASE_ID=${CAMFLY_SD_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
RELEASE_DIR=${CAMFLY_SD_RELEASE_DIR:-$OUTPUT_DIR/$RELEASE_ID}
OUTPUT_FILE=${CAMFLY_SD_OUTPUT:-$RELEASE_DIR/atomcam_tools-sd-ro-$EXPECTED_SOURCE_COMMIT.zip}
BUILD_LOG=${CAMFLY_SD_LOG:-$RELEASE_DIR/build.log}
MANIFEST_FILE=${CAMFLY_SD_MANIFEST:-$RELEASE_DIR/manifest.json}
STAGING_PATCH="$SOURCE_DIR/patches/kernel/zz-camfly-sd-read-only.patch"
STAGING_ARCHIVE="$SOURCE_DIR/atomcam_tools-sd-ro.zip"
BUILD_LOCK="$SOURCE_DIR/.camfly-sd-build.lock"
VERIFY_SCRIPT=${CAMFLY_SD_VERIFY_SCRIPT:-$ROOT_DIR/atomcam-sd/verify_release.py}

die() {
  echo "camfly SD build: $*" >&2
  exit 1
}

[ -x "$LIMACTL" ] || die "limactl not found at $LIMACTL"
[ -f "$SOURCE_PATCH" ] || die "missing source patch: $SOURCE_PATCH"
[ -f "$KERNEL_PATCH" ] || die "missing kernel patch: $KERNEL_PATCH"
[ -e "$SOURCE_DIR/.git" ] || die "atomcam_tools submodule is unavailable"

actual_source_commit=$(git -C "$SOURCE_DIR" rev-parse HEAD)
[ "$actual_source_commit" = "$EXPECTED_SOURCE_COMMIT" ] || \
  die "atomcam_tools is at $actual_source_commit; expected $EXPECTED_SOURCE_COMMIT"

[ -z "$(git -C "$SOURCE_DIR" status --short)" ] || \
  die "atomcam_tools has local changes; clean it before building"

validate_output_path() {
  python3 - "$OUTPUT_DIR" "$1" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
target = pathlib.Path(sys.argv[2])
if target.exists() and target.is_symlink():
    raise SystemExit("symlink output path is not allowed")
resolved = target.parent.resolve() / target.name
if root != resolved and root not in resolved.parents:
    raise SystemExit("output path is outside artifacts")
PY
}

case "$RELEASE_ID" in
  ''|.*|*[!A-Za-z0-9._-]*) die "CAMFLY_SD_RELEASE_ID contains unsupported characters" ;;
esac
validate_output_path "$RELEASE_DIR" || die "release directory must be inside $OUTPUT_DIR"
validate_output_path "$OUTPUT_FILE" || die "CAMFLY_SD_OUTPUT must be inside $OUTPUT_DIR"
validate_output_path "$BUILD_LOG" || die "CAMFLY_SD_LOG must be inside $OUTPUT_DIR"
validate_output_path "$MANIFEST_FILE" || die "CAMFLY_SD_MANIFEST must be inside $OUTPUT_DIR"
[ ! -e "$RELEASE_DIR" ] || die "refusing to reuse existing release directory: $RELEASE_DIR"

[ ! -e "$BUILD_LOG" ] || die "refusing to overwrite existing build log: $BUILD_LOG"
[ ! -e "$MANIFEST_FILE" ] || die "refusing to overwrite existing manifest: $MANIFEST_FILE"
[ ! -e "$OUTPUT_FILE" ] || die "refusing to overwrite existing output: $OUTPUT_FILE"
[ ! -e "$STAGING_PATCH" ] || die "refusing to overwrite staging patch: $STAGING_PATCH"
[ ! -e "$STAGING_ARCHIVE" ] || die "refusing to overwrite staging archive: $STAGING_ARCHIVE"

mkdir -p "$OUTPUT_DIR"
[ ! -e "$BUILD_LOCK" ] || die "another SD build is already using $SOURCE_DIR"
mkdir "$BUILD_LOCK" || die "could not acquire build lock: $SOURCE_DIR"

source_patched=0
kernel_patch_staged=0
staging_archive_created=0
EVIDENCE_DIR=""
release_dir_created=0
build_log_created=0
output_created=0
manifest_created=0
lock_acquired=1

cleanup() {
  rc=$?
  trap - EXIT

  if [ "$staging_archive_created" -eq 1 ]; then
    rm -f "$STAGING_ARCHIVE"
  fi
  if [ "$kernel_patch_staged" -eq 1 ]; then
    rm -f "$STAGING_PATCH"
  fi
  if [ "$source_patched" -eq 1 ]; then
    if ! git -C "$SOURCE_DIR" apply --reverse "$SOURCE_PATCH"; then
      echo "camfly SD build: failed to restore atomcam_tools source" >&2
      rc=1
    fi
  fi
  if [ -n "$EVIDENCE_DIR" ]; then
    rm -rf -- "$EVIDENCE_DIR"
  fi
  if [ "$rc" -ne 0 ]; then
    if [ "$manifest_created" -eq 1 ]; then
      rm -f -- "$MANIFEST_FILE"
    fi
    if [ "$output_created" -eq 1 ]; then
      rm -f -- "$OUTPUT_FILE"
    fi
    if [ "$build_log_created" -eq 1 ]; then
      rm -f -- "$BUILD_LOG"
    fi
    if [ "$release_dir_created" -eq 1 ]; then
      rmdir "$RELEASE_DIR" 2>/dev/null || true
    fi
  fi
  if [ "$lock_acquired" -eq 1 ]; then
    rmdir "$BUILD_LOCK" 2>/dev/null || true
  fi
  if [ -n "$(git -C "$SOURCE_DIR" status --short)" ]; then
    echo "camfly SD build: atomcam_tools is not clean after cleanup" >&2
    rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir "$RELEASE_DIR"
release_dir_created=1

git -C "$SOURCE_DIR" apply --check "$SOURCE_PATCH"
git -C "$SOURCE_DIR" apply "$SOURCE_PATCH"
source_patched=1
cp "$KERNEL_PATCH" "$STAGING_PATCH"
kernel_patch_staged=1
staging_archive_created=1

env LIMA_HOME="$LIMA_HOME" "$LIMACTL" start "$LIMA_INSTANCE" >/dev/null

guest_docker() {
  env LIMA_HOME="$LIMA_HOME" "$LIMACTL" shell "$LIMA_INSTANCE" -- \
    env DOCKER_HOST="$DOCKER_HOST_ADDR" docker "$@"
}

guest_docker inspect "$BUILDER_CONTAINER" >/dev/null 2>&1 || \
  die "retained builder container not found: $BUILDER_CONTAINER"

if [ "$(guest_docker inspect --format '{{.State.Running}}' "$BUILDER_CONTAINER")" != "true" ]; then
  guest_docker start "$BUILDER_CONTAINER" >/dev/null
fi

guest_docker exec "$BUILDER_CONTAINER" sh -lc \
  'cd /atomtools/build/buildroot-2016.02 && make linux-dirclean'

build_log_created=1
guest_docker exec -e CAMFLY_SD_RO=1 "$BUILDER_CONTAINER" \
  /src/buildscripts/build_all 2>&1 | tee "$BUILD_LOG"

[ -f "$STAGING_ARCHIVE" ] || die "build completed without $STAGING_ARCHIVE"
output_created=1
cp -p "$STAGING_ARCHIVE" "$OUTPUT_FILE"

# Keep verification tied to the actual retained builder output.  These files
# are copied through tar without executing anything on the host and are
# removed by cleanup after the manifest is written.
EVIDENCE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/camfly-sd-evidence.XXXXXX")
mkdir -p "$EVIDENCE_DIR/rootfs" "$EVIDENCE_DIR/kernel"
guest_docker exec "$BUILDER_CONTAINER" \
  tar -C /atomtools/build/buildroot-2016.02/output/target -cf - \
    etc/init.d/rcS etc/init.d/S16fwupdate etc/init.d/S20mountfs \
    atom_patch/sbin/flash_erase \
  | tar -C "$EVIDENCE_DIR/rootfs" -xf -
guest_docker exec "$BUILDER_CONTAINER" \
  tar -C /atomtools/build/buildroot-2016.02/output/build/linux-custom -cf - \
    .config drivers/mtd/devices/jz_sfc.c \
  | tar -C "$EVIDENCE_DIR/kernel" -xf -

verify_args=(
  "$VERIFY_SCRIPT" "$OUTPUT_FILE"
  --release-id "$RELEASE_ID"
  --manifest "$MANIFEST_FILE"
  --source-commit "$EXPECTED_SOURCE_COMMIT"
  --patch "source-read-only=$SOURCE_PATCH"
  --patch "kernel-jz-sfc-read-only=$KERNEL_PATCH"
  --rootfs-dir "$EVIDENCE_DIR/rootfs"
  --kernel-config "$EVIDENCE_DIR/kernel/.config"
  --kernel-source "$EVIDENCE_DIR/kernel/drivers/mtd/devices/jz_sfc.c"
)
if [ -n "${CAMFLY_BUILDER_DIGEST:-}" ]; then
  verify_args+=(--builder-digest "$CAMFLY_BUILDER_DIGEST")
fi
manifest_created=1
python3 "${verify_args[@]}"

echo "camfly SD build: wrote $OUTPUT_FILE"
echo "camfly SD build: SHA-256 $(shasum -a 256 "$OUTPUT_FILE" | awk '{print $1}')"
echo "camfly SD build: manifest $MANIFEST_FILE"
