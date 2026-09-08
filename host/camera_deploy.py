#!/usr/bin/env python3
"""Explicit, versioned Atom Cam SD runtime deployment.

This CLI never writes camera flash and never disables SSH host-key checking.
The live runner uses one fixed remote shell script and passes release IDs as
argv values after local validation.  Tests can inject ``FakeRemoteRunner``;
the default CLI still requires an explicit host, known_hosts file, expected
MAC, and bundle.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
from typing import Iterable, Protocol
import zipfile


RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_FILES = 128
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
REMOTE_ROOT = "/media/mmc/camfly"


class DeployError(RuntimeError):
    pass


def _safe_relative(path: str) -> str:
    if not path or "\\" in path or path.startswith("/"):
        raise DeployError(f"unsafe bundle path: {path!r}")
    parsed = PurePosixPath(path)
    if parsed.parts != tuple(part for part in parsed.parts if part not in ("", ".")):
        raise DeployError(f"unsafe bundle path: {path!r}")
    if ".." in parsed.parts or parsed.as_posix() != path:
        raise DeployError(f"unsafe bundle path: {path!r}")
    return path


def _safe_release_id(value: str) -> str:
    if not RELEASE_RE.fullmatch(value):
        raise DeployError("release ID must be 1-64 ASCII letters, digits, '.', '_' or '-'")
    return value


@dataclass(frozen=True)
class Bundle:
    release_id: str
    target_model: str
    entrypoint: str
    files: dict[str, bytes]
    manifest: dict[str, object]

    @property
    def total_bytes(self) -> int:
        return sum(len(value) for value in self.files.values())

    @property
    def hashes(self) -> dict[str, str]:
        return {path: hashlib.sha256(data).hexdigest() for path, data in sorted(self.files.items())}


def _read_bundle_members(path: Path) -> dict[str, bytes]:
    if path.is_dir() and not path.is_symlink():
        result: dict[str, bytes] = {}
        for candidate in path.rglob("*"):
            relative = candidate.relative_to(path).as_posix()
            _safe_relative(relative)
            if candidate.is_dir() and not candidate.is_symlink():
                continue
            if candidate.is_symlink() or not candidate.is_file():
                raise DeployError(f"bundle member is not a regular file: {relative}")
            if candidate.stat().st_size > MAX_FILE_BYTES:
                raise DeployError(f"bundle member exceeds size limit: {relative}")
            result[relative] = candidate.read_bytes()
        return result
    if path.is_file() and path.suffix.lower() == ".zip" and not path.is_symlink():
        result = {}
        with zipfile.ZipFile(path, "r") as archive:
            names = [info.filename for info in archive.infolist()]
            if len(names) != len(set(names)):
                raise DeployError("bundle ZIP contains duplicate members")
            for info in archive.infolist():
                relative = _safe_relative(info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if info.is_dir() or stat.S_IFMT(mode) == stat.S_IFLNK:
                    raise DeployError(f"bundle member is not a regular file: {relative}")
                if info.file_size > MAX_FILE_BYTES:
                    raise DeployError(f"bundle member exceeds size limit: {relative}")
                result[relative] = archive.read(info)
        return result
    raise DeployError("bundle must be a regular directory or .zip file")


def load_bundle(path: Path) -> Bundle:
    members = _read_bundle_members(path)
    if "manifest.json" not in members:
        raise DeployError("bundle is missing manifest.json")
    try:
        manifest = json.loads(members["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeployError("bundle manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("type") != "runtime":
        raise DeployError("bundle manifest type must be runtime")
    release_id = _safe_release_id(str(manifest.get("release_id", "")))
    target_model = str(manifest.get("target_model", ""))
    if not target_model or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in target_model):
        raise DeployError("bundle target_model is missing or unsafe")
    entrypoint = _safe_relative(str(manifest.get("entrypoint", "")))
    declared = manifest.get("files")
    if not isinstance(declared, list) or not 0 < len(declared) <= MAX_FILES:
        raise DeployError("bundle files must be a non-empty list")

    files: dict[str, bytes] = {}
    for item in declared:
        if not isinstance(item, dict):
            raise DeployError("bundle file entry must be an object")
        relative = _safe_relative(str(item.get("path", "")))
        if relative in files or relative == "manifest.json":
            raise DeployError(f"duplicate or reserved bundle path: {relative}")
        if relative not in members:
            raise DeployError(f"manifest file is missing from bundle: {relative}")
        data = members[relative]
        try:
            size = int(item["size"])
            expected_hash = str(item["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DeployError(f"invalid manifest entry for {relative}") from exc
        if size != len(data) or size > MAX_FILE_BYTES:
            raise DeployError(f"manifest size mismatch for {relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or hashlib.sha256(data).hexdigest() != expected_hash:
            raise DeployError(f"manifest hash mismatch for {relative}")
        files[relative] = data
    if entrypoint not in files:
        raise DeployError("manifest entrypoint is not listed in files")
    if set(members) != set(files) | {"manifest.json"}:
        raise DeployError("bundle contains an undeclared file")
    if sum(len(data) for data in files.values()) > MAX_BUNDLE_BYTES:
        raise DeployError("bundle exceeds total size limit")
    return Bundle(release_id, target_model, entrypoint, files, manifest)


class RemoteRunner(Protocol):
    def run(self, action: str, args: tuple[str, ...] = (), payload: bytes = b"") -> str: ...


REMOTE_SCRIPT = r'''#!/bin/sh
set -eu
root=/media/mmc/camfly
action=${1-}
arg=${2-}
token=${3-}
safe_id() {
  case "$1" in
    ''|.*|*[!A-Za-z0-9._-]*) return 1 ;;
  esac
}
atomic_text() {
  value=$1
  destination=$2
  temporary="$destination.tmp.$$"
  printf '%s\n' "$value" > "$temporary"
  sync
  mv -f "$temporary" "$destination"
  sync
}
acquire_lock() {
  if ! mkdir "$root/.deploy.lock" 2>/dev/null; then
    exit 30
  fi
  trap 'rmdir "$root/.deploy.lock" 2>/dev/null || true' EXIT
}
case "$action" in
  inspect)
    [ -d /media/mmc ] || exit 20
    free=$(df -Pk /media/mmc | awk 'NR==2 {print $4}')
    [ -n "$free" ] || exit 21
    mac=$(cat /sys/class/net/wlan0/address 2>/dev/null || true)
    hash_tool=$(command -v sha256sum || true)
    printf 'MOUNT=/media/mmc\nFREE_KIB=%s\nMAC=%s\nMODEL=atomcam1\nHASH_TOOL=%s\n' "$free" "$mac" "$hash_tool"
    ;;
  status)
    active=$(cat "$root/active" 2>/dev/null || true)
    previous=$(cat "$root/previous" 2>/dev/null || true)
    printf 'ACTIVE=%s\nPREVIOUS=%s\nROOT=%s\n' "$active" "$previous" "$root"
    ;;
  stage)
    safe_id "$arg"
    safe_id "$token"
    incoming=${4-}
    [ "$incoming" = "/media/mmc/.camfly-upload-$arg-$token.tar" ]
    [ -f "$incoming" ]
    [ -d /media/mmc ]
    mkdir -p "$root"
    acquire_lock
    mkdir -p "$root/.staging" "$root/releases"
    temporary="$root/.staging/$arg.$$"
    cleanup_stage() {
      [ -z "$temporary" ] || rm -rf "$temporary"
      [ -z "$incoming" ] || rm -f "$incoming"
    }
    trap 'cleanup_stage; rmdir "$root/.deploy.lock" 2>/dev/null || true' EXIT
    mkdir "$temporary"
    tar -x -f "$incoming" -C "$temporary"
    [ "$(cat "$temporary/manifest.release_id")" = "$arg" ]
    [ -f "$temporary/SHA256SUMS" ]
    (cd "$temporary" && sha256sum -c SHA256SUMS >/dev/null)
    if [ -e "$root/releases/$arg" ]; then
      [ -d "$root/releases/$arg" ] || exit 31
      cmp -s "$temporary/manifest.release_id" "$root/releases/$arg/manifest.release_id" || exit 31
      cmp -s "$temporary/manifest.model" "$root/releases/$arg/manifest.model" || exit 31
      cmp -s "$temporary/manifest.entrypoint" "$root/releases/$arg/manifest.entrypoint" || exit 31
      cmp -s "$temporary/SHA256SUMS" "$root/releases/$arg/SHA256SUMS" || exit 31
      (cd "$root/releases/$arg" && sha256sum -c SHA256SUMS >/dev/null) || exit 31
      exit 0
    fi
    mv "$temporary" "$root/releases/$arg"
    temporary=
    rm -f "$incoming"
    sync
    ;;
  activate)
    safe_id "$arg"
    [ -d "$root" ]
    acquire_lock
    [ -d "$root/releases/$arg" ]
    [ -f "$root/releases/$arg/manifest.entrypoint" ]
    current=$(cat "$root/active" 2>/dev/null || true)
    if [ -n "$current" ]; then atomic_text "$current" "$root/previous"; fi
    atomic_text "$arg" "$root/active"
    ;;
  rollback)
    [ -d "$root" ]
    acquire_lock
    previous=$(cat "$root/previous")
    safe_id "$previous"
    [ -d "$root/releases/$previous" ]
    current=$(cat "$root/active" 2>/dev/null || true)
    if [ -n "$current" ]; then atomic_text "$current" "$root/previous"; fi
    atomic_text "$previous" "$root/active"
    ;;
  *) exit 2 ;;
esac
'''


class SSHRunner:
    def __init__(self, host: str, known_hosts: Path, *, dry_run: bool = False) -> None:
        if (
            not host
            or host.startswith("-")
            or not re.fullmatch(r"[A-Za-z0-9_.@:\[\]-]+", host)
        ):
            raise DeployError("host must be explicit and single-line")
        if not known_hosts.is_file() or known_hosts.is_symlink():
            raise DeployError("known_hosts must be an existing regular file")
        self.host = host
        self.known_hosts = known_hosts
        self.dry_run = dry_run
        self.commands: list[list[str]] = []

    def run(self, action: str, args: tuple[str, ...] = (), payload: bytes = b"") -> str:
        if action not in {"inspect", "status", "stage", "activate", "rollback"}:
            raise DeployError("unsupported remote action")
        for value in args:
            if value != _safe_release_id(value):
                raise DeployError("unsafe remote argument")
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts}",
            self.host,
            "sh",
            "-s",
            "--",
            action,
            *args,
        ]
        self.commands.append(command)
        if self.dry_run:
            return "DRY_RUN"
        script = REMOTE_SCRIPT.encode("utf-8")
        if action == "stage":
            if len(args) != 2:
                raise DeployError("stage requires release ID and upload token")
            release, upload_token = args
            remote_path = f"/media/mmc/.camfly-upload-{release}-{upload_token}.tar"
            command.extend([remote_path])
            if self.dry_run:
                return "DRY_RUN"
            with tempfile.NamedTemporaryFile(prefix="camfly-stage-", suffix=".tar") as temporary:
                temporary.write(payload)
                temporary.flush()
                scp_command = [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    f"UserKnownHostsFile={self.known_hosts}",
                    temporary.name,
                    f"{self.host}:{remote_path}",
                ]
                self.commands.append(scp_command)
                uploaded = subprocess.run(
                    scp_command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if uploaded.returncode != 0:
                    raise DeployError(
                        f"scp stage failed ({uploaded.returncode}): "
                        f"{uploaded.stderr.decode(errors='replace').strip()}"
                    )
        result = subprocess.run(
            command,
            input=script,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            raise DeployError(f"ssh {action} failed ({result.returncode}): {result.stderr.decode(errors='replace').strip()}")
        return result.stdout.decode("utf-8", "replace")


class FakeRemoteRunner:
    """In-memory runner for deploy tests; no filesystem or SSH side effects."""

    def __init__(self, *, mac: str = "00:11:22:33:44:55", model: str = "atomcam1", free_kib: int = 100000) -> None:
        self.mac = mac
        self.model = model
        self.free_kib = free_kib
        self.active = ""
        self.previous = ""
        self.releases: dict[str, bytes] = {}
        self.calls: list[tuple[str, tuple[str, ...], bytes]] = []

    def run(self, action: str, args: tuple[str, ...] = (), payload: bytes = b"") -> str:
        self.calls.append((action, args, payload))
        if action == "inspect":
            return f"MOUNT=/media/mmc\nFREE_KIB={self.free_kib}\nMAC={self.mac}\nMODEL={self.model}\nHASH_TOOL=sha256sum\n"
        if action == "status":
            return f"ACTIVE={self.active}\nPREVIOUS={self.previous}\nROOT={REMOTE_ROOT}\n"
        if action == "stage":
            release = _safe_release_id(args[0])
            if release in self.releases:
                if self.releases[release] == payload:
                    return "IDEMPOTENT"
                raise DeployError("release already exists with different content")
            self.releases[release] = payload
            return "STAGED"
        if action == "activate":
            release = _safe_release_id(args[0])
            if release not in self.releases:
                raise DeployError("release is not staged")
            if self.active:
                self.previous = self.active
            self.active = release
            return "ACTIVATED"
        if action == "rollback":
            if not self.previous or self.previous not in self.releases:
                raise DeployError("previous release is unavailable")
            self.active, self.previous = self.previous, self.active
            return "ROLLED_BACK"
        raise DeployError("unsupported fake action")


def make_stage_payload(bundle: Bundle) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        metadata = {
            "manifest.release_id": bundle.release_id.encode("ascii"),
            "manifest.model": bundle.target_model.encode("ascii"),
            "manifest.entrypoint": bundle.entrypoint.encode("utf-8"),
        }
        sums = "".join(f"{digest}  {path}\n" for path, digest in bundle.hashes.items()).encode("ascii")
        metadata["SHA256SUMS"] = sums
        for name, data in {**metadata, **bundle.files}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


class CameraDeployer:
    def __init__(self, runner: RemoteRunner, *, expected_mac: str, expected_model: str = "atomcam1") -> None:
        if not re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", expected_mac):
            raise DeployError("expected MAC must be six hexadecimal octets")
        if not expected_model:
            raise DeployError("expected model is required")
        self.runner = runner
        self.expected_mac = expected_mac.lower()
        self.expected_model = expected_model

    def inspect(self) -> dict[str, str]:
        result = self._parse_kv(self.runner.run("inspect"))
        if result.get("MOUNT") != "/media/mmc":
            raise DeployError("remote mount is not the expected SD mount")
        if result.get("MAC", "").lower() != self.expected_mac:
            raise DeployError("remote MAC does not match the explicit expected camera")
        if result.get("MODEL") != self.expected_model:
            raise DeployError("remote model does not match the explicit target")
        if result.get("HASH_TOOL") != "sha256sum":
            raise DeployError("remote sha256sum tool is unavailable")
        try:
            if int(result["FREE_KIB"]) < 0:
                raise ValueError
        except (KeyError, ValueError) as exc:
            raise DeployError("remote free space is invalid") from exc
        return result

    def stage(self, bundle: Bundle) -> str:
        info = self.inspect()
        required_kib = (bundle.total_bytes + 1023) // 1024 + 1024
        if int(info["FREE_KIB"]) < required_kib:
            raise DeployError("remote SD free space is insufficient")
        payload = make_stage_payload(bundle)
        upload_token = hashlib.sha256(payload).hexdigest()[:16]
        return self.runner.run("stage", (bundle.release_id, upload_token), payload)

    def activate(self, release_id: str) -> str:
        return self.runner.run("activate", (_safe_release_id(release_id),))

    def status(self) -> dict[str, str]:
        return self._parse_kv(self.runner.run("status"))

    def rollback(self) -> str:
        return self.runner.run("rollback")

    @staticmethod
    def _parse_kv(output: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key.isupper():
                result[key] = value
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Atom Cam 1 SD runtime deployer")
    parser.add_argument("command", choices=("inspect", "stage", "activate", "status", "rollback"))
    parser.add_argument("--host", required=True, help="explicit SSH host/user@host")
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--expected-mac", required=True)
    parser.add_argument("--expected-model", default="atomcam1")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--release-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        runner = SSHRunner(args.host, args.known_hosts, dry_run=args.dry_run)
        deployer = CameraDeployer(runner, expected_mac=args.expected_mac, expected_model=args.expected_model)
        if args.command == "stage" and not args.bundle:
            parser.error("stage requires --bundle")
        if args.command == "activate" and not args.release_id:
            parser.error("activate requires --release-id")
        if args.dry_run:
            if args.command == "stage":
                bundle = load_bundle(args.bundle)
                detail = {"release_id": bundle.release_id, "bytes": bundle.total_bytes}
            elif args.command == "activate":
                detail = {"release_id": _safe_release_id(args.release_id)}
            else:
                detail = {}
            print(json.dumps({"dry_run": True, "command": args.command, "host": args.host, **detail}, sort_keys=True))
            return 0
        if args.command == "inspect":
            result = deployer.inspect()
        elif args.command == "status":
            result = deployer.status()
        elif args.command == "rollback":
            result = deployer.rollback()
        elif args.command == "stage":
            result = deployer.stage(load_bundle(args.bundle))
        else:
            result = deployer.activate(args.release_id)
        print(json.dumps(result, sort_keys=True) if isinstance(result, dict) else result)
        return 0
    except (DeployError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"camera deploy error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
