import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).absolute().parents[1]
NODE = os.environ.get("CHROME_COMPAT_TEST_NODE") or shutil.which("node.exe") or shutil.which("node")


@unittest.skipUnless(NODE, "Node is required; set CHROME_COMPAT_TEST_NODE or add Node to PATH")
class HelperTests(unittest.TestCase):
    def test_observable_policy_contract(self):
        (ROOT / "temp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="helper-", dir=ROOT / "temp") as directory:
            directory = Path(directory)
            helper = (ROOT / "identification-helper.mjs").read_text(encoding="utf-8")
            helper = helper.replace("__CCP_CONTROL_PATH_JSON__", json.dumps(str(directory / "control.json")))
            helper += "\nexport { __ccpIdentificationPolicy_20260930 as factory };\n"
            module = directory / "subject.mjs"
            module.write_text(helper, encoding="utf-8")
            completed = subprocess.run([str(NODE), str(ROOT / "tests" / "helper_contract.mjs"), str(module),
                                        str(directory / "control.json")], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            self.assertGreaterEqual(result["assertions"], 35)


if __name__ == "__main__":
    unittest.main()
