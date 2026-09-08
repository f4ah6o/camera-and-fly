from __future__ import annotations

import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "atomcam-sd" / "build.sh"


FAKE_LIMACTL = r'''#!/usr/bin/env python3
import io
import os
from pathlib import Path
import sys
import tarfile
import time
import zipfile

source = Path(os.environ["CAMFLY_FAKE_SOURCE"])
mode = os.environ.get("CAMFLY_FAKE_MODE", "success")
evidence = Path(os.environ["CAMFLY_FAKE_EVIDENCE"])

def make_archive():
    with zipfile.ZipFile(source / "atomcam_tools-sd-ro.zip", "w") as archive:
        archive.writestr("factory_t31_ZMC6tiIDQN", b"\x27\x05\x19\x56" + b"k" * 32)
        archive.writestr("rootfs_hack.squashfs", b"hsqs" + b"r" * 32)
        archive.writestr("hostname", b"atomcam\n")
        archive.writestr("authorized_keys", b"ssh-ed25519 AAAA fixture\n")

def emit_tar(root, names):
    output = sys.stdout.buffer
    with tarfile.open(fileobj=output, mode="w|") as archive:
        for name in names:
            path = root / name
            info = tarfile.TarInfo(name)
            data = path.read_bytes()
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))

if sys.argv[1] == "start":
    raise SystemExit(0)
if sys.argv[1] != "shell":
    raise SystemExit(2)
try:
    docker = sys.argv.index("docker")
except ValueError:
    raise SystemExit(2)
args = sys.argv[docker + 1:]
action = args[0]
if action == "inspect":
    if "--format" in args:
        print("true")
    raise SystemExit(0)
if action == "start":
    raise SystemExit(0)
if action != "exec":
    raise SystemExit(2)
args = args[1:]
if args[:1] == ["-e"]:
    args = args[2:]
container = args.pop(0)
command = args
if command[:2] == ["sh", "-lc"]:
    raise SystemExit(0)
if command[:1] == ["/src/buildscripts/build_all"]:
    if mode == "signal-block":
        Path(os.environ["CAMFLY_FAKE_STARTED"]).write_text("builder-started\n")
        time.sleep(30)
    if mode == "build-failure":
        print("fake builder failure", flush=True)
        raise SystemExit(23)
    make_archive()
    print("fake build completed", flush=True)
    raise SystemExit(0)
if command[:1] == ["tar"]:
    try:
        change = command.index("-C")
        requested_root = command[change + 1]
        names = command[change + 4:]
    except (ValueError, IndexError):
        raise SystemExit(2)
    root = evidence / ("rootfs" if "output/target" in requested_root else "kernel")
    emit_tar(root, names)
    raise SystemExit(0)
raise SystemExit(2)
'''


