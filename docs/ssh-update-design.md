# Atom Cam SD boot-image update and recovery design

This record separates pinned-source facts, project observations, and unknown
hardware facts. It does not authorize a kernel/rootfs replacement, reboot,
power-loss test, or internal-flash write.

## Evidence classes

| Class | Current evidence |
| --- | --- |
| source-derived | pinned `vendor/atomcam_tools` initramfs/build/init scripts described below |
| project observation | the intended original Atom Cam 1 has previously booted from the project SD and SSH access was demonstrated; target identity is verified out of band and device-specific identity values are intentionally not stored here |
| measured in this implementation | no boot-image replacement, reboot/rollback, SD power-loss, bootloader selection, or post-update SSH recovery measurement was performed |
| unknown / blocking | actual target SD filesystem/layout/free space, available on-device hash/rename tools, bootloader fallback behavior, initramfs selection, persistence under sudden power loss, and a verified camera-specific offline recovery procedure |

A known IP address is never sufficient device identity. Any later real-device
inspection must use the operator-verified target identity and strict SSH host
key checking, then collect only non-secret metadata. Authentication files and
network credentials are not copied into logs or issue records.

## Pinned-source boot/update sequence

### Initramfs partition selection

`vendor/atomcam_tools/initramfs_skeleton/init` always starts by mounting SD
storage read-write for update processing.

If `/dev/mmcblk0p2` is detected as exFAT:

1. `/dev/mmcblk0p1` is fsck'd and mounted vfat at `/boot`;
2. `/dev/mmcblk0p2` is fsck'd and mounted exFAT at `/rootfs`.

Otherwise `/dev/mmcblk0p1` is mounted vfat at `/rootfs` and bind-mounted at
`/boot`. The source therefore supports both a two-partition boot/rootfs layout
and a one-partition layout. Which branch the target camera actually uses has
not been measured in this implementation.

### Existing update bundle behavior

When `/rootfs/update/atomcam_tools.zip` exists, initramfs extracts only:

- `factory_t31_ZMC6tiIDQN`;
- `rootfs_hack.ext2`;
- `rootfs_hack.squashfs`.

The ZIP is then removed. Rootfs candidates are size-checked using their image
headers. A valid ext2 or squashfs image is moved with `mv -f` to its fixed name
under `/rootfs`, the alternate rootfs form is removed, and `sync` is called.

The kernel candidate is checked against its uImage header size and then moved
with `mv -f` to the fixed `/boot/factory_t31_ZMC6tiIDQN` name, followed by
`sync` and `exit`. The source comment says this exit is intended to cause a
panic/reboot; that reboot behavior has not been measured here.

These are sequential file replacements. There is no source-level transaction
that atomically commits rootfs and kernel together, no boot-success marker,
and no old-kernel selector in this updater.

### Root selection and switch_root

After update processing, initramfs mounts an existing
`rootfs_hack.squashfs` or `rootfs_hack.ext2` at `/newroot`. `/boot` is then
bind-remounted read-only, `/rootfs` is moved to `/newroot/media/mmc`, `/boot`
is moved to `/newroot/boot`, and `switch_root` starts the real userspace.

This means the SD tree visible during initramfs update becomes `/media/mmc`
after boot. Internal MTD is a separate boundary and is not required for the
proposed SD boot-image updater.

## Post-switch-root persistence relevant to recovery

`S20mountfs` creates/repairs SD-backed `configs` and `tools_configs` ext2 image
files. It bind-mounts `/configs/etc/ssh` onto `/etc/ssh` and similarly keeps
Wi-Fi/lighttpd/root/crontab configuration under the SD-backed tools config
image. It may read factory configuration from internal MTD when rebuilding
missing config data; this is not authorization to write MTD.

`S21rootkeys` copies `/media/mmc/authorized_keys` into `/root/.ssh`.
`S55sshd` starts only when `/media/mmc/authorized_keys` exists, runs
`ssh-keygen -A`, and starts sshd. Because `/etc/ssh` is already an SD-backed
bind mount when these scripts run, the source suggests host keys can persist
there, but persistence across the exact target's update/recovery sequence has
not been measured and must not be assumed.

`buildscripts/post_image.sh` packages the fixed kernel/rootfs names plus
`hostname` and `authorized_keys`. The existing updater therefore has no
manifest, per-file cryptographic verification, release ID, previous-version
pointer, or compatibility gate.

## Recovery guarantee boundary

The already implemented `host/camera_deploy.py` is intentionally narrower: it
updates only versioned application files under `/media/mmc/camfly/releases`
and changes small `active`/`previous` text markers. It never changes kernel,
rootfs, initramfs, bootloader, or MTD and never reboots the camera.

