import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { Parser } from "htmlparser2";
import { z } from "zod";

const PAGE_SIZE = "100";
const ITEM_PAGE_SIZE = "1000";
const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;
const STATE_ORDER = { started: 0, unstarted: 1, backlog: 2, completed: 3, cancelled: 4 };
const BLOCK_TAGS = new Set(["br", "div", "p", "blockquote", "table"]);

function unauthorized() {
  return new Response(JSON.stringify({ error: "Unauthorized" }), {
    status: 401,
    headers: { "Content-Type": "application/json", "WWW-Authenticate": "Bearer", "Cache-Control": "no-store" },
  });
}

function bearerToken(request) {
  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([^\s,]+)$/i.exec(authorization);
  return match && match[1].length <= 8192 && /^[\x21-\x7e]+$/.test(match[1]) ? match[1] : "";
}

function configFrom(env) {
  const base = new URL(env.PLANE_BASE_URL || "https://plane.apetechs.co/api/v1");
  if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash) {
    throw new Error("PLANE_BASE_URL must be a plain HTTPS URL");
  }
  const workspace = String(env.PLANE_WORKSPACE_SLUG || "apetechs");
  if (!/^[a-zA-Z0-9-]+$/.test(workspace)) throw new Error("Invalid Plane workspace slug");
  return { api: base.toString().replace(/\/$/, ""), workspace };
}

function configForUser(config, userProjects) {
  if (!Array.isArray(userProjects) || !userProjects.length) {
    throw new Error("Update the local Plane MCP so it sends your configured project UUIDs to this Worker");
  }
  const projects = {};
  const ids = new Set();
  const identifiers = new Set();
  for (const project of userProjects) {
    const { key, id, identifier, name = key } = project || {};
    if (!/^[A-Za-z][A-Za-z0-9]*$/.test(key || "") ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id || "") ||
        !/^[A-Za-z][A-Za-z0-9]*$/.test(identifier || "") || projects[key] || ids.has(id.toLowerCase()) ||
        identifiers.has(identifier.toUpperCase())) {
      throw new Error("Invalid or duplicate project in this user's local Plane MCP config");
    }
    ids.add(id.toLowerCase());
    identifiers.add(identifier.toUpperCase());
    projects[key] = { id: id.toLowerCase(), identifier, name };
  }
  return { ...config, projects };
}

function apiUrl(config, path, query = {}) {
  const url = new URL(`${config.api}/`);
  url.pathname += path.replace(/^\/+/, "");
  for (const [key, value] of Object.entries(query)) {
    if (value !== "" && value !== undefined && value !== null) url.searchParams.set(key, String(value));
  }
  return url;
}

async function planeJson(config, token, path, query = {}) {
  const response = await fetch(apiUrl(config, path, query), {
    headers: { "X-API-Key": token, Accept: "application/json", "User-Agent": "plane-mcp/1.0" },
    redirect: "manual",
  });
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`Plane API HTTP ${response.status}${detail ? `: ${detail}` : ""}`);
  }
  return response.json();
}

async function fetchPages(config, token, path, { pageSize = PAGE_SIZE, filters = {} } = {}) {
  const results = [];
  let cursor = "";
  let offset = 0;
  let mode = "cursor";
  const seen = new Set();
  for (let pageNo = 0; pageNo < 100; pageNo += 1) {
    const query = mode === "cursor"
      ? { ...filters, per_page: pageSize, ...(cursor ? { cursor } : {}) }
      : { ...filters, limit: pageSize, offset };
    const payload = await planeJson(config, token, path, query);
    if (Array.isArray(payload)) return results.concat(payload);
    if (!payload || !Array.isArray(payload.results)) throw new Error(`Plane API returned an invalid page for ${path}`);
    results.push(...payload.results);
    if (payload.next_page_results !== true) {
      if (Number.isInteger(payload.total_results) && payload.total_results > results.length) {
        throw new Error(`Plane API truncated results for ${path}: got ${results.length} of ${payload.total_results}`);
      }
      return results;
    }
    if (!payload.results.length) throw new Error(`Plane API pagination did not advance for ${path}`);
    if (payload.next_cursor) {
      if (seen.has(payload.next_cursor)) throw new Error(`Plane API repeated a cursor for ${path}`);
      seen.add(payload.next_cursor);
      cursor = payload.next_cursor;
      mode = "cursor";
    } else {
      offset = results.length;
      mode = "offset";
    }
  }
  throw new Error(`Plane API exceeded 100 pages for ${path}`);
}