class SdBuildHardeningTests(unittest.TestCase):
    def fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "vendor" / "atomcam_tools"
        patch_dir = root / "patches"
        output = root / "artifacts"
        source.mkdir(parents=True)
        (source / "patches" / "kernel").mkdir(parents=True)
        (source / "tracked.txt").write_text("before\n")
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=source, check=True)
        subprocess.run(["git", "config", "user.name", "fixture"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)
        (source / "tracked.txt").write_text("after\n")
        patch_dir.mkdir()
        patch = subprocess.run(["git", "diff", "--", "tracked.txt"], cwd=source, check=True, capture_output=True, text=True).stdout
        (patch_dir / "0001-source-read-only.patch").write_text(patch)
        subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=source, check=True)
        (patch_dir / "0002-kernel-jz-sfc-read-only.patch").write_text("fixture kernel patch\n")

        evidence = root / "evidence"
        (evidence / "rootfs" / "etc" / "init.d").mkdir(parents=True)
        (evidence / "rootfs" / "atom_patch" / "sbin").mkdir(parents=True)
        (evidence / "kernel" / "drivers" / "mtd" / "devices").mkdir(parents=True)
        (evidence / "rootfs" / "etc" / "init.d" / "rcS").write_text("mtd_readonly_guard\n")
        (evidence / "rootfs" / "etc" / "init.d" / "S16fwupdate").write_text("disabled in the SD read-only build\n")
        (evidence / "rootfs" / "etc" / "init.d" / "S20mountfs").write_text("mount -t squashfs -o ro\n")
        (evidence / "rootfs" / "atom_patch" / "sbin" / "flash_erase").write_text("disabled in camfly SD read-only build\n")
        (evidence / "kernel" / ".config").write_text("mtdparts=jz_sfc:(boot)ro,(kernel)ro,(rootfs)ro,(app)ro,(cfg)ro,(para)ro\n")
        (evidence / "kernel" / "drivers" / "mtd" / "devices" / "jz_sfc.c").write_text("MTD_CAP_NORFLASH & ~MTD_WRITEABLE\n")

        limactl = root / "limactl"
        limactl.write_text(FAKE_LIMACTL)
        limactl.chmod(limactl.stat().st_mode | stat.S_IXUSR)
        expected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, check=True, capture_output=True, text=True).stdout.strip()
        env = {
            "CAMFLY_SD_ROOT_DIR": str(root),
            "CAMFLY_SD_SOURCE_DIR": str(source),
            "CAMFLY_SD_PATCH_DIR": str(patch_dir),
            "CAMFLY_SD_OUTPUT_DIR": str(output),
            "CAMFLY_SD_EXPECTED_SOURCE_COMMIT": expected,
            "CAMFLY_SD_VERIFY_SCRIPT": str(ROOT / "atomcam-sd" / "verify_release.py"),
            "CAMFLY_SD_RELEASE_ID": "fixture-release",
            "LIMACTL": str(limactl),
            "CAMFLY_FAKE_SOURCE": str(source),
            "CAMFLY_FAKE_EVIDENCE": str(evidence),
            "CAMFLY_FAKE_STARTED": str(root / "builder-started"),
            "LIMA_HOME": str(root / "lima-home"),
            "PATH": os.environ["PATH"],
        }
        return temporary, root, env

    def run_build(self, env: dict[str, str], *, mode: str = "success", timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        child_env = os.environ.copy()
        child_env.update(env)
        child_env["CAMFLY_FAKE_MODE"] = mode
        return subprocess.run(["bash", str(BUILD)], cwd=ROOT, env=child_env, text=True, capture_output=True, timeout=timeout)

    def assert_clean_fixture(self, root: Path, source: Path) -> None:
        self.assertEqual(subprocess.run(["git", "status", "--short"], cwd=source, check=True, capture_output=True, text=True).stdout, "")
        self.assertFalse((source / ".camfly-sd-build.lock").exists())
        self.assertFalse((source / "patches" / "kernel" / "zz-camfly-sd-read-only.patch").exists())
        self.assertFalse((source / "atomcam_tools-sd-ro.zip").exists())

    def test_success_and_builder_failure_cleanup(self):
        temporary, root, env = self.fixture()
        self.addCleanup(temporary.cleanup)
        success = self.run_build(env)
        self.assertEqual(success.returncode, 0, success.stderr)
        release = root / "artifacts" / "fixture-release"
        self.assertTrue((release / f"atomcam_tools-sd-ro-{env['CAMFLY_SD_EXPECTED_SOURCE_COMMIT']}.zip").exists())
        self.assert_clean_fixture(root, Path(env["CAMFLY_SD_SOURCE_DIR"]))

        release_snapshot = {
            path.name: path.read_bytes()
            for path in release.iterdir()
            if path.is_file()
        }
        collision = self.run_build(env)
        self.assertNotEqual(collision.returncode, 0)
        self.assertEqual(
            release_snapshot,
            {path.name: path.read_bytes() for path in release.iterdir() if path.is_file()},
        )

        output_collision = root / "artifacts" / "output-collision.zip"
        output_collision.write_bytes(b"keep-output")
        output_result = self.run_build(
            env
            | {
                "CAMFLY_SD_RELEASE_ID": "output-collision",
                "CAMFLY_SD_OUTPUT": str(output_collision),
            }
        )
        self.assertNotEqual(output_result.returncode, 0)
        self.assertEqual(output_collision.read_bytes(), b"keep-output")
        self.assertFalse((root / "artifacts" / "output-collision").exists())
        self.assert_clean_fixture(root, Path(env["CAMFLY_SD_SOURCE_DIR"]))

        failure = self.run_build(env | {"CAMFLY_SD_RELEASE_ID": "failure-release"}, mode="build-failure")
        self.assertNotEqual(failure.returncode, 0)
        self.assertFalse((root / "artifacts" / "failure-release").exists())
        self.assert_clean_fixture(root, Path(env["CAMFLY_SD_SOURCE_DIR"]))

    def test_signal_cleanup_and_existing_artifact_are_protected(self):
        temporary, root, env = self.fixture()
        self.addCleanup(temporary.cleanup)
        existing = root / "artifacts" / "existing-sentinel"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"keep")
        process_env = os.environ.copy()
        process_env.update(env | {"CAMFLY_SD_RELEASE_ID": "signal-release", "CAMFLY_FAKE_MODE": "signal-block"})
        process = subprocess.Popen(
            ["bash", str(BUILD)],
            cwd=ROOT,
            env=process_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        lock = Path(env["CAMFLY_SD_SOURCE_DIR"]) / ".camfly-sd-build.lock"
        for _ in range(100):
            if lock.exists() and (root / "builder-started").exists():
                break
            time.sleep(0.01)
        os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 143, f"stdout={stdout!r} stderr={stderr!r}")
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assert_clean_fixture(root, Path(env["CAMFLY_SD_SOURCE_DIR"]))

    def test_lock_competition_fails_before_release_directory_creation(self):
        temporary, root, env = self.fixture()
        self.addCleanup(temporary.cleanup)
        process_env = os.environ.copy()
        process_env.update(env | {"CAMFLY_SD_RELEASE_ID": "first-release", "CAMFLY_FAKE_MODE": "signal-block"})
        first = subprocess.Popen(
            ["bash", str(BUILD)],
            cwd=ROOT,
            env=process_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        lock = Path(env["CAMFLY_SD_SOURCE_DIR"]) / ".camfly-sd-build.lock"
        for _ in range(100):
            if lock.exists():
                break
            time.sleep(0.01)
        second = self.run_build(env | {"CAMFLY_SD_RELEASE_ID": "second-release"}, mode="build-failure")
        self.assertNotEqual(second.returncode, 0)
        self.assertFalse((root / "artifacts" / "second-release").exists())
        os.killpg(first.pid, signal.SIGTERM)
        first.communicate(timeout=5)
        self.assert_clean_fixture(root, Path(env["CAMFLY_SD_SOURCE_DIR"]))


if __name__ == "__main__":
    unittest.main()
