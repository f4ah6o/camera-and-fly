#!/usr/bin/env python3
"""Verify an Atom Cam SD release archive and write a redacted manifest.

The verifier inspects archive structure and bytes only.  It never extracts or
executes a file from the ZIP.  Source-tree evidence is accepted separately so
an archive hash is not mistaken for proof that a source patch reached a
binary.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat
import sys
import zipfile


ALLOWED_FILES = (
    "factory_t31_ZMC6tiIDQN",
    "rootfs_hack.squashfs",
    "hostname",
    "authorized_keys",
)
MAX_FILE_BYTES = {
    "factory_t31_ZMC6tiIDQN": 16 * 1024 * 1024,
    "rootfs_hack.squashfs": 64 * 1024 * 1024,
    "hostname": 4096,
    "authorized_keys": 1024 * 1024,
}


class VerificationError(ValueError):
    """The archive does not satisfy the release contract."""


@dataclass(frozen=True)
class FileDigest:
    size: int
    sha256: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _validate_name(name: str) -> None:
    if name not in ALLOWED_FILES:
        raise VerificationError(f"ZIP contains a disallowed path: {name!r}")
    path = Path(name)
    if name.startswith("/") or ".." in path.parts or path.parts != (name,):
        raise VerificationError(f"ZIP contains an unsafe path: {name!r}")


def _source_evidence(
    rootfs_dir: Path | None,
    kernel_config: Path | None,
    kernel_source: Path | None,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "rootfs_tree_checked": rootfs_dir is not None,
        "kernel_config_checked": kernel_config is not None,
        "kernel_source_checked": kernel_source is not None,
        "checks": {},
    }
    checks: dict[str, bool] = {}
    if rootfs_dir is not None:
        if not rootfs_dir.is_dir():
            raise VerificationError(f"rootfs evidence directory is not a directory: {rootfs_dir}")
        files = {
            "rcS": rootfs_dir / "etc/init.d/rcS",
            "fwupdate": rootfs_dir / "etc/init.d/S16fwupdate",
            "mountfs": rootfs_dir / "etc/init.d/S20mountfs",
            "flash_erase": rootfs_dir / "atom_patch/sbin/flash_erase",
        }
        contents: dict[str, str] = {}
        for name, path in files.items():
            if not path.is_file() or path.is_symlink():
                checks[name] = False
                continue
            contents[name] = path.read_text(errors="replace")
        checks["rcS_guard"] = "mtd_readonly_guard" in contents.get("rcS", "")
        checks["fwupdate_disabled"] = "disabled in the SD read-only build" in contents.get("fwupdate", "")
        checks["mounts_read_only"] = "mount -t squashfs -o ro" in contents.get("mountfs", "")
        checks["flash_erase_stub"] = "disabled in camfly SD read-only build" in contents.get("flash_erase", "")
    if kernel_config is not None:
        if not kernel_config.is_file() or kernel_config.is_symlink():
            raise VerificationError(f"kernel config is not a regular file: {kernel_config}")
        config = kernel_config.read_text(errors="replace")
        checks["kernel_cmdline_mtd_ro"] = 'mtdparts=jz_sfc:' in config and all(
            part in config for part in ("(boot)ro", "(kernel)ro", "(rootfs)ro", "(app)ro", "(cfg)ro", "(para)ro")
        )
    if kernel_source is not None:
        if not kernel_source.is_file() or kernel_source.is_symlink():
            raise VerificationError(f"kernel source evidence is not a regular file: {kernel_source}")
        source = kernel_source.read_text(errors="replace")
        checks["kernel_writeable_bit_removed"] = "MTD_CAP_NORFLASH & ~MTD_WRITEABLE" in source
    evidence["checks"] = checks
    evidence["all_checked_passed"] = bool(checks) and all(checks.values())
    return evidence


def verify_archive(
    archive: Path,
    *,
    release_id: str,
    source_commit: str | None = None,
    patch_sha256: dict[str, str] | None = None,
    builder_digest: str | None = None,
    rootfs_dir: Path | None = None,
    kernel_config: Path | None = None,
    kernel_source: Path | None = None,
) -> dict[str, object]:
    if not archive.is_file() or archive.is_symlink():
        raise VerificationError(f"archive is not a regular file: {archive}")
    if not release_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in release_id):
        raise VerificationError("release_id contains unsupported characters")

    digests: dict[str, FileDigest] = {}
    with zipfile.ZipFile(archive, "r") as archive_file:
        infos = archive_file.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise VerificationError("ZIP contains duplicate member names")
        if set(names) != set(ALLOWED_FILES):
            missing = sorted(set(ALLOWED_FILES) - set(names))
            extra = sorted(set(names) - set(ALLOWED_FILES))
            raise VerificationError(f"ZIP member set mismatch: missing={missing} extra={extra}")
        bad_crc = archive_file.testzip()
        if bad_crc is not None:
            raise VerificationError(f"ZIP CRC check failed for {bad_crc}")
        for info in infos:
            _validate_name(info.filename)
            if info.is_dir() or _is_symlink(info):
                raise VerificationError(f"ZIP member is not a regular file: {info.filename}")
            limit = MAX_FILE_BYTES[info.filename]
            if info.file_size < 0 or info.file_size > limit:
                raise VerificationError(f"ZIP member exceeds size limit: {info.filename}")
            data = archive_file.read(info)
            if len(data) != info.file_size:
                raise VerificationError(f"ZIP member size changed while reading: {info.filename}")
            digests[info.filename] = FileDigest(len(data), _sha256(data))
            if info.filename == "factory_t31_ZMC6tiIDQN" and not data.startswith(b"\x27\x05\x19\x56"):
                raise VerificationError("factory_t31_ZMC6tiIDQN is not a uImage")
            if info.filename == "rootfs_hack.squashfs" and not data.startswith(b"hsqs"):
                raise VerificationError("rootfs_hack.squashfs is not a SquashFS image")

    manifest: dict[str, object] = {
        "schema_version": 1,
        "release_id": release_id,
        "archive": {
            "name": archive.name,
            "size": archive.stat().st_size,
            "sha256": _sha256(archive.read_bytes()),
        },
        "allowed_files": list(ALLOWED_FILES),
        "files": {name: {"size": value.size, "sha256": value.sha256} for name, value in sorted(digests.items())},
        "checks": {
            "member_set": True,
            "unique_names": True,
            "crc": True,
            "no_symlinks": True,
            "uimage_header": True,
            "squashfs_header": True,
        },
        "source_commit": source_commit,
        "patch_sha256": dict(sorted((patch_sha256 or {}).items())),
        "builder_digest": builder_digest,
        "source_evidence": _source_evidence(rootfs_dir, kernel_config, kernel_source),
        "notes": [
            "SHA-256 detects corruption; it does not authenticate a distribution source.",
            "authorized_keys contents are intentionally omitted from the manifest.",
            "archive verification does not prove bootloader or camera hardware behavior.",
        ],
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="verify an Atom Cam SD release ZIP")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--builder-digest")
    parser.add_argument("--rootfs-dir", type=Path)
    parser.add_argument("--kernel-config", type=Path)
    parser.add_argument("--kernel-source", type=Path)
    parser.add_argument("--patch", action="append", default=[], metavar="NAME=PATH")
    args = parser.parse_args(argv)
    patch_hashes: dict[str, str] = {}
    try:
        for item in args.patch:
            name, separator, path_text = item.partition("=")
            if not separator or not name or not path_text:
                raise VerificationError("--patch must be NAME=PATH")
            path = Path(path_text)
            if not path.is_file() or path.is_symlink():
                raise VerificationError(f"patch is not a regular file: {path}")
            patch_hashes[name] = _sha256(path.read_bytes())
        manifest = verify_archive(
            args.archive,
            release_id=args.release_id,
            source_commit=args.source_commit,
            patch_sha256=patch_hashes,
            builder_digest=args.builder_digest,
            rootfs_dir=args.rootfs_dir,
            kernel_config=args.kernel_config,
            kernel_source=args.kernel_source,
        )
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    except (OSError, zipfile.BadZipFile, VerificationError) as exc:
        print(f"release verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
