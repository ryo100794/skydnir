import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "scripts" / "verify-image-pull-crash-safety.py"
class ImagePullCrashSafetyVerifierTest(unittest.TestCase):
    def test_static_verifier_passes(self):
        subprocess.run([sys.executable, str(VERIFY)], cwd=ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