For boot images, the guarantee cannot currently exceed:

- download/staging can be made non-destructive before commit;
- per-file manifest/hash/model/version checks can reject a bad candidate before
  fixed boot names are touched;
- a same-filesystem rename can reduce a rootfs pointer/file replacement window,
  but source inspection alone does not prove sudden-power-loss durability;
- a single fixed kernel filename cannot be made A/B merely by adding an A/B
  rootfs selector;
- once the camera cannot boot to SSH, SSH cannot perform rollback.

No later implementation may describe the boot-image update as atomic unless a
specific commit protocol and power-loss result justify that term.

## Failure table

| Failure point | Active boot state before failure | Automatic recovery currently justified? | Required recovery path |
| --- | --- | --- | --- |
| download / local verification | unchanged | yes: simply abandon staging | retry after identity/space/hash checks |
| remote staging before commit | unchanged | yes if staging is isolated | delete only operation-owned staging; active names unchanged |
| rootfs commit before kernel commit | potentially new rootfs + old kernel | no general compatibility guarantee | future initramfs selector may fall back only if it can detect boot failure; otherwise offline SD recovery |
| kernel commit before successful reboot | fixed kernel name changed | no A/B kernel fallback exists in pinned source | offline SD recovery unless later bootloader/initramfs evidence proves an alternate kernel selector |
| first boot before health success | candidate may be active | not implemented | future boot-attempt/health marker design; must be independent of SSH being healthy |
| SSH does not return | unknown boot/userspace state | no | power off, remove/identify the target SD, restore a verified known-good SD artifact offline; do not write internal MTD |
| power loss during FAT/exFAT metadata update | filesystem-dependent | unknown | offline filesystem/recovery test; do not infer durability from successful `rename`/`sync` alone |

## Offline manual recovery contract

Until automated recovery is actually implemented and qualified, a boot-image
change requires a verified offline recovery path prepared *before* commit:

1. have a known-good, hash-verified SD release or full SD backup for the exact
   target model available offline;
2. record which SD partition/files are intended to change without recording
   secrets;
3. if boot/SSH fails, power the camera off and remove the SD rather than trying
   network guesses or internal-flash repair;
4. identify the correct removable SD by the prepared recovery procedure;
5. restore the known-good SD boot/rootfs state or the whole verified SD image;
6. preserve/restore required SD-backed configuration only through a procedure
   that does not print private keys, Wi-Fi credentials, or device identity;
7. verify hashes/filesystem consistency before reinstalling the SD and powering
   the camera on.

Exact destructive host commands are intentionally deferred to the dedicated SD
recovery test issue, where they must be tested against a disposable image/card
first. Internal MTD write/erase is outside this recovery contract.

## Proposed boot-image manifest/commit contract

A later implementation should stage a manifest containing at least model,
release ID, compatibility relation, source release, kernel/rootfs filenames,
per-file sizes/hashes, required updater capability, and a trust-source policy.
SHA-256 detects corruption but does not authenticate an arbitrary distributor.

The Mac-side deploy tool and initramfs updater should use a unique operation ID
and an operation-owned staging directory. Existing active boot files are not
overwritten during transfer. Commit is allowed only after identity, free space,
manifest, hash, and compatibility checks pass. Concurrent operations are
rejected by a lock. Reboot remains a separate explicit action.

Rootfs versioning, boot-attempt counters, health success, and kernel fallback
must be designed separately. A health mechanism that requires SSH to start is
not sufficient to recover a userspace that fails before sshd.

## Child issues and enablement order

1. `issues/open/20260908-atomcam-boot-layout-inspection.md` — read-only target
   inspection; records filesystem/tool/boot facts without secrets.
2. `issues/open/20260908-initramfs-rootfs-selector.md` — versioned rootfs
   selection and boot-attempt state, initially in a host fixture only.
3. `issues/open/20260908-boot-image-deploy-cli.md` — Mac-side manifest/staging/
   commit CLI; no implicit reboot.
4. `issues/open/20260908-boot-health-rollback.md` — early boot success/failure
   protocol and fallback; kernel limits remain explicit.
5. `issues/open/20260908-sd-recovery-powerloss-test.md` — disposable SD/image
   recovery and interruption matrix before any target destructive test.

The hardware-dependent parts of items 2–5 remain blocked until item 1 provides
the missing facts. Runtime-only SSH deploy is independent and remains covered
by `issues/open/20260908-ssh-runtime-deploy.md`.
