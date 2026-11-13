import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anyio
import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

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
    def test_remote_call_works_inside_a_running_event_loop(self):
        # FastMCP runs sync tools on the event loop; a bare anyio.run() there fails with "Already running asyncio".
        async def fake_remote(_url, _token, _name, _arguments):
            return SimpleNamespace(isError=False, content=[])

        async def inside_loop():
            with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
                 patch.object(plane_mcp, "_call_remote_tool", fake_remote), \
                 patch.object(plane_mcp, "get_api_token", return_value="loop-token"):
                return plane_mcp.call_remote_tool("list_plane_projects", {"force_refresh": True})

        self.assertFalse(anyio.run(inside_loop).isError)

    def test_cache_honours_per_entry_ttl(self):
        with patch.dict(os.environ, {"PLANE_API_TOKEN": "test-token"}), \
             patch.object(plane_mcp, "get_api_token", return_value="ttl-token"), \
             patch.object(plane_mcp.time, "monotonic", side_effect=[0, 0, 100, 100, 700, 700]):
            plane_mcp.set_cached("short", "a")  # default 60 s, set at t=0
            plane_mcp.set_cached("long", "b", plane_mcp.CACHE_TTL_METADATA)  # 600 s, set at t=0
            self.assertIsNone(plane_mcp.get_cached("short"))  # t=100: expired
            self.assertEqual(plane_mcp.get_cached("long"), "b")  # t=100: still fresh
            self.assertIsNone(plane_mcp.get_cached("long"))  # t=700: expired

    def test_default_assignee_uses_the_current_plane_user_id(self):
        user_id = "33333333-3333-4333-8333-333333333333"
        members = [{"member": {"id": user_id, "display_name": "Dev Name", "email": "dev@example.com"}}]
        self.assertEqual(plane_mcp.assignee_filter_ids(members, "", user_id), {user_id})
        self.assertEqual(plane_mcp.assignee_filter_ids(members, "dev@example.com"), {user_id})
        self.assertIsNone(plane_mcp.assignee_filter_ids(members, "all"))

    def test_remote_assignee_falls_back_to_detected_user_id(self):
        result = SimpleNamespace(isError=False, content=[])
        with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
             patch.object(plane_mcp, "DEFAULT_ASSIGNEE", ""), \
             patch.object(plane_mcp, "PLANE_DEFAULT_ASSIGNEE_ID", "33333333-3333-4333-8333-333333333333"), \
             patch.object(plane_mcp, "call_remote_tool", return_value=result) as call, \
             patch.object(plane_mcp, "remote_text", return_value="ok"):
            self.assertEqual(plane_mcp.list_plane_tasks(), "ok")
        self.assertEqual(call.call_args.args[1]["assignee"], "33333333-3333-4333-8333-333333333333")

    def test_remote_request_token_is_required_and_isolated(self):
        async def token_endpoint(_request):
            return PlainTextResponse(plane_mcp.get_api_token())

        app = Starlette(routes=[Route("/mcp", token_endpoint, methods=["POST"]), Route("/healthz", lambda _request: PlainTextResponse("ok"))])
        app.add_middleware(plane_mcp.PlaneBearerMiddleware)
        async def exercise_requests():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                return (
                    await client.get("/healthz"),
                    await client.post("/mcp"),
                    await client.post("/mcp", headers={"Authorization": "Bearer token-a"}),
                    await client.post("/mcp", headers={"Authorization": "Bearer token-b"}),
                )

        health, missing_auth, token_a, token_b = anyio.run(exercise_requests)
        self.assertEqual(health.text, "ok")
        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(token_a.text, "token-a")
        self.assertEqual(token_b.text, "token-b")
        self.assertEqual(plane_mcp.get_api_token(), "test-token")

    def test_remote_tokens_do_not_share_cache_or_worker_context(self):
        token_a = plane_mcp._request_api_token.set("token-a")
        try:
            plane_mcp.set_cached("same-key", "user-a")
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertEqual(plane_mcp.submit_with_context(executor, plane_mcp.get_api_token).result(), "token-a")
        finally:
            plane_mcp._request_api_token.reset(token_a)

    def test_remote_tool_results_use_user_scoped_cache_and_force_refresh(self):
        token = plane_mcp._request_api_token.set("cache-user-token")
        first = SimpleNamespace(isError=False, content=[])
        refreshed = SimpleNamespace(isError=False, content=[])
        try:
            with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
                 patch.object(plane_mcp, "anyio") as anyio_mock:
                anyio_mock.run.side_effect = [first, refreshed]
                args = {"assignee": "all", "force_refresh": False}
                self.assertIs(plane_mcp.call_remote_tool("list_plane_tasks", args), first)
                self.assertIs(plane_mcp.call_remote_tool("list_plane_tasks", args), first)
                self.assertEqual(anyio_mock.run.call_count, 1)

                self.assertIs(plane_mcp.call_remote_tool("list_plane_tasks", {**args, "force_refresh": True}), refreshed)
                self.assertEqual(anyio_mock.run.call_count, 2)
                self.assertIs(plane_mcp.call_remote_tool("list_plane_tasks", args), refreshed)
                self.assertEqual(anyio_mock.run.call_count, 2)
        finally:
            plane_mcp._request_api_token.reset(token)

        token_b = plane_mcp._request_api_token.set("token-b")
        try:
            self.assertIsNone(plane_mcp.get_cached("same-key"))
            plane_mcp.set_cached("same-key", "user-b")
        finally:
            plane_mcp._request_api_token.reset(token_b)

        token_a = plane_mcp._request_api_token.set("token-a")
        try:
            self.assertEqual(plane_mcp.get_cached("same-key"), "user-a")
        finally:
            plane_mcp._request_api_token.reset(token_a)

    def test_plane_api_uses_the_remote_callers_token(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok": true}'
        token = plane_mcp._request_api_token.set("remote-token")
        try:
            with patch.object(plane_mcp.urllib.request, "urlopen", return_value=response) as urlopen:
                self.assertEqual(plane_mcp.api_get("health/"), {"ok": True})
            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_header("X-api-key"), "remote-token")
        finally:
            plane_mcp._request_api_token.reset(token)

    def test_default_and_explicit_project_selection_are_allowlisted(self):
        self.assertEqual([p["key"] for p in plane_mcp.configured_projects()], ["alpha"])
        self.assertEqual([p["key"] for p in plane_mcp.configured_projects("beta")], ["beta"])
        self.assertEqual({p["key"] for p in plane_mcp.configured_projects("all")}, {"alpha", "beta"})
        with self.assertRaisesRegex(ValueError, "allowlist"):
            plane_mcp.configured_projects("other")

    def test_remote_project_selection_preserves_each_users_allowlist(self):
        self.assertEqual(plane_mcp.remote_project_filter(""), "DEMOALPHA")
        self.assertEqual(plane_mcp.remote_project_filter("beta"), "DEMOBETA")
        self.assertEqual(set(plane_mcp.remote_project_filter("all").split(",")), {"DEMOALPHA", "DEMOBETA"})
        self.assertEqual(plane_mcp.configured_projects("all"), [
            {"key": "alpha", **CONFIG["projects"]["alpha"]},
            {"key": "beta", **CONFIG["projects"]["beta"]},
        ])
        result = SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text="Plane projects\n\nDEMOALPHA | Demo Alpha | id-a\nDEMOBETA | Demo Beta | id-b")])
        with patch.object(plane_mcp, "PLANE_PROJECTS", {"alpha": CONFIG["projects"]["alpha"]}):
            self.assertEqual(plane_mcp.remote_project_listing(result), "Plane projects\n\nDEMOALPHA | Demo Alpha | id-a")

    def test_remote_task_images_are_fetched_only_from_configured_worker(self):
        project_id = CONFIG["projects"]["alpha"]["id"]
        uri = (
            "https://mcp.example/assets?project_id=" + project_id
            + "&item_id=22222222-2222-4222-8222-222222222222"
            + "&source=33333333-3333-4333-8333-333333333333&name=image.png&mime_type=image%2Fpng"
        )
        result = SimpleNamespace(isError=False, content=[
            SimpleNamespace(type="text", text="Task details"),
            SimpleNamespace(type="resource_link", uri=uri, mimeType="image/png"),
        ])
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"Content-Type": "image/png", "Content-Length": "3"}
        response.read.return_value = b"png"
        opener = Mock()
        opener.open.return_value = response

        with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
             patch.object(plane_mcp, "PLANE_PROJECTS", {"alpha": CONFIG["projects"]["alpha"]}), \
             patch.object(plane_mcp, "get_api_token", return_value="user-token"), \
             patch.object(plane_mcp, "build_opener", return_value=opener):
            content = plane_mcp.remote_task_content(result)

        self.assertEqual(content[0], "Task details")
        self.assertEqual(content[1].data, b"png")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, uri)
        self.assertEqual(request.get_header("Authorization"), "Bearer user-token")

    def test_remote_task_image_rejects_unconfigured_hosts_and_projects(self):
        result = SimpleNamespace(isError=False, content=[SimpleNamespace(
            type="resource_link",
            uri="https://attacker.example/assets?project_id=11111111-1111-4111-8111-111111111111&item_id=22222222-2222-4222-8222-222222222222&source=33333333-3333-4333-8333-333333333333",
            mimeType="image/png",
        )])
        with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
             patch.object(plane_mcp, "PLANE_PROJECTS", {"alpha": CONFIG["projects"]["alpha"]}), \
             patch.object(plane_mcp, "build_opener") as build_opener:
            with self.assertRaisesRegex(ValueError, "outside its configured asset endpoint"):
                plane_mcp.remote_task_content(result)
        build_opener.assert_not_called()

        result.content[0].uri = result.content[0].uri.replace("attacker.example", "mcp.example")
        result.content[0].uri = result.content[0].uri.replace("11111111-1111-4111-8111-111111111111", "99999999-9999-4999-8999-999999999999")
        with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
             patch.object(plane_mcp, "PLANE_PROJECTS", {"alpha": CONFIG["projects"]["alpha"]}):
            with self.assertRaisesRegex(ValueError, "outside this user's configured project allowlist"):
                plane_mcp.remote_task_content(result)

    def test_remote_non_image_attachments_are_saved_under_a_safe_local_path(self):
        import re

        uri = (
            "https://mcp.example/assets?project_id=11111111-1111-4111-8111-111111111111"
            "&item_id=22222222-2222-4222-8222-222222222222&source=33333333-3333-4333-8333-333333333333"
        )
        result = SimpleNamespace(isError=False, content=[SimpleNamespace(
            type="resource_link", uri=uri, mimeType="application/pdf", name="../../report.pdf",
        )])
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.headers = {"Content-Type": "application/pdf", "Content-Length": "3"}
        response.read.return_value = b"pdf"
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
             patch.object(plane_mcp, "PLANE_PROJECTS", {"alpha": CONFIG["projects"]["alpha"]}), \
             patch.object(plane_mcp, "ROOT", Path(directory)), \
             patch.object(plane_mcp, "get_api_token", return_value="user-token"), \
             patch.object(plane_mcp, "build_opener", return_value=SimpleNamespace(open=lambda *_args, **_kwargs: response)):
            content = plane_mcp.remote_task_content(result)
            match = re.search(r"→ (.+)$", content[0])
            self.assertIsNotNone(match)
            path = Path(match.group(1))
            self.assertEqual(path.read_bytes(), b"pdf")
            self.assertEqual(path.name, "attachment-1.pdf")

    def test_remote_task_code_is_checked_against_local_allowlist(self):
        self.assertEqual(plane_mcp.project_for_task_identifier("demoalpha-759")[0], "DEMOALPHA-759")
        with self.assertRaisesRegex(ValueError, "not in this user's configured project allowlist"):
            plane_mcp.project_for_task_identifier("UNKNOWN-759")

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

    def test_remote_project_listing_sends_this_users_project_uuids(self):
        result = SimpleNamespace(isError=False, content=[SimpleNamespace(
            type="text", text=f"Plane projects\n\nDEMOALPHA | Demo Alpha | {CONFIG['projects']['alpha']['id']}"
        )])
        with patch.object(plane_mcp, "REMOTE_MCP_URL", "https://mcp.example/mcp"), \
             patch.object(plane_mcp, "call_remote_tool", return_value=result) as call:
            self.assertIn("DEMOALPHA", plane_mcp.list_plane_projects())
        self.assertEqual(call.call_args.args[0], "list_plane_projects")
        self.assertEqual(call.call_args.args[1]["user_projects"], plane_mcp.configured_projects("all"))


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class CreateTaskWithImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.calls = []

        def fake_api_send(method, path, payload):
            self.calls.append((method, path.split("/projects/")[-1], payload))
            if method == "POST" and path.endswith("/work-items/"):
                return {"id": "item-1", "sequence_id": 7}
            if method == "POST":
                return {"asset_id": f"asset-{len(self.calls)}", "upload_data": {"url": "https://storage.example/up", "fields": {"key": "k"}}}
            return {}

        self.send = patch.object(plane_mcp, "api_send", side_effect=fake_api_send)
        self.storage = patch.object(plane_mcp, "post_to_storage")
        self.send.start()
        self.post_to_storage = self.storage.start()

    def tearDown(self):
        self.send.stop()
        self.storage.stop()
        self.temp.cleanup()

    def image(self, name="a.png", data=PNG):
        path = self.dir / name
        path.write_bytes(data)
        return str(path)

    def test_creates_the_task_then_uploads_every_image_in_order(self):
        result = plane_mcp.create_plane_task("Fix login", "Line one\nLine <two>", "alpha", "high", [self.image("a.png"), self.image("b.png")])

        self.assertIn("Created DEMOALPHA-7: Fix login", result)
        self.assertIn("Images attached: 2/2", result)
        self.assertEqual([call[0] for call in self.calls], ["POST", "POST", "PATCH", "POST", "PATCH"])
        self.assertEqual(self.calls[0][2]["description_html"], "<p>Line one</p><p>Line &lt;two&gt;</p>")
        self.assertEqual(self.calls[0][2]["priority"], "high")
        self.assertEqual(self.calls[1][2], {"name": "a.png", "type": "image/png", "size": len(PNG)})
        self.assertEqual(self.post_to_storage.call_count, 2)

    def test_a_bad_image_stops_everything_before_the_first_write(self):
        with self.assertRaisesRegex(ValueError, "Not an image file"):
            plane_mcp.create_plane_task("Task", "", "alpha", "none", [self.image(), str(self.dir / "missing.png")])
        self.assertEqual(self.calls, [])

    def test_a_renamed_non_image_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not a real image/png"):
            plane_mcp.create_plane_task("Task", "", "alpha", "none", [self.image("fake.png", b"just text, not a png")])
        self.assertEqual(self.calls, [])

    def test_a_symlink_to_another_file_is_rejected(self):
        secret = self.dir / "secret.txt"
        secret.write_text("private")
        (self.dir / "looks-like.png").symlink_to(secret)
        with self.assertRaisesRegex(ValueError, "Not an image file"):
            plane_mcp.load_image(str(self.dir / "looks-like.png"))

    def test_a_failed_upload_is_reported_and_the_task_is_still_reported_created(self):
        self.post_to_storage.side_effect = OSError("storage down")
        result = plane_mcp.create_plane_task("Task", "", "alpha", "none", [self.image()])

        self.assertIn("Created DEMOALPHA-7", result)
        self.assertIn("Images attached: 0/1", result)
        self.assertIn("a.png: storage down", result)

    def test_exactly_one_project_and_a_known_priority_are_required(self):
        with self.assertRaisesRegex(ValueError, "exactly one project"):
            plane_mcp.create_plane_task("Task", "", "all")
        with self.assertRaisesRegex(ValueError, "priority"):
            plane_mcp.create_plane_task("Task", "", "alpha", "asap")
        self.assertEqual(self.calls, [])

    def test_assignee_is_resolved_to_one_member_before_any_write(self):
        user_id = "33333333-3333-4333-8333-333333333333"
        members = [{"member": {"id": user_id, "display_name": "Dev Name", "email": "dev@example.com"}},
                   {"member": {"id": "44444444-4444-4444-8444-444444444444", "display_name": "Other", "email": "o@example.com"}}]
        with patch.object(plane_mcp, "fetch_members", return_value=members):
            plane_mcp.create_plane_task("Task", "", "alpha", assignee="dev@example.com")
            self.assertEqual(self.calls[0][2]["assignees"], [user_id])
            self.calls.clear()
            for bad in ("nobody", "all"):
                with self.assertRaisesRegex(ValueError, "exactly one workspace member"):
                    plane_mcp.create_plane_task("Task", "", "alpha", assignee=bad)
        self.assertEqual(self.calls, [])

    def test_the_new_task_shows_up_in_the_next_list(self):
        project_id = CONFIG["projects"]["alpha"]["id"]
        plane_mcp.set_cached(f"items_{project_id}_all", ["stale"])
        plane_mcp.create_plane_task("Task", "", "alpha")
        self.assertIsNone(plane_mcp.get_cached(f"items_{project_id}_all"))

    def test_attach_images_to_an_existing_task(self):
        item = {"project": CONFIG["projects"]["alpha"]["id"], "id": "item-9"}
        with patch.object(plane_mcp, "find_item", return_value=(item, {})):
            result = plane_mcp.attach_images_to_plane_task("demoalpha-9", [self.image()])
        self.assertEqual(result, "DEMOALPHA-9\nImages attached: 1/1")
        with self.assertRaisesRegex(ValueError, "at least one"):
            plane_mcp.attach_images_to_plane_task("DEMOALPHA-9", [])

    def test_multipart_puts_the_form_fields_first_and_the_file_last(self):
        body, content_type = plane_mcp.multipart_body({"key": "k1", "policy": "p"}, 'a"b.png', "image/png", PNG)
        text = body.decode("latin-1")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertLess(text.index('name="key"'), text.index('name="policy"'))
        self.assertLess(text.index('name="policy"'), text.index('name="file"'))
        self.assertIn('filename="ab.png"', text)
        self.assertTrue(text.rstrip().endswith("--"))


if __name__ == "__main__":
    unittest.main()
