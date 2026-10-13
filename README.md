# Plane MCP

Local, user-wide MCP server for Plane. One installation can serve multiple configured Plane projects and multiple MCP clients, including Codex CLI/Desktop and Antigravity. The MCP process runs on the user's computer; this project does not require a hosted application server.

> **Security:** the repository contains no Plane token or per-user project configuration. Setup stores the token in the operating system credential store: macOS Keychain, Windows Credential Manager, or Linux Secret Service. Each user maintains their own project allowlist and display name in `~/.config/plane-mcp/config.json`.

## English

### What it does

- Lists configured Plane projects and reads tasks, descriptions, comments, attachments, and images.
- Routes a task detail request by its full Plane code (for example, `DEMOPROJECT-123`). The code prefix must match a project in the local allowlist.
- Lists tasks for the configured default project, or for explicitly selected project keys/identifiers. `all` means all projects in that user's allowlist.
- Installs one local server process per user. Codex and Antigravity can point to the same installation; projects are not separate MCP server entries.
- Provides a terminal update prompt with **Update / Keep / Skip this version**. A skipped release is remembered until a newer version is published.

### Requirements

- macOS 11+, Windows, or desktop Linux with a Secret Service provider (such as GNOME Keyring or KDE Wallet).
- Python 3.10+ and Git.
- A Plane API token with read access to the projects you configure.
- Codex CLI/Desktop or Antigravity for MCP use.

### Install

Review the installer before running it:

```sh
curl -fsSLo /tmp/plane-mcp-install.sh https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh
less /tmp/plane-mcp-install.sh
sh /tmp/plane-mcp-install.sh
```

On macOS/Linux, the installer clones the public repository to `~/.local/share/plane-mcp`, creates a Python virtual environment, installs dependencies, and starts first-time setup. It does not overwrite an existing non-managed directory. To use a one-line bootstrap instead, run:

```sh
curl -fsSL https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh | sh
```

The one-line form executes the downloaded installer directly. Inspect it first if you prefer to review installation scripts before execution.

On Windows, download and inspect `install.ps1`, then run it in PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 -OutFile "$env:TEMP\plane-mcp-install.ps1"
notepad "$env:TEMP\plane-mcp-install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\plane-mcp-install.ps1"
```

Linux requires an active Secret Service implementation on the desktop session. If no secure credential backend is available, setup stops instead of writing the Plane token to a plain-text file.

### First-time setup

The setup wizard asks for:

1. Plane API base URL (`https://api.plane.so/api/v1` for Plane Cloud; press Enter to keep it. Change only for self-hosted Plane).
2. Plane workspace slug.
3. Your Plane display name, used as the default assignee filter.
4. Project key, project identifier/task-code prefix, project UUID, and label for each project.
5. Default project key for task-list requests that omit a project.
6. Plane API token (hidden while typing; saved in the operating system credential store).

The resulting JSON contains configuration only, not the API token. Protect the file because it identifies the projects this installation may access:

| Data | Where it is stored | Protection |
| --- | --- | --- |
| User and project settings | `~/.config/plane-mcp/config.json` | File permissions on macOS/Linux; user-profile ACL on Windows |
| Plane API token | OS credential store, service `plane-mcp` | macOS Keychain, Windows Credential Manager, or Linux Secret Service |

Project keys are personal aliases such as `np`, `busan`, or `ntssv2`. The Plane identifier is the prefix in task codes; the UUID is the project ID from Plane. Only configured project UUIDs can be read. A task code with an unknown prefix is rejected before an API request.

### Connect Codex CLI / Desktop

The installer prints the exact command after setup. It has this form:

```sh
codex mcp add plane -- "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/plane_mcp.py"
```

Codex CLI and Desktop use the same user MCP configuration. If the server is already registered, remove only that entry and add it again:

```sh
codex mcp remove plane
codex mcp add plane -- "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/plane_mcp.py"
```

Restart or reload the MCP server in Codex after changing registration.

On Windows, use the paths printed by `plane-mcp setup`; the Python executable is under `.local\share\plane-mcp\.venv\Scripts\python.exe`.

### Connect Antigravity

In Antigravity, open **MCP Servers → Open MCP Config** and add one global server entry. Use the same executable and script:

