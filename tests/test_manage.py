import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plane_mcp_manage", ROOT / "manage.py")
manage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manage
SPEC.loader.exec_module(manage)


class UpdateTests(unittest.TestCase):
    def test_latest_tag_uses_semantic_version_order(self):
        result = subprocess.CompletedProcess([], 0, "v1.10.0\nv1.9.0\nv2.0.0-rc.1\nv1.10.0\n", "")
        with patch.object(manage, "run", return_value=result):
            self.assertEqual(manage.latest_tag(), "v1.10.0")

    def test_skip_persists_until_a_new_release(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text('{"projects": {"demo": {}}}')
            fetch_result = subprocess.CompletedProcess([], 0, "", "")
            tags_result = subprocess.CompletedProcess([], 0, "v0.2.0\nv0.1.0\n", "")
            with patch.object(manage, "CONFIG_PATH", config_path), \
                 patch.object(manage, "run", side_effect=[fetch_result, tags_result]), \
                 patch.object(manage.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v0.1.0", "")), \
                 patch.object(manage, "input", create=True, return_value="s"):
                manage.update()
            self.assertEqual(json.loads(config_path.read_text())["skipped_version"], "v0.2.0")


if __name__ == "__main__":
    unittest.main()
