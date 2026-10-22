#!/usr/bin/env python3
"""Install and update the user-wide Plane MCP checkout."""

from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import UUID

REPO = "https://github.com/ydanh-dev/plane_mcp.git"
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path.home() / ".config/plane-mcp/config.json"
PLANE_BASE_URL = "https://plane.apetechs.co/api/v1"
PLANE_WORKSPACE_SLUG = "apetechs"


def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, **kwargs)  # type: ignore[arg-type]


def load_config() -> dict[str, object]:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict[str, object]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    if os.name != "nt":
        CONFIG_PATH.chmod(0o600)


def python_executable() -> Path:
    return ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def fetch_project_metadata(token: str, project_id: str) -> dict[str, str]:
    url = (
        f"{PLANE_BASE_URL}/workspaces/{urllib.parse.quote(PLANE_WORKSPACE_SLUG, safe='')}"
        f"/projects/{urllib.parse.quote(project_id, safe='')}/"
    )
    request = urllib.request.Request(url, headers={"X-API-Key": token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            project = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError("Could not read this project; check the UUID, API token, and network access") from error
    if not isinstance(project, dict):
        raise ValueError("Plane returned invalid project details for this UUID")
    identifier = str(project.get("identifier") or "").upper()
    name = str(project.get("name") or "").strip()
    if (str(project.get("id")) != project_id
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", identifier) or not name):
        raise ValueError("Plane returned invalid project details for this UUID")
    return {"id": project_id, "identifier": identifier, "name": name}


def fetch_current_user(token: str) -> dict[str, str]:
    request = urllib.request.Request(
        f"{PLANE_BASE_URL}/users/me/", headers={"X-API-Key": token, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            user = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValueError("Could not identify this Plane API token; check the token and network access") from error
    if not isinstance(user, dict):
        raise ValueError("Plane returned invalid user details for this API token")
    try:
        user_id = str(UUID(str(user.get("id", ""))))
    except ValueError as error:
        raise ValueError("Plane returned invalid user details for this API token") from error
    display = str(user.get("display_name") or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or user.get("email") or user_id)
    return {"id": user_id, "display": display, "assignee": str(user.get("email") or display)}


def setup() -> None:
    previous = load_config()
    print("Plane MCP setup — user-local config; token is stored in the operating system credential store.\n")

    token = getpass.getpass("Plane API token (input hidden): ").strip()
    if not token:
        raise SystemExit("Token is required")
    try:
        current_user = fetch_current_user(token)
    except ValueError as error:
        raise SystemExit(f"{error}.") from error
    print(f"Detected Plane account: {current_user['display']}")

    projects: dict[str, dict[str, str]] = {}
    old_projects = previous.get("projects", {})
    if isinstance(old_projects, dict):
        projects.update(old_projects)
    print("\nAdd projects by UUID. Plane supplies each project's name and task-code prefix.")
    if projects:
        print(f"Keeping {len(projects)} existing project(s).")
    while True:
        prompt = "Plane project UUID (Enter to finish): " if projects else "Plane project UUID: "
        project_id = input(prompt).strip()
        if not project_id:
            if projects:
                break
            print("Add at least one project UUID.")
            continue
        try:
            project_id = str(UUID(project_id))
        except ValueError:
            print("Enter the full project UUID from the Plane URL.")
            continue
        try:
            project = fetch_project_metadata(token, project_id)
        except ValueError as error:
            print(f"  {error}.")
            continue
        default_key = project["identifier"].lower()
        key = next((
            existing_key for existing_key, old in projects.items()
            if isinstance(old, dict) and str(old.get("id", "")).lower() == project_id
        ), default_key)
        projects[key] = project
        print(f"  Added {project['identifier']} — {project['name']}.")
        if input("Add another project? [y/N]: ").strip().lower() not in {"y", "yes"}:
            break
    if not projects:
        raise SystemExit("Configure at least one project")
    previous_default = str(previous.get("default_project", ""))
    default_project = previous_default if previous_default in projects else next(iter(projects))

    account = str(previous.get("credential_account") or getpass.getuser())
    try:
        import keyring
        if keyring.get_keyring().priority <= 0:
            raise RuntimeError("No secure credential backend is available")
        keyring.set_password("plane-mcp", account, token)
    except Exception as error:
        raise SystemExit(f"Cannot store token securely with the OS credential store: {error}") from error
    config: dict[str, object] = {
        "base_url": PLANE_BASE_URL, "workspace_slug": PLANE_WORKSPACE_SLUG,
        "default_assignee_id": current_user["id"], "default_assignee": current_user["assignee"],
        "credential_account": account,
        "default_project": default_project, "projects": projects,
    }
    if previous.get("remote_mcp_url"):
        config["remote_mcp_url"] = previous["remote_mcp_url"]
    save_config(config)
    print(f"\nSetup complete. Saved config: {CONFIG_PATH}\nPlane token saved to the OS credential store (service: plane-mcp).")
    print("\nRegister one MCP server in Codex CLI/Desktop:")
    print(f'  codex mcp add plane -- "{python_executable()}" "{ROOT / "plane_mcp.py"}"')
    print("\nFor Antigravity, add the same command and args as one global MCP server in MCP Config.")
    print("Restart the clients after registration.")


def latest_tag() -> str:
    output = run(["git", "-C", str(ROOT), "tag", "--list", "v*", "--sort=-version:refname"], capture_output=True).stdout
    tags = [tag for tag in output.splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", tag)]
    if not tags:
        raise SystemExit("No stable version tags found on this checkout")
    return tags[0]


def update() -> None:
    run(["git", "-C", str(ROOT), "fetch", "--tags", "--force", "origin"])
    latest = latest_tag()
    current = subprocess.run(["git", "-C", str(ROOT), "describe", "--tags", "--exact-match", "HEAD"], text=True, capture_output=True).stdout.strip()
    if current == latest:
        print(f"Already using {latest}.")
        return
    print(f"Installed: {current or 'development checkout'}\nAvailable: {latest}")
    if input("Update now? [y/N]: ").strip().lower() not in {"y", "yes"}:
        print("Kept the installed version. Run `plane-mcp update` to check again later.")
        return
    old_head = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True).stdout.strip()
    changed = run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True).stdout.strip()
    if changed:
        raise SystemExit("Update stopped: managed install has local file changes. Back them up or reinstall before updating.")
    try:
        run(["git", "-C", str(ROOT), "checkout", "--detach", latest])
        run([str(python_executable()), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    except (subprocess.CalledProcessError, OSError):
        subprocess.run(["git", "-C", str(ROOT), "checkout", "--detach", old_head], check=False)
        raise
    print(f"Updated to {latest}. Restart the MCP host to load it.")


def set_remote_url(value: str) -> None:
    config = load_config()
    if not config.get("projects"):
        raise SystemExit("Run `plane-mcp setup` before configuring a remote server")
    if value.strip().lower() in {"off", "local"}:
        config.pop("remote_mcp_url", None)
        save_config(config)
        print("Plane MCP now calls Plane directly from this machine.")
        return
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("Remote MCP URL must be an HTTPS URL ending in /mcp")
    if not parsed.path.rstrip("/").endswith("/mcp"):
        raise SystemExit("Remote MCP URL must end in /mcp")
    config["remote_mcp_url"] = value.strip().rstrip("/")
    save_config(config)
    print("Remote Plane MCP URL saved. The Plane token remains in the OS credential store.")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("Usage: plane-mcp {setup|update|status|remote <https-url|off>}")
        return
    if sys.argv[1] == "setup":
        setup()
    elif sys.argv[1] == "update":
        update()
    elif sys.argv[1] == "status":
        config = load_config()
        print(f"Install: {ROOT}\nVersion: {subprocess.run(['git', '-C', str(ROOT), 'describe', '--tags', '--exact-match', 'HEAD'], text=True, capture_output=True).stdout.strip() or 'development'}\nConfig: {CONFIG_PATH}\nProjects: {', '.join(config.get('projects', {}).keys())}")
    elif sys.argv[1] == "remote" and len(sys.argv) == 3:
        set_remote_url(sys.argv[2])
    else:
        raise SystemExit(f"Unknown command: {sys.argv[1]}")


if __name__ == "__main__":
    main()