```json
{
  "mcpServers": {
    "plane": {
      "command": "/Users/YOUR_USER/.local/share/plane-mcp/.venv/bin/python",
      "args": ["/Users/YOUR_USER/.local/share/plane-mcp/plane_mcp.py"]
    }
  }
}
```

Replace `YOUR_USER` with the macOS home folder name. Preserve any other existing `mcpServers` entries. Use Antigravity's **Refresh** action or restart the app after saving. This is a global user server; do not copy it into each project workspace.

On Windows, use the `.venv\\Scripts\\python.exe` path and escape backslashes in JSON (for example, `C:\\Users\\YOUR_USER\\.local\\share\\plane-mcp\\.venv\\Scripts\\python.exe`).

### Selecting a project

- For a known task, ask for the full code, such as `get DEMOPROJECT-123`. The project is resolved from the code prefix.
- For a task list, specify a project key or identifier, such as `list my Todo and In Progress tasks in demo` or `projects=DEMOPROJECT`.
- If no project is specified, the configured `default_project` is used.
- `projects=all` means every project configured by that user. It never grants access to projects omitted from their config.

The MCP reads project IDs from the user's config, not from the current repository/workspace. Workspace instructions should tell the assistant which project key to use for list requests.

### Updates

Run this when you want to check for a release:

```sh
plane-mcp update
```

The updater fetches version tags from GitHub and shows **Update / Keep current / Skip this version**. It installs only after you choose Update, preserves the user config and Keychain token, and returns to the prior code version if dependency installation fails. Skipping a version suppresses that same version; a later release will be offered. Use `plane-mcp update --force` to review a previously skipped release again. Restart the MCP host after updating.

There is no always-on update service and no automatic change to the installed version. To publish an update, the maintainer pushes a semantic version tag (for example, `v0.2.0`). Users choose when to install it.

Other commands:

```sh
plane-mcp status
plane-mcp setup
```

### Troubleshooting

- `plane-mcp: command not found`: add `~/.local/bin` to `PATH`, then open a new terminal.
- Token error: rerun `plane-mcp setup` and enter a valid Plane API token.
- Project unavailable: check that the workspace slug, project UUID, and token permissions match Plane.
- Task prefix rejected: configure the matching Plane identifier in `projects`.
- Codex or Antigravity does not show the server: verify the executable path, then reload/restart its MCP server.
- Update reports local file changes: do not overwrite the managed install manually. Back up needed changes, reinstall, and keep user config under `~/.config`.

### Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PLANE_MCP_CONFIG=/path/to/test-config.json PLANE_API_TOKEN=test-token .venv/bin/python -m unittest discover -s tests -v
```

### Privacy and limitations

- Plane API credentials are sent only to the configured Plane API host. The token is stored in the OS credential manager and is not written to the JSON config.
- Project allowlisting is enforced by the MCP before reading task details or listing work items. Plane permissions remain the underlying access boundary.
- GitHub is used to download source and check release tags. No application server is operated by this project.
- Updating is a terminal workflow. MCP clients do not share a common update-confirmation dialog, so this release does not display a clickable update modal inside Codex or Antigravity.
- Linux setup needs a logged-in desktop session with a working Secret Service provider. Headless Linux without that service is not supported by the secure setup flow.
- **License:** not yet specified. Public visibility does not itself grant permission to redistribute or modify this software; the maintainer must add a license before granting those rights.

## Tiếng Việt

### Giới thiệu

Plane MCP là MCP chạy cục bộ, dùng chung ở cấp user. Một lần cài đặt có thể dùng với nhiều project Plane và nhiều MCP client như Codex CLI/Desktop và Antigravity. MCP chạy trên máy của user; dự án này không cần thuê server ứng dụng để chạy liên tục.

> **Bảo mật:** repo không chứa Plane token hay cấu hình project cá nhân. Wizard lưu token vào kho credential của hệ điều hành: Keychain trên macOS, Credential Manager trên Windows, hoặc Secret Service trên Linux. Mỗi user có allowlist project và display name riêng trong `~/.config/plane-mcp/config.json`.

### Chức năng

- Liệt kê các project Plane đã cấu hình và đọc task, mô tả, bình luận, file đính kèm, hình ảnh.
- Tự định tuyến khi đọc task bằng mã đầy đủ, ví dụ `DEMOPROJECT-123`. Prefix phải khớp với một project trong allowlist cục bộ.
- Mặc định liệt kê task ở project mặc định; có thể chọn project bằng key/identifier. `all` chỉ có nghĩa là tất cả project trong allowlist của user đó.
- Chạy một MCP local cho mỗi user. Codex và Antigravity cùng trỏ vào một cài đặt; không cần tạo MCP riêng cho từng project.
- Có updater trên terminal với lựa chọn **Update / Keep / Skip this version**. Bản đã Skip được ghi nhớ tới khi có version mới.

### Yêu cầu

- macOS 11+, Windows, hoặc Linux desktop có Secret Service (ví dụ GNOME Keyring hoặc KDE Wallet).
- Python 3.10+ và Git.
- Plane API token có quyền đọc các project cần cấu hình.
- Codex CLI/Desktop hoặc Antigravity để sử dụng MCP.

### Cài đặt

Nên xem nội dung installer trước khi chạy:

```sh
curl -fsSLo /tmp/plane-mcp-install.sh https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh
less /tmp/plane-mcp-install.sh
sh /tmp/plane-mcp-install.sh
```

Trên macOS/Linux, installer clone repo công khai vào `~/.local/share/plane-mcp`, tạo Python virtual environment, cài dependencies và mở setup lần đầu. Installer không ghi đè thư mục có sẵn không do nó quản lý. Nếu muốn dùng lệnh bootstrap một dòng:

```sh
curl -fsSL https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh | sh
```

Dạng một dòng thực thi trực tiếp installer vừa tải về. Nếu muốn xem script trước, hãy dùng cách tải file rồi đọc ở trên.

Trên Windows, tải và đọc `install.ps1`, sau đó chạy bằng PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 -OutFile "$env:TEMP\plane-mcp-install.ps1"
notepad "$env:TEMP\plane-mcp-install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\plane-mcp-install.ps1"
```

