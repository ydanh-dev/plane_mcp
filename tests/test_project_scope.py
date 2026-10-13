import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONFIG = {
    "base_url": "https://plane.example/api/v1",
    "workspace_slug": "test",
    "display_name": "Test User",
    "default_project": "alpha",
    "projects": {
        "alpha": {"id": "11111111-1111-4111-8111-111111111111", "identifier": "DEMOALPHA", "name": "Demo Alpha"},
        "beta": {"id": "22222222-2222-4222-8222-222222222222", "identifier": "DEMOBETA", "name": "Demo Beta"},
    },
}
TEMP = tempfile.TemporaryDirectory()
CONFIG_PATH = Path(TEMP.name) / "config.json"
CONFIG_PATH.write_text(json.dumps(CONFIG))
with patch.dict(os.environ, {"PLANE_MCP_CONFIG": str(CONFIG_PATH), "PLANE_API_TOKEN": "test-token"}):
    plane_mcp = importlib.import_module("plane_mcp")


class ProjectScopeTests(unittest.TestCase):
    def test_default_and_explicit_project_selection_are_allowlisted(self):
        self.assertEqual([p["key"] for p in plane_mcp.configured_projects()], ["alpha"])
        self.assertEqual([p["key"] for p in plane_mcp.configured_projects("beta")], ["beta"])
        self.assertEqual({p["key"] for p in plane_mcp.configured_projects("all")}, {"alpha", "beta"})
        with self.assertRaisesRegex(ValueError, "allowlist"):
            plane_mcp.configured_projects("other")

    def test_task_prefix_routes_to_configured_project(self):
        with patch.object(plane_mcp, "api_get", return_value={
            "sequence_id": 759, "project": CONFIG["projects"]["alpha"]["id"],
            "id": "item-759", "description_html": "", "state": "state-id",
        }), patch.object(plane_mcp, "get_cached", return_value=None), \
             patch.object(plane_mcp, "set_cached", side_effect=lambda _key, value: value), \
             patch.object(plane_mcp, "fetch_states", return_value={"state-id": {"name": "Todo", "group": "unstarted"}}):
            item, _ = plane_mcp.find_item("DEMOALPHA-759", force_refresh=True)
        self.assertEqual(item["id"], "item-759")

    def test_task_outside_configured_project_is_rejected_before_cache(self):
        with patch.object(plane_mcp, "api_get", return_value={
            "sequence_id": 759, "project": CONFIG["projects"]["beta"]["id"],
            "id": "wrong-project", "description_html": "",
        }), patch.object(plane_mcp, "get_cached", return_value=None), \
             patch.object(plane_mcp, "set_cached") as set_cached:
            with self.assertRaisesRegex(ValueError, "does not belong"):
                plane_mcp.find_item("DEMOALPHA-759", force_refresh=True)
        set_cached.assert_not_called()

    def test_unknown_task_prefix_does_not_call_plane(self):
        with patch.object(plane_mcp, "api_get") as api_get:
            with self.assertRaisesRegex(ValueError, "not in this user's configured project allowlist"):
                plane_mcp.find_item("UNKNOWN-1")
        api_get.assert_not_called()

    def test_project_listing_only_returns_configured_projects(self):
        plane_mcp.fetch_projects = Mock(return_value=[
            {"id": CONFIG["projects"]["alpha"]["id"], "identifier": "DEMOALPHA", "name": "Demo Alpha"},
            {"id": "unconfigured", "identifier": "SECRET", "name": "Not configured"},
        ])
        output = plane_mcp.list_plane_projects()
        self.assertIn("DEMOALPHA", output)
        self.assertNotIn("SECRET", output)


if __name__ == "__main__":
    unittest.main()