async function fetchItems(config, token, projectId, fields = "") {
  const path = `workspaces/${config.workspace}/projects/${projectId}/work-items/`;
  try {
    return [...new Map((await fetchPages(config, token, path, {
      pageSize: ITEM_PAGE_SIZE,
      filters: fields ? { fields } : {},
    })).map((item) => [String(item.id), item])).values()];
  } catch (error) {
    if (!String(error.message).includes("Plane API HTTP 400")) throw error;
    return [...new Map((await fetchPages(config, token, path, {
      pageSize: PAGE_SIZE,
      filters: fields ? { fields } : {},
    })).map((item) => [String(item.id), item])).values()];
  }
}

function configuredProjects(config, requested = "") {
  const value = requested.trim();
  if (!value) throw new Error("Specify a project key/identifier, or configure the default project locally");
  if (value.toLowerCase() === "all") {
    return Object.entries(config.projects).map(([key, project]) => ({ key, ...project }));
  }
  const terms = new Set(value.split(",").map((part) => part.trim().toLowerCase()).filter(Boolean));
  const selected = Object.entries(config.projects)
    .filter(([key, project]) => [key, project.id, project.identifier, project.name || key].some((field) => terms.has(String(field).toLowerCase())))
    .map(([key, project]) => ({ key, ...project }));
  if (selected.length !== terms.size) throw new Error("Requested project is not in this user's configured project allowlist");
  return selected;
}

async function fetchConfiguredProjects(config, token, projects) {
  return Promise.all(projects.map(async (project) => {
    const live = await planeJson(config, token, `workspaces/${config.workspace}/projects/${project.id}/`);
    if (String(live.id).toLowerCase() !== project.id || String(live.identifier || "").toUpperCase() !== project.identifier.toUpperCase()) {
      throw new Error(`Plane project metadata does not match configured project ${project.identifier}`);
    }
    return { ...live, key: project.key };
  }));
}

function projectForTask(config, taskIdentifier) {
  const code = taskIdentifier.trim().toUpperCase();
  const match = /^([A-Z][A-Z0-9]*)-(\d+)$/.exec(code);
  if (!match) throw new Error("task_identifier must be a full Plane code such as PROJECT-123");
  const project = Object.entries(config.projects).find(([, value]) => value.identifier.toUpperCase() === match[1]);
  if (!project) throw new Error(`Task prefix ${match[1]} is not in this user's configured project allowlist`);
  return { code, sequence: Number(match[2]), key: project[0], ...project[1] };
}

async function fetchStates(config, token, projectId) {
  const rows = await fetchPages(config, token, `workspaces/${config.workspace}/projects/${projectId}/states/`);
  return Object.fromEntries(rows.map((state) => [String(state.id), { name: state.name || "Unknown", group: state.group || "unknown" }]));
}

async function fetchMembers(config, token) {
  return fetchPages(config, token, `workspaces/${config.workspace}/members/`);
}

function assigneeName(member) {
  const user = member.member || member;
  return user.display_name || `${user.first_name || ""} ${user.last_name || ""}`.trim() || user.email || "Unknown";
}

function assigneeFilterIds(members, filter) {
  if (filter.toLowerCase() === "all") return null;
  return new Set(members.filter((member) => {
    const user = member.member || member;
    return [user.id, user.display_name, user.email, `${user.first_name || ""} ${user.last_name || ""}`.trim()]
      .some((name) => String(name || "").toLowerCase() === filter.toLowerCase());
  }).map((member) => String((member.member || member).id)));
}