Linux cần Secret Service đang hoạt động trong phiên desktop. Nếu không có credential backend bảo mật, setup sẽ dừng thay vì ghi Plane token vào file text thường.

### Cấu hình lần đầu

Wizard sẽ hỏi:

1. Plane API base URL (`https://api.plane.so/api/v1` nếu dùng Plane Cloud; nhấn Enter để giữ mặc định. Chỉ đổi nếu tự host Plane).
2. Workspace slug trên Plane.
3. Display name trên Plane, dùng làm assignee mặc định khi lọc task.
4. Project key, identifier/prefix task, UUID project và tên hiển thị cho mỗi project.
5. Project mặc định khi yêu cầu liệt kê task không chỉ định project.
6. Plane API token (không hiển thị khi nhập; được lưu trong kho credential của hệ điều hành).

File JSON chỉ chứa cấu hình, không chứa API token. Hãy bảo vệ file này vì nó xác định các project MCP được phép truy cập:

| Dữ liệu | Nơi lưu | Bảo vệ |
| --- | --- | --- |
| Cấu hình user và project | `~/.config/plane-mcp/config.json` | Quyền file trên macOS/Linux; ACL user profile trên Windows |
| Plane API token | Kho credential của OS, service `plane-mcp` | Keychain trên macOS, Credential Manager trên Windows, Secret Service trên Linux |

Project key là bí danh cá nhân, ví dụ `np`, `busan`, `ntssv2`. Plane identifier là prefix trong mã task; UUID là project ID lấy từ Plane. MCP chỉ đọc các UUID đã khai báo. Mã task có prefix chưa cấu hình sẽ bị từ chối trước khi gọi API.

### Kết nối Codex CLI / Desktop

Sau setup, installer in ra câu lệnh chính xác. Dạng lệnh:

```sh
codex mcp add plane -- "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/plane_mcp.py"
```

Codex CLI và Desktop dùng chung MCP config theo user. Nếu server đã được đăng ký, chỉ xóa entry đó rồi thêm lại:

```sh
codex mcp remove plane
codex mcp add plane -- "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/plane_mcp.py"
```

Sau khi thay đổi, reload MCP server hoặc khởi động lại Codex. Trên Windows, dùng các đường dẫn mà `plane-mcp setup` in ra; Python nằm trong `.local\share\plane-mcp\.venv\Scripts\python.exe`.

### Kết nối Antigravity

Trong Antigravity, mở **MCP Servers → Open MCP Config** và thêm một entry server toàn cục. Dùng cùng executable và script:

