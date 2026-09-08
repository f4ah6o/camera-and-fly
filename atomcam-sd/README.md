# Atom Cam SD read-only build

This directory contains the project-owned, reproducible hardening layer for an
original Atom Cam 1 SD-boot image. It applies to the pinned
`mnakada/atomcam_tools` commit `313048b4d652b0058271ffa42de1289e9d5d08ee`.

Build it with the retained 4 GiB Lima instance and builder container:

```sh
./atomcam-sd/build.sh
```

Each invocation receives a new UTC release ID (or the explicitly supplied
`CAMFLY_SD_RELEASE_ID`) and writes a ZIP, build log, and `manifest.json` under
`artifacts/<release-id>/`. Existing artifacts are never reused. The script
refuses a dirty `vendor/atomcam_tools` checkout, rejects output paths outside
`artifacts/`, takes an atomic build lock, and restores the submodule on normal,
failed, or interrupted builds. To resume a release after a failed VM attempt,
choose a new release ID; do not delete or overwrite the previous directory.

It temporarily applies
`patches/0001-source-read-only.patch`, stages the kernel patch into the pinned
submodule, forces a kernel dirclean, and restores the submodule before exit.
Before writing the manifest it copies only the relevant rootfs init scripts,
the built kernel `.config`, and the built `jz_sfc.c` through a tar stream for
source-evidence checks; the temporary evidence directory is removed during
cleanup.
The existing ordinary `vendor/atomcam_tools/atomcam_tools.zip` is not used or
overwritten. Set `CAMFLY_BUILDER_DIGEST` when the retained builder digest is
known; it is recorded in the manifest rather than inferred from a mutable
container name.

The archive verifier can also be run without Lima:

```sh
python3 atomcam-sd/verify_release.py artifacts/<release-id>/<archive>.zip \
  --release-id <release-id> \
  --manifest artifacts/<release-id>/manifest.json \
  --source-commit 313048b4d652b0058271ffa42de1289e9d5d08ee \
  --patch source-read-only=atomcam-sd/patches/0001-source-read-only.patch \
  --patch kernel-jz-sfc-read-only=atomcam-sd/patches/0002-kernel-jz-sfc-read-only.patch
```

`verify_release.py` requires exactly the four expected regular files, checks
duplicate names, path traversal, symlinks, CRC, bounded sizes, the uImage and
SquashFS magic bytes, and records per-file hashes. The build path also records
checks against the retained builder's extracted rootfs and kernel source/config.
It never prints `authorized_keys`. A successful ZIP check is not proof of
bootloader behavior or a hardware boot test; source evidence is recorded
separately from the archive checks.

The variant provides these defenses:

- all eight source-defined `jz_sfc` MTD partitions carry the kernel `ro` flag;
- the JZ SFC master clears `MTD_WRITEABLE` as defense in depth;
- startup firmware-update logic is removed;
- the flash configuration JFFS2 mount is explicitly read-only;
- the legacy `flash_erase` path is replaced by a failing stub; and
- startup stops before service scripts if any sysfs MTD flags expose
  `MTD_WRITEABLE`, or if MTD flags cannot be inspected.

This is still not a guarantee against bootloader activity before Linux, direct
SoC/register access outside the MTD API, an unverified camera partition layout,
or a different kernel being booted. Before using the image on hardware, inspect
the built kernel configuration and root filesystem, identify the original
camera by the user-supplied MAC, and capture live CPU/MTD metadata. No physical
disk or camera flash is selected or written by this build script.

## Startup guard regression tests

The guard requires readable flags for exactly `mtd0` through `mtd7`, validates
32-bit hexadecimal values before arithmetic, and rejects any partition with
`MTD_WRITEABLE` set. Missing flags, read failures, malformed values, and extra
MTD devices stop startup before service scripts run.

Run the hardware-independent tests with:

```sh
python3 -m unittest discover -s atomcam-sd/tests -v
```

Tests apply the actual rcS patch in a temporary directory and run its guard
against a synthetic sysfs tree. They do not start services or contact a camera.
`CAMFLY_TEST_SHELL` can select another POSIX shell executable for the tests.
This checks shell behavior on the host; it does not validate camera boot.

The pre-existing 2026-09-08 SD archive predates this stricter guard and remains
unchanged. These source changes require a fresh, separately named build and
artifact inspection before hardware use; running the tests never rebuilds or
replaces an archive.
