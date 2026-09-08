# Atom Cam SD update and recovery boundary

This is a source-derived design record. No kernel/rootfs replacement or power
loss test is authorized by the roadmap implementation.

## Boot sequence observed in the pinned source

`vendor/atomcam_tools/initramfs_skeleton/init` mounts the SD card at
`/rootfs`, optionally extracts `update/atomcam_tools.zip`, validates and moves
`rootfs_hack.ext2` or `rootfs_hack.squashfs` into the SD root, and moves the
mounted SD tree to `/media/mmc` before `switch_root`. The kernel file is moved
to `/boot` when a matching `factory_t31_ZMC6tiIDQN` update is present. These
operations are sequential moves, not an atomic rootfs+kernel transaction.

After `switch_root`, `S20mountfs` can create or repair SD filesystems and
mounts configuration data. `S21rootkeys` copies `/media/mmc/authorized_keys`
into the runtime SSH directory, and `S55sshd` starts only when that file is
present. `S16fwupdate` is disabled in the read-only SD patch, but the ordinary
image has flash-update behavior and must not be confused with the derived
image.

## Implemented boundary

`host/camera_deploy.py` updates only `/media/mmc/camfly/releases`, using
versioned directories and `active`/`previous` text markers. It validates the
bundle manifest locally, checks the explicitly identified camera before
staging, verifies per-file hashes on the remote SD, and never restarts a
service or writes MTD. `activate` and `rollback` update markers with a same-SD
temporary file and `sync`; this is a bounded marker protocol, not a claim of
power-loss atomicity.

## Recovery limits

If SSH is unavailable after a boot-image change, SSH cannot perform the
rollback. The known recovery path remains a manually identified SD card and a
known-good artifact. A/B rootfs alone cannot recover a single replaced kernel
or a damaged initramfs. The following are intentionally unknown until a
hardware-specific, read-only investigation is completed:

- the user's confirmed camera MAC and SSH host-key fingerprint;
- exact SD filesystem type and free space;
- fixed bootloader filenames and whether initramfs can be selected;
- rename/flush behavior across power loss;
- a camera-specific manual recovery image.

The ordinary Atom Cam 1 target and the internal MTD boundary are explicit;
Atom Cam 2 and internal-flash writes are out of scope.
