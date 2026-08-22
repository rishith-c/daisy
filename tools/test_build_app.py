import os
import plistlib
import subprocess
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "Daisy.app")


class DaisyBundleTests(unittest.TestCase):
    def test_build_produces_signed_self_contained_bundle(self):
        result = subprocess.run(
            ["bash", os.path.join(ROOT, "tools", "build_app.sh")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

        contents = os.path.join(APP, "Contents")
        resources = os.path.join(contents, "Resources")
        required = [
            os.path.join(contents, "MacOS", "Daisy"),
            os.path.join(resources, "index.html"),
            os.path.join(resources, "AppIcon.icns"),
            os.path.join(resources, "agents", "discover.py"),
            os.path.join(resources, "garden", "link.py"),
            os.path.join(resources, "port", "client.py"),
            os.path.join(resources, "scrape", "cli.py"),
            os.path.join(resources, "obs", "otlp.py"),
        ]
        self.assertTrue(all(os.path.isfile(path) for path in required), required)
        self.assertFalse(any("__pycache__" in path for path, _, _ in os.walk(resources)))

        with open(os.path.join(contents, "Info.plist"), "rb") as fh:
            info = plistlib.load(fh)
        self.assertEqual("Daisy", info["CFBundleExecutable"])
        self.assertEqual("com.rishith.daisy", info["CFBundleIdentifier"])

        verify = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", APP],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, verify.returncode, verify.stdout + verify.stderr)


if __name__ == "__main__":
    unittest.main()