function formatAssignees(ids, members) {
  if (!ids?.length) return "Unassigned";
  const byId = new Map(members.map((member) => {
    const user = member.member || member;
    const name = assigneeName(member);
    return [String(user.id), user.email && !name.includes(user.email) ? `${name} (${user.email})` : name];
  }));
  return ids.map((id) => byId.get(String(id)) || String(id)).join(", ");
}

function htmlMarkdown(html) {
  const parts = [];
  const images = [];
  const links = [];
  let skipDepth = 0;
  let preDepth = 0;
  const parser = new Parser({
    onopentag(tag, attrs) {
      if (tag === "script" || tag === "style") { skipDepth += 1; return; }
      if (skipDepth) return;
      if (tag === "img" || tag === "image-component") {
        if (attrs.src) {
          const index = parts.length;
          parts.push(null);
          images.push({ index, source: attrs.src.trim(), alt: (attrs.alt || "Plane image").trim() || "Plane image" });
        }
      } else if (tag === "a") {
        links.push(attrs.href || "");
        if (attrs.href) parts.push("[");
      } else if (tag === "tr") parts.push("| ");
      else if (tag === "li") parts.push("\n• ");
      else if (/^h[1-6]$/.test(tag)) parts.push(`\n${"#".repeat(Number(tag[1]))} `);
      else if (tag === "b" || tag === "strong") parts.push("**");
      else if (tag === "i" || tag === "em") parts.push("*");
      else if (tag === "pre") { preDepth += 1; parts.push("\n```\n"); }
      else if (tag === "code" && !preDepth) parts.push("`");
      else if (BLOCK_TAGS.has(tag)) parts.push("\n");
    },
    ontext(text) { if (!skipDepth) parts.push(text); },
    onclosetag(tag) {
      if ((tag === "script" || tag === "style") && skipDepth) { skipDepth -= 1; return; }
      if (skipDepth) return;
      if (tag === "a" && links.length) {
        const target = links.pop();
        if (target) parts.push(`](${target})`);
      } else if (tag === "td" || tag === "th") parts.push(" | ");
      else if (tag === "b" || tag === "strong") parts.push("**");
      else if (tag === "i" || tag === "em") parts.push("*");
      else if (tag === "pre") { preDepth = Math.max(0, preDepth - 1); parts.push("\n```\n"); }
      else if (tag === "code" && !preDepth) parts.push("`");
      else if (BLOCK_TAGS.has(tag) || tag === "tr" || tag === "li" || /^h[1-6]$/.test(tag)) parts.push("\n");
    },
  }, { decodeEntities: true, lowerCaseTags: true, lowerCaseAttributeNames: true });
  parser.write(html || "");
  parser.end();
  return { parts, images, render: () => markdownLines(parts) };
}

function markdownLines(parts) {
  let inCode = false;
  return parts.filter((part) => part !== null).join("").split(/\r?\n/).map((line) => {
    if (line.trim().startsWith("```")) { inCode = !inCode; return line.trim(); }
    return inCode ? line : line.replace(/\u00a0/g, " ").replace(/[ \t]+/g, " ").trim();
  }).join("\n").trim();
}

function itemBasePath(config, projectId, itemId) {
  return `workspaces/${config.workspace}/projects/${projectId}/work-items/${itemId}/attachments/`;
}

function safeAssetURL(config, projectId, itemId, source) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(projectId) ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(itemId)) {
    throw new Error("Invalid Plane project or task ID");
  }
  const itemBase = apiUrl(config, itemBasePath(config, projectId, itemId));
  let url;
  if (/^[0-9a-f-]{16,}$/i.test(source)) url = new URL(encodeURIComponent(source), itemBase);
  else url = new URL(source, `${config.api}/`);
  if (url.origin !== new URL(config.api).origin || url.username || url.password) throw new Error("External image URL blocked; use a Plane attachment");
  const decoded = decodeURIComponent(url.pathname);
  if (decoded.split("/").some((part) => part === "." || part === "..")) throw new Error("Unsafe image path");
  if (!decoded.startsWith(itemBase.pathname)) throw new Error("Image URL is outside the task attachment endpoint");
  return url;
}

