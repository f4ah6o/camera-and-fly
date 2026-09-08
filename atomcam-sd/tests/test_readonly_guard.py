"""Exercise the patched startup guard against synthetic sysfs trees."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ReadonlyGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        rc = Path('overlay_rootfs/etc/init.d/rcS')
        target = self.root / rc
        target.parent.mkdir(parents=True)
        target.write_bytes((ROOT / 'vendor/atomcam_tools' / rc).read_bytes())
        subprocess.run(
            ['git', 'apply', '--include=' + str(rc),
             str(ROOT / 'atomcam-sd/patches/0001-source-read-only.patch')],
            cwd=self.root, check=True, capture_output=True,
        )
        patched = target.read_text()
        begin = patched.index('mtd_readonly_guard()')
        end = patched.index('\n# Start all init scripts', begin)
        # Only redirect the fixed sysfs path, preserving production guard logic
        # and startup rejection. No service scripts or hardware are accessed.
        self.script = patched[begin:end].replace('/sys/class/mtd', str(self.root / 'mtd'))
        for index in range(8):
            self.flags(index).parent.mkdir(parents=True)
            self.flags(index).write_text('0x800\n')

    def flags(self, index):
        return self.root / 'mtd' / f'mtd{index}' / 'flags'

    def run_guard(self):
        result = subprocess.run(
            [os.environ.get('CAMFLY_TEST_SHELL', '/bin/sh')],
            input=self.script, text=True, capture_output=True, timeout=5,
        )
        return result.returncode

    def test_all_partitions_readonly(self):
        self.assertEqual(self.run_guard(), 0)

    def test_each_writable_partition_rejected(self):
        for index in range(8):
            with self.subTest(index=index):
                self.flags(index).write_text('0xc00\n')
                self.assertNotEqual(self.run_guard(), 0)
                self.flags(index).write_text('0x800\n')

    def test_each_missing_partition_rejected(self):
        for index in range(8):
            with self.subTest(index=index):
                self.flags(index).unlink()
                self.assertNotEqual(self.run_guard(), 0)
                self.flags(index).write_text('0x800\n')

    def test_read_failure_rejected(self):
        self.flags(3).unlink()
        self.flags(3).mkdir()
        self.assertNotEqual(self.run_guard(), 0)

    def test_malformed_flags_rejected(self):
        for value in ('', '0x', '0x800junk', '0x800\n0x800', '-1', '2048',
                      '0x100000000', '0x800 + 0', '0x$(false)'):
            with self.subTest(value=value):
                self.flags(0).write_text(value)
                self.assertNotEqual(self.run_guard(), 0)

    def test_uppercase_digits_accepted(self):
        self.flags(0).write_text('0xA00\n')
        self.assertEqual(self.run_guard(), 0)

    def test_unexpected_device_rejected(self):
        self.flags(8).parent.mkdir()
        self.flags(8).write_text('0x800\n')
        self.assertNotEqual(self.run_guard(), 0)


if __name__ == '__main__':
    unittest.main()
