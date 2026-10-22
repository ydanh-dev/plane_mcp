import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
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


class SetupTests(unittest.TestCase):
    def test_project_metadata_uses_configured_plane_endpoint(self):
        project_id = "e82f2243-a973-4dfd-b2f0-005b44f4fbf5"
        response = SimpleNamespace(read=lambda: b'{"id":"e82f2243-a973-4dfd-b2f0-005b44f4fbf5","identifier":"NTSS","name":"NTSS v2"}')
        with patch.object(manage.urllib.request, "urlopen", return_value=nullcontext(response)) as urlopen:
            project = manage.fetch_project_metadata("secret-token", project_id)

        self.assertEqual(project, {"id": project_id, "identifier": "NTSS", "name": "NTSS v2"})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{manage.PLANE_BASE_URL}/workspaces/apetechs/projects/{project_id}/")
        self.assertEqual(request.get_header("X-api-key"), "secret-token")

    def test_setup_uses_fixed_plane_and_reads_project_metadata(self):
        project_id = "e82f2243-a973-4dfd-b2f0-005b44f4fbf5"
        project = {"id": project_id, "identifier": "NTSS", "name": "NTSS v2"}
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            credential_store = SimpleNamespace(
                get_keyring=lambda: SimpleNamespace(priority=1),
                set_password=lambda *_args: None,
            )
            with patch.object(manage, "CONFIG_PATH", config_path), \
                 patch.object(manage, "load_config", return_value={"remote_mcp_url": "https://mcp.example.com/mcp"}), \
                 patch.object(manage, "fetch_project_metadata", return_value=project) as fetch, \
                 patch.object(manage, "input", create=True, side_effect=["Dev Name", project_id, "", ""]), \
                 patch.object(manage.getpass, "getpass", return_value="secret-token"), \
                 patch.dict(sys.modules, {"keyring": credential_store}), \
                 redirect_stdout(StringIO()):
                manage.setup()

            config = json.loads(config_path.read_text())
            self.assertEqual(config["base_url"], "https://plane.apetechs.co/api/v1")
            self.assertEqual(config["workspace_slug"], "apetechs")
            self.assertEqual(config["display_name"], "Dev Name")
            self.assertEqual(config["projects"], {"ntss": project})
            self.assertEqual(config["remote_mcp_url"], "https://mcp.example.com/mcp")
            self.assertNotIn("secret-token", config_path.read_text())
            fetch.assert_called_once_with("secret-token", project_id)

    def test_remote_url_requires_https_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text('{"projects": {"demo": {}}}')
            with patch.object(manage, "CONFIG_PATH", config_path):
                with self.assertRaises(SystemExit):
                    manage.set_remote_url("http://mcp.example.com/mcp")
                manage.set_remote_url("https://mcp.example.com/mcp")
                self.assertEqual(json.loads(config_path.read_text())["remote_mcp_url"], "https://mcp.example.com/mcp")
                manage.set_remote_url("off")
                self.assertNotIn("remote_mcp_url", json.loads(config_path.read_text()))


if __name__ == "__main__":
    unittest.main()