function dataImage(source) {
  const match = /^data:(image\/(?:png|jpeg|gif|webp|bmp));base64,([A-Za-z0-9+/=]+)$/i.exec(source);
  if (!match) return null;
  // shortcut: inline data images above 128 KiB are skipped to stay within Workers Free CPU, raise after real-task benchmarks.
  if (match[2].length > 4 * Math.ceil((128 * 1024) / 3)) return null;
  return { type: "image", mimeType: match[1].toLowerCase(), data: match[2] };
}

function assetLink(origin, projectId, itemId, source, name, mimeType = "application/octet-stream") {
  const url = new URL("/assets", origin);
  url.searchParams.set("project_id", projectId);
  url.searchParams.set("item_id", itemId);
  url.searchParams.set("source", source);
  url.searchParams.set("name", name);
  url.searchParams.set("mime_type", mimeType);
  return { type: "resource_link", uri: url.toString(), name, title: name, mimeType, description: "Plane task attachment; fetched by the local MCP bridge" };
}

async function taskImages(config, token, origin, projectId, itemId, html, filenamePrefix) {
  const rendered = htmlMarkdown(html);
  const content = [];
  const imageIds = new Set();
  for (const image of rendered.images) {
    if (image.source.startsWith("data:")) {
      const block = dataImage(image.source);
      if (block) {
        rendered.parts[image.index] = `\n![${image.alt}](inline image)\n`;
        content.push(block);
      } else {
        rendered.parts[image.index] = `\n[Image unavailable: ${image.alt} (inline image too large or unsupported)]\n`;
      }
      continue;
    }
    try {
      safeAssetURL(config, projectId, itemId, image.source);
      imageIds.add(image.source);
      const mime = image.source.match(/\.(png|jpe?g|gif|webp|bmp)(?:[?#]|$)/i)?.[1];
      const mimeType = mime ? `image/${mime.toLowerCase().replace("jpg", "jpeg")}` : "image/*";
      const link = assetLink(origin, projectId, itemId, image.source, `${filenamePrefix}-${imageIds.size}: ${image.alt}`, mimeType);
      rendered.parts[image.index] = `\n![${image.alt}](${link.uri})\n`;
      content.push(link);
    } catch (error) {
      rendered.parts[image.index] = `\n[Image unavailable: ${image.alt} (${error.message})]\n`;
    }
  }
  return { markdown: markdownLines(rendered.parts), content, imageIds };
}

async function streamAsset(config, token, projectId, itemId, source) {
  let url = safeAssetURL(config, projectId, itemId, source);
  let apiOrigin = new URL(config.api).origin;
  let headers = { "X-API-Key": token, Accept: "*/*", "User-Agent": "plane-mcp/1.0" };
  for (let redirects = 0; redirects <= 4; redirects += 1) {
    const response = await fetch(url, { headers, redirect: "manual" });
    if (![301, 302, 303, 307, 308].includes(response.status)) {
      if (!response.ok) return new Response("Plane attachment unavailable", { status: response.status });
      const contentType = response.headers.get("Content-Type") || "application/octet-stream";
      const length = Number(response.headers.get("Content-Length"));
      if (Number.isFinite(length) && length > MAX_ATTACHMENT_BYTES) return new Response("Attachment exceeds 50 MiB", { status: 413 });
      let body = response.body;
      if (body) {
        let total = 0;
        body = body.pipeThrough(new TransformStream({
          transform(chunk, controller) {
            total += chunk.byteLength;
            if (total > MAX_ATTACHMENT_BYTES) throw new Error("Plane attachment exceeds 50 MiB");
            controller.enqueue(chunk);
          },
        }));
      }
      return new Response(body, {
        headers: { "Content-Type": contentType, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" },
      });
    }
    if (redirects === 4) return new Response("Too many Plane attachment redirects", { status: 502 });
    const location = response.headers.get("Location");
    if (!location) return new Response("Invalid Plane attachment redirect", { status: 502 });
    url = new URL(location, url);
    if (url.protocol !== "https:") return new Response("Unsafe Plane attachment redirect", { status: 502 });
    if (url.origin !== apiOrigin) headers = { Accept: "*/*", "User-Agent": "plane-mcp/1.0" };
  }
  return new Response("Plane attachment unavailable", { status: 502 });
}

async function taskDetail(config, token, origin, taskIdentifier, forceRefresh) {
  const project = projectForTask(config, taskIdentifier);
  const item = await planeJson(config, token, `workspaces/${config.workspace}/work-items/${encodeURIComponent(project.code)}/`);
  if (!item || Number(item.sequence_id) !== project.sequence || String(item.project).toLowerCase() !== project.id || !item.id ||
      typeof item.description_html !== "string" && item.description_html !== null) {
    throw new Error(`Plane API returned an invalid or mismatched task for ${project.code}`);
  }
  const itemId = String(item.id);
  if (!/^[0-9a-f-]{16,}$/i.test(itemId)) throw new Error("Plane API returned an invalid work item ID");
  const p = project.id;
  const prefix = `workspaces/${config.workspace}/projects/${p}/work-items/${itemId}/`;
  const [statesRows, members, labelsRows, links, comments, attachments] = await Promise.all([
    fetchPages(config, token, `workspaces/${config.workspace}/projects/${p}/states/`),
    fetchMembers(config, token),
    fetchPages(config, token, `workspaces/${config.workspace}/projects/${p}/labels/`),
    fetchPages(config, token, `${prefix}links/`),
    fetchPages(config, token, `${prefix}comments/`),
    fetchPages(config, token, `${prefix}attachments/`),
  ]);
  const states = Object.fromEntries(statesRows.map((state) => [String(state.id), { name: state.name || "Unknown", group: state.group || "unknown" }]));
  const state = states[String(item.state)] || { name: "Unknown", group: "unknown" };
  const labels = Object.fromEntries(labelsRows.map((label) => [String(label.id), label.name || "Unknown"]));
  const labelNames = (item.labels || []).map((id) => labels[String(id)] || String(id));
  const projectMembers = members.map((member) => ({ member: member.member || member }));
  const creatorId = String(item.created_by || "");
  const creator = projectMembers.find((member) => String(member.member.id) === creatorId);
  const description = await taskImages(config, token, origin, p, itemId, item.description_html || "", "description");
  const content = [...description.content];
  const commentsText = [];
  for (const [index, comment] of comments.entries()) {
    const rendered = await taskImages(config, token, origin, p, itemId, comment.comment_html || "", `comment-${index + 1}`);
    content.push(...rendered.content);
    if (!rendered.markdown) continue;
    const actorId = String(comment.actor || comment.created_by || "");
    const actor = projectMembers.find((member) => String(member.member.id) === actorId);
    const created = String(comment.created_at || "").slice(0, 19).replace("T", " ");
    commentsText.push(`💬 [${created} | ${actor ? assigneeName(actor) : "User"}]:\n${rendered.markdown}`);
  }
  const embeddedIds = new Set([...description.imageIds]);
  const attachmentLines = [];
  for (const [index, attachment] of attachments.entries()) {
    const attributes = attachment.attributes || {};
    const name = String(attributes.name || `Attachment ${index + 1}`);
    const mimeType = String(attributes.type || "");
    const size = attachment.size;
    const details = mimeType && size != null ? `${mimeType}, ${size} bytes` : mimeType || "size unknown";
    const id = String(attachment.id || "");
    if (!id) { attachmentLines.push(`- ${name} (${details}; unavailable: missing attachment id)`); continue; }
    if (mimeType.startsWith("image/") && embeddedIds.has(id)) { attachmentLines.push(`- ${name} (${details}; included above)`); continue; }
    if (mimeType.startsWith("image/")) {
      content.push(assetLink(origin, p, itemId, id, name, mimeType));
      attachmentLines.push(`- ${name} (${details}; downloaded by the local MCP bridge)`);
    } else {
      content.push(assetLink(origin, p, itemId, id, name, mimeType || "application/octet-stream"));
      attachmentLines.push(`- ${name} (${details}; downloaded by the local MCP bridge)`);
    }
  }
  const linkText = links.filter((link) => link.url).map((link) => `[${link.title || "Link"}](${link.url})`).join(", ");
  const text = [
    `#${item.sequence_id} — ${item.name || "N/A"}`,
    `State: ${state.name} | Priority: ${item.priority || "none"} | Labels: ${labelNames.join(", ") || "None"}`,
    `Assignees: ${formatAssignees(item.assignees || [], members)} | Creator: ${creator ? assigneeName(creator) : creatorId ? "Unknown" : "N/A"}`,
    `Start Date: ${item.start_date || "None"} | Target Date (Deadline): ${item.target_date || "None"}`,
    linkText ? `External Links: ${linkText}` : "",
    "Mô tả đầy đủ:",
    description.markdown || "(trống)",
    commentsText.length ? `${"=".repeat(60)}\n=== BÌNH LUẬN & ACCEPTANCE CRITERIA (Comments): ===\n${"=".repeat(60)}\n\n${commentsText.join("\n\n---\n\n")}` : "",
    attachmentLines.length ? `Attachments:\n${attachmentLines.join("\n")}` : "",
    forceRefresh ? "(force_refresh=True)" : "",
  ].filter(Boolean).join("\n\n");
  return { content: [{ type: "text", text }, ...content] };
}

async function listTasks(config, token, args) {
  config = configForUser(config, args.user_projects);
  const projects = configuredProjects(config, args.projects || "");
  const selected = await fetchConfiguredProjects(config, token, projects);
  const members = await fetchMembers(config, token);
  const assignee = (args.assignee || "").trim();
  const memberIds = assigneeFilterIds(members, assignee || "all");
  const itemsPerProject = await Promise.all(selected.map(async (project) => {
    const [stateRows, items] = await Promise.all([
      fetchPages(config, token, `workspaces/${config.workspace}/projects/${project.id}/states/`),
      fetchItems(config, token, project.id, `id,sequence_id,name,assignees,state,priority,target_date${args.query?.trim() ? ",description_html" : ""}`),
    ]);
    const states = Object.fromEntries(stateRows.map((state) => [String(state.id), { name: state.name || "Unknown", group: state.group || "unknown" }]));
    return items.map((item) => {
      const state = states[String(item.state)] || { name: "Unknown", group: "unknown" };
      return { ...item, state_name: state.name, group: state.group, project_identifier: project.identifier };
    })
      .filter((item) => !memberIds || (item.assignees || []).some((id) => memberIds.has(String(id))))
      .filter((item) => args.state || args.include_closed || ["unstarted", "started"].includes(item.group))
      .filter((item) => args.include_closed || !["completed", "cancelled"].includes(item.group));
  }));
  let items = itemsPerProject.flat();
  const query = (args.query || "").trim().toLowerCase();
  if (query) items = items.filter((item) => [item.sequence_id, item.name, item.description_html].some((value) => String(value || "").toLowerCase().includes(query)));
  const state = (args.state || "").trim().toLowerCase();
  if (state) items = items.filter((item) => state === String(item.group || "").toLowerCase() || String(item.state_name || "").toLowerCase().includes(state));
  const priority = (args.priority || "").trim().toLowerCase();
  if (priority) items = items.filter((item) => priority === String(item.priority || "").toLowerCase());
  items.sort((a, b) => (STATE_ORDER[a.group] ?? 9) - (STATE_ORDER[b.group] ?? 9) ||
    String(a.target_date || "9999").localeCompare(String(b.target_date || "9999")) || Number(b.sequence_id || 0) - Number(a.sequence_id || 0));
  const limit = Math.max(1, Math.min(Number(args.limit) || 50, 200));
  const result = items.slice(0, limit);
  const projectFilter = selected.map((project) => project.identifier).join(",");
  const labels = [`assignee=${assignee || "all"}`, `projects=${projectFilter}`];
  if (args.query) labels.push(`query='${args.query}'`);
  if (args.state) labels.push(`state='${args.state}'`);
  if (args.priority) labels.push(`priority='${args.priority}'`);
  if (args.force_refresh) labels.push("force_refresh=True");
  const accessNote = "";
  if (!result.length) return `Không có task phù hợp (${labels.join(", ")}).${accessNote}`;
  return `Plane tasks (${result.length}/${items.length}, ${labels.join(", ")})\n\n` +
    result.map((item) => `${item.project_identifier}-${item.sequence_id} | ${item.state_name} | ${item.name || "-"}`).join("\n\n");
}

function createServer(config, token, origin) {
  const server = new McpServer({ name: "Plane", version: "0.2.2" });
  server.registerTool("list_plane_projects", {
    description: "List the projects configured in the calling user's local Plane MCP.",
    inputSchema: { user_projects: z.array(z.object({ key: z.string(), id: z.string(), identifier: z.string(), name: z.string().optional() })).min(1) },
    annotations: { readOnlyHint: true },
  }, async ({ user_projects }) => {
    const userConfig = configForUser(config, user_projects);
    const visible = await fetchConfiguredProjects(userConfig, token, configuredProjects(userConfig, "all"));
    return { content: [{ type: "text", text: `Plane projects\n\n${visible.map((project) => `${project.identifier || "?"} | ${project.name || "-"} | ${project.id || "?"}`).join("\n")}` }] };
  });
  server.registerTool("list_plane_tasks", {
    description: "List Todo/In Progress tasks in configured projects. Use a full task code directly with get_plane_task when known.",
    inputSchema: {
      assignee: z.string().optional(), query: z.string().optional(), state: z.string().optional(),
      priority: z.string().optional(), include_closed: z.boolean().optional(), limit: z.number().optional(),
      force_refresh: z.boolean().optional(), projects: z.string().optional(),
      user_projects: z.array(z.object({ key: z.string(), id: z.string(), identifier: z.string(), name: z.string().optional() })).min(1),
    },
    annotations: { readOnlyHint: true },
  }, async (args) => ({ content: [{ type: "text", text: await listTasks(config, token, args) }] }));
  server.registerTool("get_plane_task", {
    description: "Read a task by full Plane code, including its description, comments, image attachments, and links.",
    inputSchema: {
      task_identifier: z.string(), force_refresh: z.boolean().optional(),
      user_projects: z.array(z.object({ key: z.string(), id: z.string(), identifier: z.string(), name: z.string().optional() })).min(1),
    },
    annotations: { readOnlyHint: true },
  }, async ({ task_identifier, force_refresh = false, user_projects }) => {
    const userConfig = configForUser(config, user_projects);
    return taskDetail(userConfig, token, origin, task_identifier, force_refresh);
  });
  return server;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/healthz" && request.method === "GET") return new Response("ok");
    const token = bearerToken(request);
    if (!token) return unauthorized();
    let config;
    try { config = configFrom(env); } catch (error) {
      return new Response(JSON.stringify({ error: error.message }), { status: 500, headers: { "Content-Type": "application/json" } });
    }
    if (url.pathname === "/assets" && request.method === "GET") {
      try {
        const projectId = url.searchParams.get("project_id") || "";
        const itemId = url.searchParams.get("item_id") || "";
        const source = url.searchParams.get("source") || "";
        if (!source || source.length > 4096) throw new Error("Invalid Plane asset source");
        return await streamAsset(config, token, projectId, itemId, source);
      } catch (error) {
        return new Response(error.message, { status: 400 });
      }
    }
    if (url.pathname !== "/mcp") return new Response("Not found", { status: 404 });
    const handler = createMcpHandler(() => createServer(config, token, url.origin));
    return handler.fetch(request, { authInfo: { token, clientId: "plane-mcp", scopes: [] } });
  },
};

export { bearerToken, configForUser, configFrom, fetchPages, htmlMarkdown, safeAssetURL, streamAsset };
