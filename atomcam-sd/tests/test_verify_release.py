from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_release import VerificationError, verify_archive  # noqa: E402


class VerifyReleaseTests(unittest.TestCase):
    def make_archive(self, root: Path, *, extra: list[tuple[str, bytes]] | None = None) -> Path:
        archive = root / "release.zip"
        files = {
            "factory_t31_ZMC6tiIDQN": b"\x27\x05\x19\x56" + b"k" * 32,
            "rootfs_hack.squashfs": b"hsqs" + b"r" * 32,
            "hostname": b"atomcam\n",
            "authorized_keys": b"ssh-ed25519 AAAA secret-material\n",
        }
        with zipfile.ZipFile(archive, "w") as output:
            for name, data in files.items():
                output.writestr(name, data)
            for name, data in extra or []:
                output.writestr(name, data)
        return archive

    def test_valid_archive_has_redacted_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch = root / "patch.diff"
            patch.write_text("patch")
            manifest = verify_archive(
                self.make_archive(root),
                release_id="test-1",
                source_commit="abc",
                patch_sha256={"patch.diff": hashlib.sha256(b"patch").hexdigest()},
            )
            self.assertTrue(manifest["checks"]["crc"])
            self.assertEqual(manifest["files"]["hostname"]["size"], 8)
            self.assertNotIn("secret-material", str(manifest))

    def test_extra_and_duplicate_members_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(VerificationError):
                verify_archive(self.make_archive(root, extra=[("../../escape", b"x")]), release_id="x")
            duplicate = root / "duplicate.zip"
            valid = self.make_archive(root)
            with zipfile.ZipFile(valid, "r") as input_zip, zipfile.ZipFile(duplicate, "w") as output:
                for info in input_zip.infolist():
                    output.writestr(info.filename, input_zip.read(info))
                output.writestr("hostname", b"other")
            with self.assertRaises(VerificationError):
                verify_archive(duplicate, release_id="x")

    def test_bad_magic_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.make_archive(root)
            broken = root / "broken.zip"
            with zipfile.ZipFile(archive, "r") as input_zip, zipfile.ZipFile(broken, "w") as output:
                for info in input_zip.infolist():
                    data = input_zip.read(info)
                    if info.filename == "rootfs_hack.squashfs":
                        data = b"nope" + data[4:]
                    output.writestr(info.filename, data)
            with self.assertRaises(VerificationError):
                verify_archive(broken, release_id="x")

    def test_builder_source_evidence_is_recorded_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rootfs = root / "rootfs"
            (rootfs / "etc/init.d").mkdir(parents=True)
            (rootfs / "atom_patch/sbin").mkdir(parents=True)
            (rootfs / "etc/init.d/rcS").write_text("mtd_readonly_guard")
            (rootfs / "etc/init.d/S16fwupdate").write_text("disabled in the SD read-only build")
            (rootfs / "etc/init.d/S20mountfs").write_text("mount -t squashfs -o ro")
            (rootfs / "atom_patch/sbin/flash_erase").write_text(
                "disabled in camfly SD read-only build"
            )
            kernel = root / "kernel"
            kernel.mkdir()
            (kernel / ".config").write_text(
                'mtdparts=jz_sfc:256K(boot)ro,1984K(kernel)ro,3904K(rootfs)ro,'
                '3904K(app)ro,1984K(kback)ro,3904K(aback)ro,384K(cfg)ro,64K(para)ro'
            )
            source = kernel / "jz_sfc.c"
            source.write_text("flash->mtd.flags = MTD_CAP_NORFLASH & ~MTD_WRITEABLE;")
            manifest = verify_archive(
                self.make_archive(root),
                release_id="evidence",
                rootfs_dir=rootfs,
                kernel_config=kernel / ".config",
                kernel_source=source,
            )
            self.assertTrue(manifest["source_evidence"]["all_checked_passed"])


if __name__ == "__main__":
    unittest.main()
