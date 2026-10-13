#!/usr/bin/env python3
"""Install and update the user-wide Plane MCP checkout."""

from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

REPO = "https://github.com/ydanh-dev/plane_mcp.git"
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path.home() / ".config/plane-mcp/config.json"


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


def setup() -> None:
    previous = load_config()
    print("Plane MCP setup — user-local config; token is stored in the operating system credential store.\n")
    base_url = input(f"Plane API base URL [{previous.get('base_url', 'https://api.plane.so/api/v1')}]: ").strip()
    base_url = base_url or str(previous.get("base_url", "https://api.plane.so/api/v1"))
    if urlparse(base_url).scheme != "https":
        raise SystemExit("Plane API URL must use HTTPS")
    workspace = input(f"Workspace slug [{previous.get('workspace_slug', '')}]: ").strip() or str(previous.get("workspace_slug", ""))
    if not workspace or not re.fullmatch(r"[A-Za-z0-9_-]+", workspace):
        raise SystemExit("Enter a valid workspace slug")
    display = input(f"Your Plane display name [{previous.get('display_name', '')}]: ").strip() or str(previous.get("display_name", ""))
    if not display:
        raise SystemExit("Display name is required")

    projects: dict[str, dict[str, str]] = {}
    old_projects = previous.get("projects", {})
    if isinstance(old_projects, dict):
        projects.update(old_projects)
    print("\nAdd or update projects. Leave the key empty when done.")
    while True:
        key = input("Project key (e.g. np, busan, ntssv2): ").strip()
        if not key:
            break
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key):
            print("Use letters/numbers/underscore/hyphen; begin with a letter.")
            continue
        identifier = input("  Plane identifier (task-code prefix): ").strip().upper()
        project_id = input("  Plane project UUID: ").strip()
        try:
            project_id = str(UUID(project_id))
        except ValueError:
            print("  Invalid UUID; this project was not saved.")
            continue
        name = input("  Display label [same as key]: ").strip() or key
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", identifier):
            print("  Invalid Plane identifier; this project was not saved.")
            continue
        projects[key] = {"id": project_id, "identifier": identifier, "name": name}
    if not projects:
        raise SystemExit("Configure at least one project")
    default_project = input(f"Default project key [{previous.get('default_project', '')}]: ").strip() or str(previous.get("default_project", ""))
    if not default_project:
        default_project = next(iter(projects))
    if default_project not in projects:
        raise SystemExit("Default project key must match a configured project")

    account = str(previous.get("credential_account") or getpass.getuser())
    token = getpass.getpass("Plane API token (input hidden): ").strip()
    if not token:
        raise SystemExit("Token is required")
    try:
        import keyring
        if keyring.get_keyring().priority <= 0:
            raise RuntimeError("No secure credential backend is available")
        keyring.set_password("plane-mcp", account, token)
    except Exception as error:
        raise SystemExit(f"Cannot store token securely with the OS credential store: {error}") from error
    config: dict[str, object] = {
        "base_url": base_url.rstrip("/"), "workspace_slug": workspace,
        "display_name": display, "credential_account": account,
        "default_project": default_project, "projects": projects,
    }
    save_config(config)
    print(f"\nSaved config: {CONFIG_PATH}\nPlane token saved to the OS credential store (service: plane-mcp).")
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
    config = load_config()
    if current == latest:
        print(f"Already using {latest}.")
        return
    if config.get("skipped_version") == latest:
        print(f"Version {latest} was skipped. Run `plane-mcp update --force` to review it again.")
        return
    print(f"Installed: {current or 'development checkout'}\nAvailable: {latest}")
    if "--force" not in sys.argv:
        choice = input("[u] Update  [k] Keep current  [s] Skip this version: ").strip().lower()
        if choice == "s":
            config["skipped_version"] = latest
            save_config(config)
            print(f"Skipped {latest}; it will not be offered again until a newer version is published.")
            return
        if choice != "u":
            print("Kept the installed version.")
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
    config.pop("skipped_version", None)
    save_config(config)
    print(f"Updated to {latest}. Restart the MCP host to load it.")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print("Usage: plane-mcp {setup|update|status}")
        return
    if sys.argv[1] == "setup":
        setup()
    elif sys.argv[1] == "update":
        update()
    elif sys.argv[1] == "status":
        config = load_config()
        print(f"Install: {ROOT}\nVersion: {subprocess.run(['git', '-C', str(ROOT), 'describe', '--tags', '--exact-match', 'HEAD'], text=True, capture_output=True).stdout.strip() or 'development'}\nConfig: {CONFIG_PATH}\nProjects: {', '.join(config.get('projects', {}).keys())}")
    else:
        raise SystemExit(f"Unknown command: {sys.argv[1]}")


if __name__ == "__main__":
    main()
