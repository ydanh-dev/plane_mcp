import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import worker, { bearerToken, configForUser, configFrom, fetchPages, safeAssetURL } from "./index.js";

const projectId = "11111111-1111-4111-8111-111111111111";
const userProjectId = "44444444-4444-4444-8444-444444444444";
const itemId = "22222222-2222-4222-8222-222222222222";
const env = {
  PLANE_BASE_URL: "https://plane.example/api/v1",
  PLANE_WORKSPACE_SLUG: "test",
};
const userProjects = [{ key: "demo", id: projectId, identifier: "DEMO", name: "Demo Project" }];
const originalFetch = globalThis.fetch;

afterEach(() => { globalThis.fetch = originalFetch; });

async function callTool(name, args) {
  const { user_projects = userProjects, ...toolArgs } = args;
  const response = await worker.fetch(new Request("https://mcp.example/mcp", {
    method: "POST",
    headers: { authorization: "Bearer test-token", "content-type": "application/json", accept: "application/json, text/event-stream" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 7, method: "tools/call", params: { name, arguments: { ...toolArgs, user_projects } } }),
  }), env);
  const event = (await response.text()).split("\n").find((line) => line.startsWith("data: "));
  assert.ok(event, "MCP response contains an SSE data event");
  return JSON.parse(event.slice(6));
}

test("does not require a Worker-wide project allowlist and validates user projects", () => {
  assert.equal(configFrom(env).workspace, "test");
  assert.deepEqual(configForUser(configFrom(env), userProjects).projects.demo, {
    id: projectId, identifier: "DEMO", name: "Demo Project",
  });
  assert.throws(() => configForUser(configFrom(env), []), /Update the local Plane MCP/i);
  assert.throws(() => configForUser(configFrom(env), [userProjects[0], userProjects[0]]), /duplicate project/i);
  assert.throws(() => configFrom({ ...env, PLANE_BASE_URL: "http://plane.example/api/v1" }), /HTTPS/);
});

test("accepts only a single bounded bearer token", () => {
  assert.equal(bearerToken(new Request("https://mcp.example/mcp", { headers: { authorization: "Bearer token" } })), "token");
  assert.equal(bearerToken(new Request("https://mcp.example/mcp", { headers: { authorization: "Bearer token,other" } })), "");
  assert.equal(bearerToken(new Request("https://mcp.example/mcp")), "");
});

test("restricts image URLs to the current task attachment path", () => {
  const config = configFrom(env);
  const url = safeAssetURL(config, projectId, itemId, "33333333-3333-4333-8333-333333333333");
  assert.match(url.pathname, new RegExp(`/projects/${projectId}/work-items/${itemId}/attachments/`));
  assert.throws(() => safeAssetURL(config, projectId, itemId, "https://other.example/image.png"), /External image URL blocked/);
  assert.throws(() => safeAssetURL(config, projectId, itemId, "../comments/secret"), /outside the task attachment endpoint/);
  assert.throws(() => safeAssetURL(config, "../../projects", itemId, "33333333-3333-4333-8333-333333333333"), /Invalid Plane project or task ID/);
});

test("follows Plane cursor pages and rejects a repeated cursor", async () => {
  const config = configFrom(env);
  let call = 0;
  globalThis.fetch = async () => {
    call += 1;
    return Response.json(call === 1
      ? { results: [{ id: 1 }], next_page_results: true, next_cursor: "next" }
      : { results: [{ id: 2 }], next_page_results: false });
  };
  assert.deepEqual(await fetchPages(config, "secret", "items/"), [{ id: 1 }, { id: 2 }]);

  globalThis.fetch = async () => Response.json({ results: [{ id: 1 }], next_page_results: true, next_cursor: "same" });
  await assert.rejects(fetchPages(config, "secret", "items/"), /repeated a cursor/);
});

test("requires auth for MCP and lists only the configured tools over Streamable HTTP", async () => {
  const unauthenticated = await worker.fetch(new Request("https://mcp.example/mcp", { method: "POST" }), env);
  assert.equal(unauthenticated.status, 401);

  const response = await worker.fetch(new Request("https://mcp.example/mcp", {
    method: "POST",
    headers: { authorization: "Bearer test-token", "content-type": "application/json", accept: "application/json, text/event-stream" },
    body: JSON.stringify({
      jsonrpc: "2.0", id: 1, method: "tools/list", params: {},
    }),
  }), env);
  assert.equal(response.status, 200);
  const body = await response.text();
  assert.match(body, /event: message/);
  assert.match(body, /list_plane_projects/);
  assert.match(body, /list_plane_tasks/);
  assert.match(body, /get_plane_task/);
});