```json
{
  "mcpServers": {
    "plane": {
      "command": "/Users/TEN_USER/.local/share/plane-mcp/.venv/bin/python",
      "args": ["/Users/TEN_USER/.local/share/plane-mcp/plane_mcp.py"]
    }
  }
}
```

Thay `TEN_USER` bằng tên thư mục home trên macOS. Giữ nguyên các entry `mcpServers` khác đang có. Lưu xong chọn **Refresh** hoặc khởi động lại Antigravity. Đây là MCP toàn cục của user, không cần chép config vào từng workspace.

Trên Windows, dùng đường dẫn `.venv\\Scripts\\python.exe` và escape dấu gạch chéo ngược trong JSON (ví dụ `C:\\Users\\TEN_USER\\.local\\share\\plane-mcp\\.venv\\Scripts\\python.exe`).

### Chọn project

- Nếu đã biết task, hỏi bằng mã đầy đủ như `get DEMOPROJECT-123`; MCP tự định tuyến theo prefix.
- Khi liệt kê task, nêu project key hoặc identifier, ví dụ `list my Todo and In Progress tasks in demo` hoặc `projects=DEMOPROJECT`.
- Nếu không chỉ định project, MCP dùng `default_project`.
- `projects=all` nghĩa là tất cả project user đã cấu hình, không mở quyền sang project khác.

Project ID được đọc từ config của user, không phải từ repo/workspace hiện tại. Hướng dẫn workspace nên nói rõ project key cần dùng khi liệt kê task.

### Cập nhật

Khi muốn kiểm tra có bản mới, chạy:

```sh
plane-mcp update
```

Updater lấy version tags từ GitHub và hỏi **Update / Keep current / Skip this version**. Chỉ khi chọn Update thì mới cài; config và token Keychain được giữ nguyên. Nếu cài dependency lỗi, updater quay lại phiên bản code trước đó. Khi Skip, version đó được ghi nhớ; bản phát hành mới hơn vẫn được thông báo. Chạy `plane-mcp update --force` để xem lại bản đã Skip. Sau khi cập nhật, restart MCP host.

Không có dịch vụ update chạy nền và phiên bản đang cài không tự thay đổi. Để phát hành bản mới, maintainer push một semantic version tag, ví dụ `v0.2.0`. User tự chọn thời điểm cập nhật.

Các lệnh khác:

```sh
plane-mcp status
plane-mcp setup
```

### Khắc phục sự cố

- `plane-mcp: command not found`: thêm `~/.local/bin` vào `PATH`, sau đó mở terminal mới.
- Lỗi token: chạy lại `plane-mcp setup` và nhập Plane API token hợp lệ.
- Không tìm thấy project: kiểm tra workspace slug, project UUID và quyền token trên Plane.
- Prefix task bị từ chối: thêm Plane identifier tương ứng vào `projects`.
- Codex hoặc Antigravity không hiện server: kiểm tra đường dẫn executable, rồi reload/khởi động lại MCP server.
- Update báo file local đã thay đổi: không ghi đè thủ công thư mục cài do updater quản lý. Sao lưu phần cần giữ, cài lại, và giữ config user trong `~/.config`.

### Phát triển

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PLANE_MCP_CONFIG=/path/to/test-config.json PLANE_API_TOKEN=test-token .venv/bin/python -m unittest discover -s tests -v
```

### Riêng tư và giới hạn

- Plane API credential chỉ được gửi tới Plane API host đã cấu hình. Token lưu trong credential manager của hệ điều hành và không ghi vào JSON config.
- MCP kiểm tra allowlist trước khi đọc task hoặc liệt kê work items. Quyền Plane của token vẫn là lớp giới hạn truy cập bên dưới.
- GitHub được dùng để tải source và kiểm tra version tags. Dự án không vận hành application server.
- Cập nhật chạy qua terminal. MCP clients không có hộp thoại xác nhận cập nhật chung, nên bản này không hiện modal có nút bấm ngay trong Codex hay Antigravity.
- Linux cần phiên desktop đăng nhập và Secret Service provider hoạt động. Linux headless không có dịch vụ này chưa được hỗ trợ trong luồng setup an toàn.
- **Giấy phép:** chưa được chỉ định. Repo công khai không tự cấp quyền phân phối lại hay chỉnh sửa phần mềm; maintainer cần thêm license trước khi cấp các quyền đó.
