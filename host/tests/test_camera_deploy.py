from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from host.camera_deploy import (
    CameraDeployer,
    DeployError,
    FakeRemoteRunner,
    load_bundle,
    make_stage_payload,
)


class CameraDeployTests(unittest.TestCase):
    def make_bundle(self, root: Path, release: str = "v1") -> Path:
        bundle = root / release
        (bundle / "bin").mkdir(parents=True)
        script = b"#!/bin/sh\nprintf '%s\\n' v1\n"
        (bundle / "bin/version.sh").write_bytes(script)
        manifest = {
            "type": "runtime",
            "target_model": "atomcam1",
            "release_id": release,
            "entrypoint": "bin/version.sh",
            "files": [
                {
                    "path": "bin/version.sh",
                    "size": len(script),
                    "sha256": hashlib.sha256(script).hexdigest(),
                }
            ],
        }
        (bundle / "manifest.json").write_text(json.dumps(manifest))
        return bundle

    def test_stage_activate_rollback_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            v1 = load_bundle(self.make_bundle(root, "v1"))
            v2 = load_bundle(self.make_bundle(root, "v2"))
            runner = FakeRemoteRunner()
            deployer = CameraDeployer(runner, expected_mac="00:11:22:33:44:55")
            self.assertEqual(deployer.stage(v1), "STAGED")
            self.assertEqual(deployer.stage(v1), "IDEMPOTENT")
            self.assertEqual(deployer.activate("v1"), "ACTIVATED")
            self.assertEqual(deployer.stage(v2), "STAGED")
            self.assertEqual(deployer.activate("v2"), "ACTIVATED")
            self.assertEqual(deployer.status()["ACTIVE"], "v2")
            self.assertEqual(deployer.rollback(), "ROLLED_BACK")
            self.assertEqual(deployer.status()["ACTIVE"], "v1")
            self.assertTrue(make_stage_payload(v1))

    def test_wrong_identity_and_capacity_fail_before_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = load_bundle(self.make_bundle(root))
            wrong = CameraDeployer(FakeRemoteRunner(mac="aa:bb:cc:dd:ee:ff"), expected_mac="00:11:22:33:44:55")
            with self.assertRaises(DeployError):
                wrong.stage(bundle)
            small = CameraDeployer(FakeRemoteRunner(free_kib=1), expected_mac="00:11:22:33:44:55")
            with self.assertRaises(DeployError):
                small.stage(bundle)

    def test_missing_remote_hash_tool_fails_preflight(self):
        class NoHashRunner(FakeRemoteRunner):
            def run(self, action, args=(), payload=b""):
                result = super().run(action, args, payload)
                if action == "inspect":
                    return result.replace("HASH_TOOL=sha256sum", "HASH_TOOL=")
                return result

        deployer = CameraDeployer(NoHashRunner(), expected_mac="00:11:22:33:44:55")
        with self.assertRaises(DeployError):
            deployer.inspect()

    def test_manifest_rejects_traversal_and_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle_path = self.make_bundle(root)
            manifest = json.loads((bundle_path / "manifest.json").read_text())
            manifest["files"][0]["path"] = "../escape"
            (bundle_path / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(DeployError):
                load_bundle(bundle_path)


if __name__ == "__main__":
    unittest.main()