test("fetches a known task directly and returns a safe image resource link", async () => {
  const paths = [];
  globalThis.fetch = async (input) => {
    const url = new URL(input);
    paths.push(url.pathname);
    if (url.pathname.endsWith("/work-items/DEMO-759/")) {
      return Response.json({
        id: itemId, sequence_id: 759, project: projectId, state: "state-1", name: "Task title",
        priority: "high", assignees: [], description_html: '<p>See <img src="33333333-3333-4333-8333-333333333333" alt="evidence" /></p>',
      });
    }
    if (url.pathname.endsWith("/states/")) return Response.json({ results: [{ id: "state-1", name: "In Progress", group: "started" }], next_page_results: false });
    if (url.pathname.endsWith("/members/")) return Response.json({ results: [], next_page_results: false });
    if (url.pathname.endsWith("/labels/")) return Response.json({ results: [], next_page_results: false });
    return Response.json({ results: [], next_page_results: false });
  };
  const response = await callTool("get_plane_task", { task_identifier: "DEMO-759" });
  assert.equal(response.result.isError, undefined);
  const [details, image] = response.result.content;
  assert.match(details.text, /#759 — Task title/);
  assert.equal(image.type, "resource_link");
  assert.match(image.uri, /^https:\/\/mcp\.example\/assets\?/);
  assert.ok(paths.some((path) => path.endsWith("/work-items/DEMO-759/")));
  assert.equal(paths.some((path) => /\/work-items\/$/.test(path)), false, "known task lookup does not scan work-item pages");
});

test("lists task title and state without scanning the workspace project list", async () => {
  const paths = [];
  globalThis.fetch = async (input) => {
    const url = new URL(input);
    paths.push(url.pathname);
    if (url.pathname.endsWith(`/projects/${projectId}/`)) return Response.json({ id: projectId, identifier: "DEMO", name: "Demo Project" });
    if (url.pathname.endsWith("/members/")) return Response.json({ results: [], next_page_results: false });
    if (url.pathname.endsWith("/states/")) return Response.json({ results: [{ id: "state-1", name: "In Progress", group: "started" }], next_page_results: false });
    if (url.pathname.endsWith("/work-items/")) return Response.json({
      results: [{ id: itemId, sequence_id: 759, name: "Task title", state: "state-1", priority: "high", assignees: [] }],
      next_page_results: false,
    });
    throw new Error(`Unexpected Plane API route: ${url.pathname}`);
  };
  const response = await callTool("list_plane_tasks", { assignee: "all", projects: "DEMO" });
  assert.match(response.result.content[0].text, /DEMO-759 \| In Progress \| Task title/);
  assert.equal(paths.some((path) => path.endsWith("/projects/")), false, "only configured project UUID is queried");
});

test("uses project UUIDs supplied by each local MCP caller", async () => {
  const paths = [];
  globalThis.fetch = async (input) => {
    const url = new URL(input);
    paths.push(url.pathname);
    if (url.pathname.endsWith(`/projects/${userProjectId}/`)) return Response.json({ id: userProjectId, identifier: "USERPROJECT", name: "User Project" });
    if (url.pathname.endsWith("/members/")) return Response.json({ results: [], next_page_results: false });
    if (url.pathname.endsWith("/states/")) return Response.json({ results: [{ id: "state-1", name: "Todo", group: "unstarted" }], next_page_results: false });
    if (url.pathname.endsWith("/work-items/")) return Response.json({
      results: [{ id: itemId, sequence_id: 12, name: "Private project task", state: "state-1", assignees: [] }],
      next_page_results: false,
    });
    throw new Error(`Unexpected Plane API route: ${url.pathname}`);
  };
  const response = await callTool("list_plane_tasks", {
    projects: "USERPROJECT",
    user_projects: [{ key: "custom", id: userProjectId, identifier: "USERPROJECT", name: "User Project" }],
    assignee: "all",
  });
  assert.match(response.result.content[0].text, /USERPROJECT-12 \| Todo \| Private project task/);
  assert.ok(paths.includes(`/api/v1/workspaces/test/projects/${userProjectId}/`));
  assert.ok(!paths.some((path) => path.includes(projectId)));
});

test("routes task details by the caller's project prefix and UUID", async () => {
  const paths = [];
  globalThis.fetch = async (input) => {
    const url = new URL(input);
    paths.push(url.pathname);
    if (url.pathname.endsWith("/work-items/USERPROJECT-12/")) {
      return Response.json({ id: itemId, sequence_id: 12, project: userProjectId, state: "state-1", name: "Caller task", assignees: [], description_html: "" });
    }
    if (url.pathname.endsWith("/states/")) return Response.json({ results: [{ id: "state-1", name: "Todo", group: "unstarted" }], next_page_results: false });
    if (url.pathname.endsWith("/members/") || url.pathname.endsWith("/labels/") || url.pathname.endsWith("/links/") ||
        url.pathname.endsWith("/comments/") || url.pathname.endsWith("/attachments/")) {
      return Response.json({ results: [], next_page_results: false });
    }
    throw new Error(`Unexpected Plane API route: ${url.pathname}`);
  };
  const response = await callTool("get_plane_task", {
    task_identifier: "USERPROJECT-12",
    user_projects: [{ key: "custom", id: userProjectId, identifier: "USERPROJECT", name: "User Project" }],
  });
  assert.match(response.result.content[0].text, /Caller task/);
  assert.ok(paths.includes(`/api/v1/workspaces/test/work-items/USERPROJECT-12/`));
  assert.ok(paths.some((path) => path.includes(`/projects/${userProjectId}/states/`)));
});

test("filters tasks by the authenticated Plane user ID", async () => {
  const userId = "33333333-3333-4333-8333-333333333333";
  globalThis.fetch = async (input) => {
    const url = new URL(input);
    if (url.pathname.endsWith(`/projects/${projectId}/`)) return Response.json({ id: projectId, identifier: "DEMO", name: "Demo Project" });
    if (url.pathname.endsWith("/members/")) return Response.json({ results: [{ member: { id: userId, display_name: "Dev Name" } }], next_page_results: false });
    if (url.pathname.endsWith("/states/")) return Response.json({ results: [{ id: "state-1", name: "Todo", group: "unstarted" }], next_page_results: false });
    if (url.pathname.endsWith("/work-items/")) return Response.json({
      results: [{ id: itemId, sequence_id: 759, name: "Assigned to me", state: "state-1", assignees: [userId] }],
      next_page_results: false,
    });
    throw new Error(`Unexpected Plane API route: ${url.pathname}`);
  };
  const response = await callTool("list_plane_tasks", { assignee: userId, projects: "DEMO" });
  assert.match(response.result.content[0].text, /DEMO-759 \| Todo \| Assigned to me/);
});

test("streams only images and never forwards Plane credentials to redirected storage", async () => {
  const config = configFrom(env);
  const sentHeaders = [];
  globalThis.fetch = async (_input, options) => {
    sentHeaders.push(options.headers);
    return sentHeaders.length === 1
      ? new Response(null, { status: 302, headers: { Location: "https://storage.example/signed" } })
      : new Response("image", { status: 200, headers: { "Content-Type": "image/png" } });
  };
  const response = await worker.fetch(new Request(
    `https://mcp.example/assets?project_id=${projectId}&item_id=${itemId}&source=33333333-3333-4333-8333-333333333333`,
    { headers: { authorization: "Bearer test-token" } },
  ), { ...env, PLANE_API_TOKEN: "must-not-forward" });
  assert.equal(response.status, 200);
  assert.equal(sentHeaders[0]["X-API-Key"], "test-token");
  assert.equal(sentHeaders[1]["X-API-Key"], undefined);
  assert.equal(response.headers.get("content-type"), "image/png");
  assert.equal(await response.text(), "image");
});

test("streams non-image attachments and rejects oversized files", async () => {
  const url = `https://mcp.example/assets?project_id=${projectId}&item_id=${itemId}&source=33333333-3333-4333-8333-333333333333`;
  globalThis.fetch = async () => new Response("pdf", { headers: { "Content-Type": "application/pdf" } });
  const response = await worker.fetch(new Request(url, { headers: { authorization: "Bearer test-token" } }), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Content-Type"), "application/pdf");
  assert.equal(await response.text(), "pdf");

  globalThis.fetch = async () => new Response(null, { headers: { "Content-Length": String(50 * 1024 * 1024 + 1) } });
  const tooLarge = await worker.fetch(new Request(url, { headers: { authorization: "Bearer test-token" } }), env);
  assert.equal(tooLarge.status, 413);
});
