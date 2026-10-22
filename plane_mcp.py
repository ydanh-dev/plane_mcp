#!/usr/bin/env python3
"""User-wide local MCP server for reading Plane work items."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import html
import json
import mimetypes
import os
import posixpath
import re
import shlex
import sys
import subprocess
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from contextvars import copy_context
from functools import partial
from uuid import UUID
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, unquote_to_bytes, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import anyio
from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse, PlainTextResponse

CONFIG_PATH = Path(os.environ.get("PLANE_MCP_CONFIG", Path.home() / ".config/plane-mcp/config.json"))
try:
    CONFIG_JSON = os.environ.get("PLANE_MCP_CONFIG_JSON", "")
    USER_CONFIG = json.loads(CONFIG_JSON) if CONFIG_JSON else json.loads(CONFIG_PATH.read_text())
except FileNotFoundError as error:
    raise RuntimeError(f"Plane MCP is not configured. Run `plane-mcp setup` ({CONFIG_PATH})") from error
except (OSError, json.JSONDecodeError) as error:
    raise RuntimeError(f"Cannot read Plane MCP config at {CONFIG_PATH}: {error}") from error

PLANE_BASE_URL = os.environ.get("PLANE_BASE_URL", USER_CONFIG.get("base_url", "https://plane.apetechs.co/api/v1"))
PLANE_WORKSPACE_SLUG = os.environ.get("PLANE_WORKSPACE_SLUG", USER_CONFIG.get("workspace_slug", "apetechs"))
PLANE_PROJECTS: dict[str, dict[str, str]] = USER_CONFIG.get("projects", {})
PLANE_DEFAULT_PROJECT = USER_CONFIG.get("default_project", "")
PLANE_DEFAULT_ASSIGNEE = os.environ.get("PLANE_DEFAULT_ASSIGNEE", USER_CONFIG.get("display_name", ""))
REMOTE_MCP_URL = str(os.environ.get("PLANE_MCP_REMOTE_URL", USER_CONFIG.get("remote_mcp_url", "")) or "").strip().rstrip("/")
HTTP_MODE = os.environ.get("PLANE_MCP_HTTP", "").lower() in {"1", "true", "yes"}
if not isinstance(PLANE_PROJECTS, dict) or not PLANE_PROJECTS:
    raise RuntimeError(f"At least one project must be configured in {CONFIG_PATH}")
for project_key, project in PLANE_PROJECTS.items():
    if not isinstance(project, dict) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", project_key):
        raise RuntimeError(f"Invalid project configuration for {project_key!r}")
    try:
        project["id"] = str(UUID(project["id"]))
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError(f"Project {project_key!r} must have a valid UUID in its id field") from error
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", project.get("identifier", "")):
        raise RuntimeError(f"Project {project_key!r} must have a valid Plane identifier")
if PLANE_DEFAULT_PROJECT and PLANE_DEFAULT_PROJECT not in PLANE_PROJECTS:
    raise RuntimeError("default_project must match a key in projects")
PLANE_PAGE_SIZE = "100"
PLANE_PAGINATION_MODE = "cursor"

def read_api_token() -> str:
    if os.environ.get("PLANE_API_TOKEN"):
        return os.environ["PLANE_API_TOKEN"]
    account = str(USER_CONFIG.get("credential_account", "plane-mcp"))
    try:
        import keyring
        token = keyring.get_password("plane-mcp", account)
    except Exception as error:
        raise RuntimeError("Cannot access the operating system credential store. Unlock/configure it, then run `plane-mcp setup`.") from error
    if not token:
        raise RuntimeError("Plane API token missing from the operating system credential store. Run `plane-mcp setup`.")
    return token


TOKEN = "" if HTTP_MODE else read_api_token()
_request_api_token: contextvars.ContextVar[str | None] = contextvars.ContextVar("plane_api_token", default=None)

CONFIG = {
    "PLANE_BASE_URL": PLANE_BASE_URL,
    "PLANE_WORKSPACE_SLUG": PLANE_WORKSPACE_SLUG,
    "PLANE_PROJECTS": PLANE_PROJECTS,
    "PLANE_DEFAULT_ASSIGNEE": PLANE_DEFAULT_ASSIGNEE,
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
# ponytail: bound in-memory downloads at 50 MiB; stream large attachments if needed.
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
BLOCK_TAGS = {"br", "div", "p", "blockquote", "table"}
STATE_GROUP_ORDER = {
    "started": 0,    # In Progress
    "unstarted": 1,  # Todo
    "backlog": 2,    # Backlog
    "completed": 3,  # Done
    "cancelled": 4,  # Cancelled
}


class SafeRedirectHandler(HTTPRedirectHandler):
    """Do not forward the Plane API key to a signed storage redirect."""

    def redirect_request(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        redirected = super().redirect_request(request, *args, **kwargs)
        if redirected is not None:
            original = urlparse(request.full_url)
            target = urlparse(redirected.full_url)
            if (original.scheme, original.netloc) != (target.scheme, target.netloc):
                for headers in (redirected.headers, redirected.unredirected_hdrs):
                    for name in list(headers):
                        if name.lower() == "x-api-key":
                            del headers[name]
        return redirected


def extension_for(content_type: str, source: str) -> str:
    mime = content_type.split(";", 1)[0].lower()
    extension = mimetypes.guess_extension(mime) or Path(urlparse(source).path).suffix
    return extension if extension and len(extension) <= 5 else ".bin"


class MarkdownRenderer(HTMLParser):
    """Preserve task text structure; image rendering is supplied by the caller."""

    def __init__(self, render_image: Callable[[str], str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str | None] = []
        self.render_image = render_image
        self.skip_depth = 0
        self.pre_depth = 0
        self.link_targets: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"img", "image-component"}:
            self.handle_image(attrs)
        elif tag == "a":
            target = dict(attrs).get("href")
            self.link_targets.append(target)
            if target:
                self.parts.append("[")
        elif tag == "tr":
            self.parts.append("| ")
        elif tag == "li":
            self.parts.append("\n• ")
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in {"b", "strong"}:
            self.parts.append("**")
        elif tag in {"i", "em"}:
            self.parts.append("*")
        elif tag == "pre":
            self.pre_depth += 1
            self.parts.append("\n```\n")
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "a" and self.link_targets:
            target = self.link_targets.pop()
            if target:
                self.parts.append(f"]({target})")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in {"b", "strong"}:
            self.parts.append("**")
        elif tag in {"i", "em"}:
            self.parts.append("*")
        elif tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            self.parts.append("\n```\n")
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
        elif tag in BLOCK_TAGS or tag in {"tr", "li"} or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def handle_image(self, attrs: list[tuple[str, str | None]]) -> None:
        if self.render_image:
            self.parts.append(self.render_image(self.get_starttag_text() or ""))

    def render(self) -> str:
        lines = []
        in_code = False
        for line in "".join(p for p in self.parts if p is not None).splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
                lines.append(line.strip())
            else:
                lines.append(line if in_code else re.sub(r"[ \t]+", " ", line.replace("\xa0", " ")).strip())
        return "\n".join(lines).strip()


class DescriptionRenderer(MarkdownRenderer):
    def __init__(self, asset_dir: Path, api_token: str, base_url: str, asset_url_prefix: str, filename_prefix: str = "image") -> None:
        super().__init__()
        self.asset_dir = asset_dir
        self.api_token = api_token
        self.base_url = base_url.rstrip("/") + "/"
        self.asset_url_prefix = asset_url_prefix.rstrip("/") + "/"
        self.filename_prefix = filename_prefix
        self.opener = build_opener(SafeRedirectHandler())
        self.image_index = 0
        self.pending_images: list[tuple[int, int, str, str]] = []

    def handle_image(self, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        source = (attributes.get("src") or "").strip()
        if source:
            self.image_index += 1
            alt = (attributes.get("alt") or "Plane image").strip() or "Plane image"
            parts_idx = len(self.parts)
            self.parts.append(None)
            self.pending_images.append((parts_idx, self.image_index, source, alt))

    def materialize_image(self, image_index: int, source: str, alt: str) -> str:
        try:
            raw, content_type = self.read_image(source)
            if len(raw) > MAX_IMAGE_BYTES:
                raise ValueError(f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MiB")

            suffix = extension_for(content_type, source)
            output = self.asset_dir / f"{self.filename_prefix}-{image_index}{suffix}"
            output.write_bytes(raw)
            return f"\n![{alt}]({output.resolve()})\n"
        except Exception as error:  # Keep the task readable if one image is unavailable.
            return f"\n[Image unavailable: {alt} ({error})]\n"

    def render(self) -> str:
        if self.pending_images:
            max_workers = min(5, len(self.pending_images))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_idx = {
                    submit_with_context(executor, self.materialize_image, img_idx, src, alt): parts_idx
                    for (parts_idx, img_idx, src, alt) in self.pending_images
                }
                for future in as_completed(future_to_idx):
                    parts_idx = future_to_idx[future]
                    self.parts[parts_idx] = future.result()
        return super().render()

    def read_image(self, source: str, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
        if source.startswith("data:"):
            header, payload = source.split(",", 1)
            content_type = header[5:].split(";", 1)[0] or "application/octet-stream"
            if ";base64" in header:
                if len(payload) > 4 * ((max_bytes + 2) // 3):
                    raise ValueError("inline asset exceeds size limit")
                raw = base64.b64decode(payload, validate=True)
            else:
                encoded = payload.encode("utf-8")
                decoded_size = len(encoded) - 2 * sum(1 for _ in re.finditer(rb"%[0-9a-fA-F]{2}", encoded))
                if decoded_size > max_bytes:
                    raise ValueError("inline asset exceeds size limit")
                raw = unquote_to_bytes(payload)
            if len(raw) > max_bytes:
                raise ValueError("inline asset exceeds size limit")
            return raw, content_type

        url = urljoin(self.asset_url_prefix if re.fullmatch(r"[0-9a-fA-F-]{16,}", source) else self.base_url, source)
        parsed_url = urlparse(url)
        parsed_base = urlparse(self.base_url)
        parsed_asset_base = urlparse(self.asset_url_prefix)
        if (parsed_url.scheme, parsed_url.netloc) != (parsed_base.scheme, parsed_base.netloc):
            raise ValueError("external image URL blocked; use a Plane attachment or data URL")
        decoded_path = unquote(parsed_url.path)
        if any(segment in {".", ".."} for segment in decoded_path.split("/")):
            raise ValueError("image URL contains an unsafe path segment")
        asset_path = posixpath.normpath(unquote(parsed_asset_base.path)).rstrip("/")
        normalized_path = posixpath.normpath(decoded_path)
        if normalized_path != asset_path and not normalized_path.startswith(f"{asset_path}/"):
            raise ValueError("image URL is outside the task attachment endpoint")
        headers = {"User-Agent": "plane-task-script/1.0"}
        if parsed_url.scheme == parsed_base.scheme and parsed_url.netloc == parsed_base.netloc:
            headers["X-API-Key"] = self.api_token
        request = Request(url, headers=headers)
        with self.opener.open(request, timeout=20) as response:
            content_type = response.headers.get("Content-Type", "application/octet-stream")
            return response.read(max_bytes + 1), content_type

    def materialize_attachment(self, index: int, source: str, name: str) -> str:
        try:
            raw, content_type = self.read_image(source, MAX_ATTACHMENT_BYTES)
            if len(raw) > MAX_ATTACHMENT_BYTES:
                raise ValueError("attachment exceeds 50 MiB")
            suffix = Path(name).suffix
            if not suffix or len(suffix) > 10:
                suffix = extension_for(content_type, name)
            output = self.asset_dir / f"attachment-{index}{suffix}"
            output.write_bytes(raw)
            return str(output.resolve())
        except Exception as error:
            return f"unavailable ({error})"


ROOT = Path(os.environ.get("PLANE_MCP_DATA", Path.home() / ".cache/plane-mcp"))


API = CONFIG["PLANE_BASE_URL"].rstrip("/")
WORKSPACE = CONFIG["PLANE_WORKSPACE_SLUG"]
DEFAULT_ASSIGNEE = CONFIG["PLANE_DEFAULT_ASSIGNEE"]

if not WORKSPACE or (not TOKEN and not HTTP_MODE):
    raise RuntimeError("Missing Plane workspace or credential configuration")


def get_api_token() -> str:
    request_token = _request_api_token.get()
    token = request_token if request_token is not None else TOKEN
    if not token:
        raise RuntimeError("Plane API credential is missing")
    return token


def _token_scope(token: str | None = None) -> str:
    return hashlib.sha256((token if token is not None else get_api_token()).encode()).hexdigest()


def submit_with_context(executor: ThreadPoolExecutor, function: Callable[..., Any], *args: Any, **kwargs: Any):
    """Carry the active per-request Plane token into worker threads."""
    return executor.submit(copy_context().run, partial(function, *args, **kwargs))


mcp = FastMCP(
    "Plane",
    host="0.0.0.0" if HTTP_MODE else "127.0.0.1",
    port=int(os.environ.get("PORT", "8080")) if HTTP_MODE else 8000,
    stateless_http=HTTP_MODE,
)


def api_get(path: str, query: dict[str, str] | None = None) -> Any:
    url = f"{API}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={"X-API-Key": get_api_token(), "Accept": "application/json", "User-Agent": "plane-mcp/1.0"},
    )
    retry_wait_remaining = 30.0
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code not in {429, 502, 503, 504} or attempt == 2:
                raise
            try:
                retry_after = error.headers.get("Retry-After", "1").strip()
                if re.fullmatch(r"[0-9]+", retry_after):
                    delay = float(retry_after)
                else:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(0.0, retry_at.timestamp() - time.time())
            except (AttributeError, TypeError, ValueError, OverflowError):
                delay = 1.0
            if delay > retry_wait_remaining:
                raise RuntimeError(
                    f"Plane API HTTP {error.code}: retry after {delay:.0f} seconds "
                    f"exceeds remaining retry wait budget ({retry_wait_remaining:.0f} seconds); try again later"
                ) from error
            time.sleep(delay)
            retry_wait_remaining -= delay
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise
            if retry_wait_remaining < 1:
                raise RuntimeError("Plane API retry wait budget exhausted; try again later") from error
            time.sleep(1)
            retry_wait_remaining -= 1
    raise RuntimeError("Plane API retry exhausted")


async def _call_remote_tool(url: str, token: str, name: str, arguments: dict[str, Any]) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        sse_read_timeout=300,
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(name, arguments)


def call_remote_tool(name: str, arguments: dict[str, Any]) -> Any:
    if not REMOTE_MCP_URL:
        raise RuntimeError("Remote Plane MCP URL is not configured")
    parsed = urlparse(REMOTE_MCP_URL)
    if (parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}) or (
        parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.endswith("/mcp")
    ):
        raise RuntimeError("Remote Plane MCP URL must be an HTTPS /mcp URL without embedded credentials or query parameters")
    return anyio.run(_call_remote_tool, REMOTE_MCP_URL, get_api_token(), name, arguments)


def remote_text(result: Any) -> str:
    texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    if result.isError:
        raise RuntimeError("\n".join(texts) or "Remote Plane MCP request failed")
    return "\n".join(texts)


def remote_project_listing(result: Any) -> str:
    text = remote_text(result)
    allowed = {project["identifier"].upper() for project in PLANE_PROJECTS.values()}
    rows = [
        line for line in text.splitlines()
        if "|" in line and line.split("|", 1)[0].strip().upper() in allowed
    ]
    if not rows:
        raise ValueError("None of this user's configured Plane projects are visible to this API user")
    return "Plane projects\n\n" + "\n".join(rows)


def remote_task_content(result: Any) -> list[Any]:
    content: list[Any] = []
    texts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
    if result.isError:
        raise RuntimeError("\n".join(texts) or "Remote Plane MCP request failed")
    if texts:
        content.append("\n".join(texts))
    for block in result.content:
        if getattr(block, "type", None) != "image" or not block.mimeType.startswith("image/"):
            continue
        format = block.mimeType.split("/", 1)[1]
        content.append(Image(data=base64.b64decode(block.data, validate=True), format=format))
    return content


def remote_project_filter(requested: str) -> str:
    if not requested.strip():
        keys = [PLANE_DEFAULT_PROJECT] if PLANE_DEFAULT_PROJECT else []
    elif requested.strip().casefold() == "all":
        keys = list(PLANE_PROJECTS)
    else:
        keys = []
        for value in requested.split(","):
            term = value.strip().casefold()
            if not term:
                continue
            project_key = next((
                key for key, project in PLANE_PROJECTS.items()
                if term in {key.casefold(), project["id"].casefold(), project["identifier"].casefold(), str(project.get("name", "")).casefold()}
            ), None)
            keys.append(project_key if project_key is not None else value.strip())
    return ",".join(PLANE_PROJECTS[key]["identifier"] if key in PLANE_PROJECTS else key for key in keys)


class PlaneBearerMiddleware:
    """Use each remote MCP caller's Plane token only for that request."""

    def __init__(self, app: Callable[..., Any]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "http" or scope.get("path") == "/healthz":
            await self.app(scope, receive, send)
            return
        values = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
        try:
            authorization = values[0].decode("ascii") if len(values) == 1 else ""
        except UnicodeDecodeError:
            authorization = ""
        scheme, separator, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not separator or not token or len(token) > 8192:
            response = JSONResponse({"error": "Unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return

        request_token = _request_api_token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _request_api_token.reset(request_token)


async def _health(_request: Any) -> PlainTextResponse:
    return PlainTextResponse("ok")


def run_http_server() -> None:
    import uvicorn

    app = mcp.streamable_http_app()
    app.add_route("/healthz", _health, methods=["GET"])
    app.add_middleware(PlaneBearerMiddleware)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), access_log=False)


CACHE_TTL = 60.0  # seconds
_cache: dict[str, tuple[float, Any]] = {}


def get_cached(key: str) -> Any | None:
    cache_key = f"{_token_scope()}:{key}"
    cached = _cache.get(cache_key)
    if cached is not None:
        timestamp, value = cached
        if time.monotonic() - timestamp < CACHE_TTL:
            return value
        _cache.pop(cache_key, None)
    return None


def set_cached(key: str, value: Any) -> Any:
    for expired_key, (timestamp, _) in list(_cache.items()):
        if time.monotonic() - timestamp >= CACHE_TTL:
            _cache.pop(expired_key, None)
    _cache[f"{_token_scope()}:{key}"] = (time.monotonic(), value)
    return value


def fetch_pages(path: str, mode: str = "cursor", page_size: str = PLANE_PAGE_SIZE, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    cursor = ""
    offset = 0
    seen_cursors: set[str] = set()
    for _ in range(100):
        if mode == "cursor":
            params = {"per_page": page_size}
            if cursor:
                params["cursor"] = cursor
        else:
            params = {"limit": page_size, "offset": str(offset)}
        payload = api_get(path, {**(filters or {}), **params})
        if isinstance(payload, list):
            return results + payload
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError(f"Plane API returned an invalid page for {path}")
        page = payload["results"]
        results.extend(page)
        if payload.get("next_page_results") is not True:
            total = payload.get("total_results")
            if isinstance(total, int) and total > len(results):
                raise RuntimeError(f"Plane API truncated results for {path}: got {len(results)} of {total}")
            return results
        if not page:
            raise RuntimeError(f"Plane API pagination did not advance for {path}")
        next_cursor = payload.get("next_cursor")
        if next_cursor:
            if next_cursor in seen_cursors:
                raise RuntimeError(f"Plane API repeated a cursor for {path}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            mode = "cursor"
        else:
            offset = len(results)
            mode = "offset"
    raise RuntimeError(f"Plane API exceeded 100 pages for {path}")


def fetch_items(project_id: str, force_refresh: bool = False, fields: str = "") -> list[dict[str, Any]]:
    cache_key = f"items_{project_id}_{fields or 'all'}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached
    path = f"workspaces/{WORKSPACE}/projects/{project_id}/work-items/"
    filters = {"fields": fields} if fields else None
    try:
        # This self-hosted Plane accepts 1000 rows per page; retry the documented maximum if that changes.
        items = fetch_pages(path, "cursor", "1000", filters=filters)
    except urllib.error.HTTPError as error:
        if error.code != 400:
            raise
        items = fetch_pages(path, "cursor", PLANE_PAGE_SIZE, filters=filters)
    result = list({str(item.get("id")): item for item in items}.values())
    return set_cached(cache_key, result)

def fetch_projects(force_refresh: bool = False) -> list[dict[str, Any]]:
    if not force_refresh:
        cached = get_cached("projects")
        if cached is not None:
            return cached
    return set_cached("projects", fetch_pages(f"workspaces/{WORKSPACE}/projects/"))


def fetch_states(project_id: str, force_refresh: bool = False) -> dict[str, dict[str, str]]:
    cache_key = f"states_{project_id}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached
    states = fetch_pages(f"workspaces/{WORKSPACE}/projects/{project_id}/states/")
    result = {str(item["id"]): {"name": item.get("name", "Unknown"), "group": item.get("group", "unknown")} for item in states}
    return set_cached(cache_key, result)

def fetch_members(force_refresh: bool = False) -> list[dict[str, Any]]:
    if not force_refresh:
        cached = get_cached("members")
        if cached is not None:
            return cached
    result = fetch_pages(f"workspaces/{WORKSPACE}/members/")
    return set_cached("members", result)


def fetch_labels(project_id: str, force_refresh: bool = False) -> dict[str, str]:
    cache_key = f"labels_{project_id}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached
    labels = fetch_pages(f"workspaces/{WORKSPACE}/projects/{project_id}/labels/")
    result = {str(item["id"]): item.get("name", "Unknown") for item in labels}
    return set_cached(cache_key, result)

def html_to_markdown(content: str, render_image: Callable[[str], str] | None = None) -> str:
    renderer = MarkdownRenderer(render_image)
    renderer.feed(content or "")
    return renderer.render()


def fetch_comments(project_id: str, item_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    cache_key = f"comments_{project_id}_{item_id}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached
    result = fetch_pages(f"workspaces/{WORKSPACE}/projects/{project_id}/work-items/{item_id}/comments/")
    return set_cached(cache_key, result)

def fetch_links(project_id: str, item_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    cache_key = f"links_{project_id}_{item_id}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached
    result = fetch_pages(f"workspaces/{WORKSPACE}/projects/{project_id}/work-items/{item_id}/links/")
    return set_cached(cache_key, result)

def fetch_attachments(project_id: str, item_id: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    cache_key = f"attachments_{project_id}_{item_id}"
    if not force_refresh:
        cached = get_cached(cache_key)
        if cached is not None:
            return cached
    result = fetch_pages(f"workspaces/{WORKSPACE}/projects/{project_id}/work-items/{item_id}/attachments/")
    return set_cached(cache_key, result)

def cleanup_old_assets(max_age_days: int = 7) -> None:
    base_tmp = ROOT / "tmp" / "plane-mcp"
    if not base_tmp.exists():
        return
    cutoff = time.time() - (max_age_days * 86400)
    for task_dir in base_tmp.iterdir():
        if task_dir.is_dir():
            try:
                if task_dir.stat().st_mtime < cutoff:
                    for f in task_dir.iterdir():
                        f.unlink(missing_ok=True)
                    task_dir.rmdir()
            except Exception:
                pass


def text_preview(description_html: str, lines: int = 3) -> str:
    value = re.sub(r"</(p|div|li|h[1-6]|blockquote|tr)>|<br\s*/?>", "\n", description_html or "", flags=re.I)
    value = re.sub(r"<image-component[^>]*>|<img[^>]*>", " [image] ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\xa0", " ")
    normalized = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    normalized = [line for line in normalized if line]
    if not normalized:
        normalized = [re.sub(r"\s+", " ", value).strip()] if value.strip() else []
    return "\n".join(normalized[:lines])


def enrich(item: dict[str, Any], states: dict[str, dict[str, str]]) -> dict[str, Any]:
    state = states.get(str(item.get("state")), {"name": "Unknown", "group": "unknown"})
    return {**item, "state_name": state["name"], "state_group": state["group"]}


def format_assignees(assignee_ids: list[Any], members: list[dict[str, Any]]) -> str:
    if not assignee_ids:
        return "Unassigned"
    id_map: dict[str, str] = {}
    for m in members:
        user = m.get("member", m)
        uid = str(user.get("id"))
        display = user.get("display_name") or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("email", "Unknown")
        email = user.get("email")
        id_map[uid] = f"{display} ({email})" if email and email not in display else display
    formatted = [id_map.get(str(aid), str(aid)) for aid in assignee_ids]
    return ", ".join(formatted)


def configured_projects(project_filter: str = "") -> list[dict[str, str]]:
    """Resolve only projects explicitly present in this user's allowlist."""
    requested = project_filter.strip()
    if not requested:
        if not PLANE_DEFAULT_PROJECT:
            raise ValueError("Specify projects by key/identifier, or configure default_project")
        keys = [PLANE_DEFAULT_PROJECT]
    elif requested.casefold() == "all":
        keys = list(PLANE_PROJECTS)
    else:
        values = {part.strip().casefold() for part in requested.split(",") if part.strip()}
        keys = [
            key for key, project in PLANE_PROJECTS.items()
            if values.intersection({
                key.casefold(), project["id"].casefold(), project["identifier"].casefold(),
                str(project.get("name", "")).casefold(),
            })
        ]
        if len(keys) != len(values):
            raise ValueError("Requested project is not in this user's configured project allowlist")
    return [
        {"key": key, "id": PLANE_PROJECTS[key]["id"], "identifier": PLANE_PROJECTS[key]["identifier"],
         "name": PLANE_PROJECTS[key].get("name", key)}
        for key in keys
    ]


def project_for_task_identifier(task_identifier: str) -> tuple[str, int, dict[str, str]]:
    code = task_identifier.strip().upper()
    match = re.fullmatch(r"([A-Z][A-Z0-9]*)-(\d+)", code)
    if not match:
        raise ValueError("task_identifier must be a full Plane code such as PROJECT-123")
    project = next((p for p in configured_projects("all") if p["identifier"].upper() == match.group(1)), None)
    if project is None:
        raise ValueError(f"Task prefix {match.group(1)} is not in this user's configured project allowlist")
    return code, int(match.group(2)), project


def find_item(task_identifier: str, include_closed: bool = True, force_refresh: bool = False) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    task_identifier, expected_sequence, project = project_for_task_identifier(task_identifier)
    cache_key = f"item_{task_identifier}"
    item = None if force_refresh else get_cached(cache_key)
    fetched = item is None
    if item is None:
        try:
            item = api_get(f"workspaces/{WORKSPACE}/work-items/{urllib.parse.quote(task_identifier, safe='')}/")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ValueError(f"Không tìm thấy task {task_identifier}") from error
            raise
        if (not isinstance(item, dict) or item.get("sequence_id") != expected_sequence
                or not isinstance(item.get("project"), str) or not item["project"]
                or not isinstance(item.get("id"), str) or not item["id"]
                or "description_html" not in item
                or (item["description_html"] is not None and not isinstance(item["description_html"], str))):
            raise ValueError(f"Plane API trả về task {task_identifier} không hợp lệ")
    if str(item.get("project")) != project["id"]:
        raise ValueError(f"Task {task_identifier} does not belong to configured project {project['identifier']}")
    if fetched:
        set_cached(cache_key, item)
    states = fetch_states(str(item["project"]), force_refresh=force_refresh)
    item = enrich(item, states)
    if not include_closed and item["state_group"] in {"completed", "cancelled"}:
        raise ValueError(f"Task {task_identifier} đã đóng")
    return item, states


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_plane_projects() -> str:
    """List only Plane projects explicitly configured for this user."""
    if REMOTE_MCP_URL:
        return remote_project_listing(call_remote_tool("list_plane_projects", {}))
    configured = configured_projects("all")
    live = {str(project.get("id")): project for project in fetch_projects()}
    projects = [live[p["id"]] for p in configured if p["id"] in live]
    if not projects:
        raise ValueError("None of the configured Plane projects are visible to this API user")
    return "Plane projects\n\n" + "\n".join(
        f"{project.get('identifier', '?')} | {project.get('name', '-')} | {project.get('id', '?')}"
        for project in projects
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def list_plane_tasks(
    assignee: str = "",
    query: str = "",
    state: str = "",
    priority: str = "",
    include_closed: bool = False,
    limit: int = 50,
    force_refresh: bool = False,
    projects: str = "",
) -> str:
    """List Todo/In Progress tasks for configured project key(s). Use a task code directly with get_plane_task when known."""
    if REMOTE_MCP_URL:
        result = call_remote_tool("list_plane_tasks", {
            "assignee": assignee.strip() or DEFAULT_ASSIGNEE,
            "query": query,
            "state": state,
            "priority": priority,
            "include_closed": include_closed,
            "limit": limit,
            "force_refresh": force_refresh,
            "projects": remote_project_filter(projects),
        })
        return remote_text(result)
    available_projects = fetch_projects(force_refresh=force_refresh)
    configured = configured_projects(projects)
    live_projects = {str(project.get("id")): project for project in available_projects}
    selected_projects = []
    for allowed in configured:
        live = live_projects.get(allowed["id"])
        if live is None:
            raise ValueError(f"Configured project {allowed['identifier']} is unavailable to this Plane API user")
        selected_projects.append({**live, "key": allowed["key"]})
    members = fetch_members(force_refresh=force_refresh)
    assignee_filter = assignee.strip() or DEFAULT_ASSIGNEE
    member_ids = {
        str(member.get("id")) for member in members
        if assignee_filter.lower() in {
            str(member.get("display_name", "")).lower(), str(member.get("email", "")).lower(),
            f"{member.get('first_name', '')} {member.get('last_name', '')}".strip().lower(),
        }
    } if assignee_filter.lower() != "all" else set()

    # Keep per-project results isolated while the independent project reads run concurrently.
    def load_project(project: dict[str, Any]) -> list[dict[str, Any]]:
        project_id = str(project["id"])
        states = fetch_states(project_id, force_refresh=force_refresh)
        result = []
        fields = "id,sequence_id,name,assignees,state,priority,target_date"
        if query.strip():
            fields += ",description_html"
        for item in fetch_items(project_id, force_refresh=force_refresh, fields=fields):
            enriched = enrich(item, states)
            enriched["project_identifier"] = project.get("identifier", "?")
            if assignee_filter.lower() != "all" and not any(str(value) in member_ids for value in item.get("assignees", [])):
                continue
            if not state and not include_closed and enriched["state_group"] not in {"unstarted", "started"}:
                continue
            if not include_closed and enriched["state_group"] in {"completed", "cancelled"}:
                continue
            result.append(enriched)
        return result

    inaccessible_projects = []
    with ThreadPoolExecutor(max_workers=min(5, max(1, len(selected_projects)))) as executor:
        futures = {submit_with_context(executor, load_project, project): project for project in selected_projects}
        items = []
        for future in as_completed(futures):
            project = futures[future]
            try:
                items.extend(future.result())
            except urllib.error.HTTPError as error:
                if error.code != 403:
                    raise
                identifier = str(project.get("identifier", project.get("name", project["id"])))
                inaccessible_projects.append(identifier)
                if not force_refresh:
                    set_cached(f"forbidden_project_{project['id']}", True)
    clean_query = query.strip().lower()
    if clean_query:
        items = [item for item in items if clean_query in str(item.get("sequence_id", "")).lower() or clean_query in str(item.get("name", "")).lower() or clean_query in str(item.get("description_html", "")).lower()]
    clean_state = state.strip().lower()
    if clean_state:
        items = [item for item in items if clean_state == item.get("state_name", "").lower() or clean_state == item.get("state_group", "").lower() or clean_state in item.get("state_name", "").lower()]
    clean_priority = priority.strip().lower()
    if clean_priority:
        items = [item for item in items if clean_priority == str(item.get("priority", "")).lower()]
    items.sort(key=lambda item: (STATE_GROUP_ORDER.get(item.get("state_group", "backlog"), 9), item.get("target_date") or "9999", -int(item.get("sequence_id") or 0)))
    selected = items[:max(1, min(limit, 200))]
    project_filter = ",".join(str(project.get("identifier")) for project in selected_projects)
    filter_label = f"assignee={assignee_filter}, projects={project_filter}"
    if query:
        filter_label += f", query='{query}'"
    if state:
        filter_label += f", state='{state}'"
    if priority:
        filter_label += f", priority='{priority}'"
    if force_refresh:
        filter_label += ", force_refresh=True"
    access_note = f"\n\nBỏ qua project không có quyền đọc work items: {', '.join(sorted(inaccessible_projects))}." if inaccessible_projects else ""
    if not selected:
        return f"Không có task phù hợp ({filter_label}).{access_note}"
    blocks = [f"{item.get('project_identifier')}-{item.get('sequence_id')} | {item['state_name']} | {item.get('name', '-')}" for item in selected]
    return f"Plane tasks ({len(selected)}/{len(items)}, {filter_label})\n\n" + "\n\n".join(blocks) + access_note


@mcp.tool(structured_output=False, annotations=ToolAnnotations(readOnlyHint=True))
def get_plane_task(task_identifier: str, force_refresh: bool = False) -> list[Any]:
    """Read a task from this MCP server's configured project by its full code, with description, comments, attachments and images.

    Read inline images directly. If the client exposes only image paths, inspect all images in one parallel tool batch.
    """
    if REMOTE_MCP_URL:
        task_identifier, _sequence, _project = project_for_task_identifier(task_identifier)
        return remote_task_content(call_remote_tool("get_plane_task", {
            "task_identifier": task_identifier,
            "force_refresh": force_refresh,
        }))
    cleanup_old_assets(7)
    item, _states = find_item(task_identifier, force_refresh=force_refresh)
    project_id = str(item["project"])
    asset_root = ROOT / "tmp" / "plane-mcp" / _token_scope()
    asset_root.mkdir(parents=True, exist_ok=True)
    asset_dir = Path(tempfile.mkdtemp(prefix=f"{task_identifier}-", dir=asset_root))
    renderer = DescriptionRenderer(
        asset_dir=asset_dir,
        api_token=get_api_token(),
        base_url=API,
        asset_url_prefix=f"{API}/workspaces/{WORKSPACE}/projects/{project_id}/work-items/{item['id']}/attachments",
    )
    with ThreadPoolExecutor(max_workers=5) as executor:
        members_future = submit_with_context(executor, fetch_members, force_refresh=force_refresh)
        labels_future = submit_with_context(executor, fetch_labels, project_id, force_refresh=force_refresh)
        links_future = submit_with_context(executor, fetch_links, project_id, item["id"], force_refresh=force_refresh)
        comments_future = submit_with_context(executor, fetch_comments, project_id, item["id"], force_refresh=force_refresh)
        attachments_future = submit_with_context(executor, fetch_attachments, project_id, item["id"], force_refresh=force_refresh)
        renderer.feed(item.get("description_html") or "")
        rendered = renderer.render().strip() or "(trống)"
        members = members_future.result()
        labels = labels_future.result()
        links = links_future.result()
        comments = comments_future.result()
        attachments = attachments_future.result()

    assignee_str = format_assignees(item.get("assignees", []), members)

    label_names = [labels.get(str(lid), str(lid)) for lid in item.get("labels", [])]
    labels_str = ", ".join(label_names) if label_names else "None"
    target_date = item.get("target_date") or "None"
    start_date = item.get("start_date") or "None"
    created_by_id = str(item.get("created_by", ""))
    creator_name = next((
        user.get("display_name") or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("email", "Unknown")
        for m in members
        for user in [m.get("member", m)]
        if str(user.get("id")) == created_by_id
    ), "Unknown") if created_by_id else "N/A"

    # External links if any
    links_str = ""
    if links:
        link_items = [f"[{l.get('title') or 'Link'}]({l.get('url')})" for l in links if l.get("url")]
        if link_items:
            links_str = f"External Links: {', '.join(link_items)}\n"

    header = (
        f"#{item.get('sequence_id')} — {item.get('name', 'N/A')}\n"
        f"State: {item['state_name']} | Priority: {item.get('priority', 'none')} | Labels: {labels_str}\n"
        f"Assignees: {assignee_str} | Creator: {creator_name}\n"
        f"Start Date: {start_date} | Target Date (Deadline): {target_date}\n"
        f"{links_str}\n"
        "Mô tả đầy đủ:\n"
    )

    # Comments (contains Acceptance Criteria / discussion from QA/Lead)
    comments_block = ""
    if comments:
        comment_parts = []
        for comment_index, c in enumerate(comments, 1):
            raw_html = c.get("comment_html") or ""
            image_number = 0

            def render_comment_image(tag: str) -> str:
                nonlocal image_number
                image_number += 1
                image_renderer = DescriptionRenderer(
                    asset_dir, get_api_token(), API,
                    f"{API}/workspaces/{WORKSPACE}/projects/{project_id}/work-items/{item['id']}/attachments",
                    filename_prefix=f"comment-{comment_index}-{image_number}",
                )
                image_renderer.feed(tag)
                return image_renderer.render()

            c_text = html_to_markdown(raw_html, render_comment_image)
            if not c_text:
                continue
            actor_id = str(c.get("actor") or c.get("created_by") or "")
            actor_name = next((
                user.get("display_name") or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("email", "Unknown")
                for m in members
                for user in [m.get("member", m)]
                if str(user.get("id")) == actor_id
            ), "Unknown User") if actor_id else "User"
            created_at = (c.get("created_at") or "")[:19].replace("T", " ")
            comment_parts.append(f"💬 [{created_at} | {actor_name}]:\n{c_text}")
        if comment_parts:
            comments_block = (
                "\n\n" + "=" * 60 + "\n"
                "=== BÌNH LUẬN & ACCEPTANCE CRITERIA (Comments): ===\n"
                + "=" * 60 + "\n\n"
                + "\n\n---\n\n".join(comment_parts)
            )

    attachment_lines = []
    embedded_ids = {
        source for part_index, _, source, _ in renderer.pending_images
        if (renderer.parts[part_index] or "").startswith("\n![")
    }
    attachment_renderer = DescriptionRenderer(
        asset_dir, get_api_token(), API,
        f"{API}/workspaces/{WORKSPACE}/projects/{project_id}/work-items/{item['id']}/attachments",
    )
    for attachment_index, attachment in enumerate(attachments, 1):
        attributes = attachment.get("attributes") or {}
        name = str(attributes.get("name") or f"Attachment {attachment_index}")
        content_type = str(attributes.get("type") or "")
        size = attachment.get("size")
        details = f"{content_type}, {size} bytes" if content_type and size is not None else content_type or "size unknown"
        attachment_id = attachment.get("id")
        if not attachment_id:
            attachment_lines.append(f"- {name} ({details}; unavailable: missing attachment id)")
            continue
        if content_type.startswith("image/") and str(attachment_id) in embedded_ids:
            attachment_lines.append(f"- {name} ({details}; included above)")
            continue
        path = attachment_renderer.materialize_attachment(attachment_index, str(attachment_id), name)
        link = f"[{name}]({path})" if not path.startswith("unavailable") else path
        attachment_lines.append(f"- {name} ({details}; {link})")
    attachments_block = "\n\nAttachments:\n" + "\n".join(attachment_lines) if attachment_lines else ""
    images = [
        Image(path=path) for path in sorted(asset_dir.iterdir())
        if path.is_file() and path.name.startswith(("image-", "comment-", "attachment-"))
        and mimetypes.guess_type(path.name)[0] in {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}
    ]
    return [header + rendered + comments_block + attachments_block, *images]


def render_html_cli() -> int:
    """Render HTML from stdin for the legacy shell helper."""
    asset_dir = Path(os.environ["PLANE_TASK_ASSET_DIR"])
    asset_url_prefix = os.environ.get(
        "PLANE_ASSET_URL_PREFIX",
        f"{API}/workspaces/{WORKSPACE}/work-items/attachments",
    )
    renderer = DescriptionRenderer(asset_dir, get_api_token(), API, asset_url_prefix)
    renderer.feed(sys.stdin.read())
    print(renderer.render())
    return 0


def print_shell_config_cli() -> int:
    """Print embedded configuration as shell-safe assignments."""
    for key, value in CONFIG.items():
        print(f"{key}={shlex.quote(str(value))}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--render-html":
        raise SystemExit(render_html_cli())
    if len(sys.argv) > 1 and sys.argv[1] == "--print-shell-config":
        raise SystemExit(print_shell_config_cli())
    if HTTP_MODE:
        run_http_server()
    else:
        mcp.run()
