# Plane MCP

User-wide MCP server for Plane. It runs locally by default and can optionally use a Cloud Run backend; a Cloudflare Workers backend prototype is also included. One installation serves multiple configured Plane projects and clients including Codex CLI/Desktop and Antigravity.

> **Security:** the repository contains no Plane token or per-user project configuration. Setup stores the token in the operating system credential store: macOS Keychain, Windows Credential Manager, or Linux Secret Service. Each user maintains their own project allowlist and detected Plane user ID in `~/.config/plane-mcp/config.json`.

## English

### What it does

- Lists configured Plane projects and reads tasks, descriptions, comments, attachments, and images.
- Routes a task detail request by its full Plane code (for example, `DEMOPROJECT-123`). The code prefix must match a project in the local allowlist.
- Lists tasks for the configured default project, or for explicitly selected project keys/identifiers. `all` means all projects in that user's allowlist.
- Installs one local server process per user. Codex and Antigravity can point to the same installation; projects are not separate MCP server entries.
- Checks for updates when you run `plane-mcp update` or rerun the installer, then asks whether to update now.

### Requirements

- macOS 11+, Windows, or desktop Linux with a Secret Service provider (such as GNOME Keyring or KDE Wallet).
- No preinstalled Python or Git is required. The installer downloads `uv`, a private Python 3.12 runtime, and the MCP dependencies into the Plane MCP install directory.
- Internet access and `curl`/`tar` on macOS/Linux, or PowerShell on Windows.
- A Plane API token with read access to the projects you configure.
- Codex CLI/Desktop or Antigravity for MCP use.

### Install

Review the installer before running it:

```sh
curl -fsSLo /tmp/plane-mcp-install.sh https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh
less /tmp/plane-mcp-install.sh
sh /tmp/plane-mcp-install.sh
```

On macOS/Linux, the installer downloads the latest tagged source, installs a private Python runtime and dependencies under `~/.local/share/plane-mcp`, then starts first-time setup. It does not overwrite an existing directory. To use a one-line bootstrap, run:

```sh
curl -fsSL https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh | sh
```

The one-line form executes the downloaded installer directly. Inspect it first if you prefer to review installation scripts before execution.

For manual source review, download [Plane MCP v0.3.0 (.zip)](https://github.com/ydanh-dev/plane_mcp/archive/refs/tags/v0.3.0.zip).

On Windows, the installer also installs a private Python runtime and dependencies; Python and Git do not need to be installed first. To inspect the script before running it:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 -OutFile "$env:TEMP\plane-mcp-install.ps1"
notepad "$env:TEMP\plane-mcp-install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\plane-mcp-install.ps1"
```

Or install directly from PowerShell:

```powershell
irm https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 | iex
```

Linux requires an active Secret Service implementation on the desktop session. If no secure credential backend is available, setup stops instead of writing the Plane token to a plain-text file. The installer does not install system credential services or change Linux package settings.

### First-time setup

This installer is configured for ApeTech's self-hosted Plane (`https://plane.apetechs.co/api/v1`, workspace `apetechs`), so those values are not prompted. Setup asks for your API token and project UUIDs only. It identifies the account through Plane's current-user API, and reads project names and task-code prefixes from Plane. After each UUID, answer whether to add another project; choosing No finishes setup. The first project becomes the default.

The resulting JSON contains configuration only, not the API token. Protect the file because it identifies the projects this installation may access:

| Data | Where it is stored | Protection |
| --- | --- | --- |
| User and project settings | `~/.config/plane-mcp/config.json` | File permissions on macOS/Linux; user-profile ACL on Windows |
| Plane API token | OS credential store, service `plane-mcp` | macOS Keychain, Windows Credential Manager, or Linux Secret Service |

Project keys are generated from each Plane identifier, so setup needs no project alias. Enter the full project UUID from its Plane URL. The current Plane user ID is detected automatically and used as the default assignee filter. Only configured project UUIDs can be read. A task code with an unknown prefix is rejected before an API request.

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

### Optional Cloud Run backend

`Dockerfile` runs the same MCP over Streamable HTTP at `/mcp`. Set `PLANE_MCP_HTTP=1` and provide `PLANE_MCP_CONFIG_JSON` with the Plane URL, workspace, and server project allowlist only. Do not put a Plane token in the image or Cloud Run environment.

`cloud-run.env.yaml` is an operator-local Cloud Run environment file; the installer does not read it. Its project list is server-wide, not a per-user setup file.

After deployment, run `plane-mcp remote https://YOUR-SERVICE.run.app/mcp` and reload Codex or Antigravity. The local MCP process is a small bridge: it reads each user's token from the OS credential store and sends it over HTTPS to the configured service; the service forwards it to the configured Plane host for that request and does not save it. Use only a Cloud Run service you control and trust. Run `plane-mcp remote off` to return to local Plane API calls.

Cloud Run requires an active billing account. Its free tier is usage-based, not a spending cap, so usage beyond the free quota can cost money.

### Experimental Cloudflare Workers backend

The stateless Worker can be deployed at `https://plane-mcp.apetechs.workers.dev/mcp`. Project UUIDs are sent by the local MCP from each user's own config with each request; the Worker verifies the UUID and task-code prefix against Plane using that user's token. The Worker does not keep a shared project list.

After installing and configuring the local Plane MCP (including the user's own Plane token in the OS credential store), run `plane-mcp remote https://plane-mcp.apetechs.workers.dev/mcp`, then reload Codex or Antigravity. To develop or deploy the Worker, install Node.js 20+, then from `cloudflare/worker` run `npm ci`, `npm test`, and `npx wrangler deploy`. Only the shared Plane API URL and workspace slug belong in Worker variables; project UUIDs and Plane tokens do not.

The local bridge reads each user's Plane token from the OS credential store and sends it to the configured Worker over HTTPS for each uncached call. The Worker forwards it to Plane in memory and does not store it. Plane's own permissions for that token are the access boundary; this public Worker does not impose a separate company-wide per-user project policy. Only use a Worker you control and trust. The local bridge caches successful tool responses for 60 seconds per token; `force_refresh=true` bypasses that cache.

Cloudflare advertises a Free plan with no card required, 100,000 requests/day, and a 10 ms CPU limit per request ([official limits](https://developers.cloudflare.com/workers/platform/limits/), [Workers overview](https://www.cloudflare.com/products/workers/)). The CPU cap is the main risk: large task lists or image-heavy details may exceed it. Inline images over 128 KiB are currently skipped; benchmark before asking other users to switch.

### Creating tasks with images

Two write tools let an assistant (Codex, Claude, ...) create a task and attach pictures to it:

- `create_plane_task(name, description, project, priority, image_paths)` creates a task in exactly one configured project, then attaches each image.
- `attach_images_to_plane_task(task_identifier, image_paths)` adds images to an existing task.

Rules: the Plane API token must have **write** access (the read-only token from the first setup may not). `image_paths` are files on the same computer as the MCP process (png, jpg, gif, webp, bmp, up to 20 MiB each); every file is checked, including its real content, before anything is written, so a bad path creates nothing. If a picture fails to upload, the task is still reported as created and the failed files are listed; do not repeat the call blindly or you may get a duplicate task. These tools always talk to Plane directly from the local process, even when `plane-mcp remote` is on, because a cloud backend cannot read files on the user's computer. Ask the assistant to name the project explicitly.

### Slow machines and checking your setup

The installer is safe to run again: if an earlier install failed or was interrupted, running the same command continues with whatever is missing (source, `uv`, Python, dependencies, setup) instead of starting over, and an older install it did not create is moved aside to `plane-mcp.backup-<time>`, never deleted.

Local mode runs the Plane MCP process on the user's machine: it fetches the task list, parses HTML and writes images to disk. The model itself never runs locally, so no GPU is used. Measured on one Mac, listing tasks used about 0.05 s of CPU and 65 MB of RAM, so the wait is mostly network time to Plane. `plane-mcp setup` asks whether to use the company Cloudflare Worker; with it, the Plane fetching and HTML work run in the cloud and the local process is a thin bridge, at the cost of one extra network hop (a few hundred ms to a few seconds slower in the same test). Switch any time:

```sh
plane-mcp remote https://YOUR-SERVICE/mcp   # heavy work moves to the cloud
plane-mcp remote off                        # back to local
```

The token still lives only in the OS credential store; the local process becomes a thin bridge.

After `plane-mcp setup` (which asks for the API token and stores it in the credential store), run:

```sh
plane-mcp doctor
```

It checks the config, that a token is stored (never printed), the Plane API and each project, and, in remote mode, the backend's `/healthz`, with a timing for each step. It exits non-zero if anything fails. Compare timings with and without `plane-mcp remote` on the slow machine before rolling out. Project, state, member and label lookups are cached for 10 minutes; task lists and details for 60 seconds (`force_refresh=true` bypasses the cache).

### Updates

Run this when you want to check for a release:

```sh
plane-mcp update
```

The updater checks the small `VERSION` file on GitHub and asks `Update now? [y/N]`. Yes downloads the matching tagged source archive from GitHub and installs it; No keeps the current version. This update path needs neither Git nor Cloudflare. It preserves user config and the OS credential-store token, and rolls source files back if installation fails. Restart the MCP host after updating. Updates are checked when you run this command or rerun the installer; MCP clients do not provide a shared interactive update prompt.

There is no background service, so users will not get a pop-up while the MCP is idle. To check, run `plane-mcp update`; the maintainer publishes a new `VERSION` and matching semantic version tag (for example, `0.2.3` and `v0.2.3`), then users choose whether to install it.

Other commands:

```sh
plane-mcp doctor
plane-mcp status
plane-mcp setup
plane-mcp remote https://YOUR-SERVICE.run.app/mcp
plane-mcp remote off
```

### Troubleshooting

- `plane-mcp: command not found`: add `~/.local/bin` to `PATH`, then open a new terminal. On Windows, open a new PowerShell after installation or call `%USERPROFILE%\.local\bin\plane-mcp.cmd` directly.
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
cd cloudflare/worker && npm ci && npm test && npx wrangler deploy --dry-run
```

### Privacy and limitations

- In local mode, Plane credentials are sent directly to the configured Plane API host. In Cloud Run or Cloudflare Worker mode, the token also passes through the configured HTTPS MCP service in memory; it remains in the OS credential store locally and is not written to JSON or persisted by this MCP.
- Project allowlisting is enforced by the MCP before reading task details or listing work items. Plane permissions remain the underlying access boundary.
- GitHub is used to download the installer/source and check the `VERSION` file. This release update flow does not use Cloudflare or Git. Cloud hosting remains optional.
- Updating is a terminal workflow. MCP clients do not share a common update-confirmation dialog, so this release does not display a clickable update modal inside Codex or Antigravity.
- Linux setup needs a logged-in desktop session with a working Secret Service provider. Headless Linux without that service is not supported by the secure setup flow.
- **License:** not yet specified. Public visibility does not itself grant permission to redistribute or modify this software; the maintainer must add a license before granting those rights.

## Tiếng Việt

### Giới thiệu

Plane MCP dùng chung ở cấp user và mặc định chạy cục bộ. Có thể chuyển phần xử lý Plane sang Cloud Run; repo cũng có prototype backend Cloudflare Workers. Một lần cài đặt vẫn dùng được với nhiều project Plane và MCP client như Codex CLI/Desktop, Antigravity.

> **Bảo mật:** repo không chứa Plane token hay cấu hình project cá nhân. Wizard lưu token vào kho credential của hệ điều hành: Keychain trên macOS, Credential Manager trên Windows, hoặc Secret Service trên Linux. Mỗi user có allowlist project và Plane user ID được nhận diện tự động trong `~/.config/plane-mcp/config.json`.

### Chức năng

- Liệt kê các project Plane đã cấu hình và đọc task, mô tả, bình luận, file đính kèm, hình ảnh.
- Tự định tuyến khi đọc task bằng mã đầy đủ, ví dụ `DEMOPROJECT-123`. Prefix phải khớp với một project trong allowlist cục bộ.
- Mặc định liệt kê task ở project mặc định; có thể chọn project bằng key/identifier. `all` chỉ có nghĩa là tất cả project trong allowlist của user đó.
- Chạy một MCP local cho mỗi user. Codex và Antigravity cùng trỏ vào một cài đặt; không cần tạo MCP riêng cho từng project.
- Kiểm tra cập nhật khi chạy `plane-mcp update` hoặc chạy lại installer; terminal sẽ hỏi có cập nhật ngay không.

### Yêu cầu

- macOS 11+, Windows, hoặc Linux desktop có Secret Service (ví dụ GNOME Keyring hoặc KDE Wallet).
- Không cần cài Python hay Git trước. Installer tải `uv`, Python 3.12 riêng và các thư viện MCP vào thư mục cài Plane MCP.
- Cần Internet và `curl`/`tar` trên macOS/Linux, hoặc PowerShell trên Windows.
- Plane API token có quyền đọc các project cần cấu hình.
- Codex CLI/Desktop hoặc Antigravity để sử dụng MCP.

### Cài đặt

Nên xem nội dung installer trước khi chạy:

```sh
curl -fsSLo /tmp/plane-mcp-install.sh https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh
less /tmp/plane-mcp-install.sh
sh /tmp/plane-mcp-install.sh
```

Trên macOS/Linux, installer tải source theo tag mới nhất, tự cài Python riêng cùng dependencies vào `~/.local/share/plane-mcp`, rồi mở setup lần đầu. Installer không ghi đè thư mục đã tồn tại. Dùng lệnh bootstrap một dòng:

```sh
curl -fsSL https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.sh | sh
```

Dạng một dòng thực thi trực tiếp installer vừa tải về. Nếu muốn xem script trước, hãy dùng cách tải file rồi đọc ở trên.

Nếu muốn xem source thủ công, tải [Plane MCP v0.3.0 (.zip)](https://github.com/ydanh-dev/plane_mcp/archive/refs/tags/v0.3.0.zip).

Trên Windows, installer cũng tự cài Python riêng và dependencies; không cần cài Python hay Git trước. Để xem script trước khi chạy:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 -OutFile "$env:TEMP\plane-mcp-install.ps1"
notepad "$env:TEMP\plane-mcp-install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\plane-mcp-install.ps1"
```

Hoặc cài trực tiếp bằng PowerShell:

```powershell
irm https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 | iex
```

Linux cần Secret Service đang hoạt động trong phiên desktop. Nếu không có credential backend bảo mật, setup sẽ dừng thay vì ghi Plane token vào file text thường. Installer không tự cài dịch vụ credential hệ thống hoặc thay đổi cấu hình package của Linux.

### Cấu hình lần đầu

Installer này đã cấu hình sẵn Plane self-host của ApeTech (`https://plane.apetechs.co/api/v1`, workspace `apetechs`), nên không hỏi hai mục đó. Setup chỉ hỏi API token và UUID project. MCP tự nhận diện tài khoản qua API current-user của Plane, rồi đọc tên project và identifier/prefix task từ Plane. Sau mỗi UUID, chọn có thêm project không; chọn No để kết thúc. Project đầu tiên tự động làm project mặc định.

File JSON chỉ chứa cấu hình, không chứa API token. Hãy bảo vệ file này vì nó xác định các project MCP được phép truy cập:

| Dữ liệu | Nơi lưu | Bảo vệ |
| --- | --- | --- |
| Cấu hình user và project | `~/.config/plane-mcp/config.json` | Quyền file trên macOS/Linux; ACL user profile trên Windows |
| Plane API token | Kho credential của OS, service `plane-mcp` | Keychain trên macOS, Credential Manager trên Windows, Secret Service trên Linux |

Project key được tạo tự động từ identifier của Plane, nên không cần nhập bí danh. Nhập đầy đủ project UUID trong URL Plane. Plane user ID được nhận diện tự động và dùng làm bộ lọc assignee mặc định. MCP chỉ đọc các UUID đã khai báo. Mã task có prefix chưa cấu hình sẽ bị từ chối trước khi gọi API.

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

### Cloud Run tùy chọn

`Dockerfile` chạy cùng MCP bằng Streamable HTTP tại `/mcp`. Cấu hình `PLANE_MCP_HTTP=1` và biến `PLANE_MCP_CONFIG_JSON`, chỉ chứa URL Plane, workspace và allowlist project của server. Không đưa Plane token vào image hoặc biến môi trường Cloud Run.

`cloud-run.env.yaml` chỉ là file môi trường Cloud Run cục bộ của người vận hành; installer không đọc file này. Danh sách project trong đó áp dụng toàn server, không phải cấu hình riêng cho từng user.

Sau khi deploy, chạy `plane-mcp remote https://YOUR-SERVICE.run.app/mcp`, rồi reload Codex hoặc Antigravity. MCP local chỉ làm cầu nối nhẹ: đọc token của từng user từ credential store của OS và gửi qua HTTPS tới service đã cấu hình; service dùng token đó để gọi Plane rồi không lưu lại. Chỉ dùng Cloud Run service mà bạn quản lý và tin cậy. Chạy `plane-mcp remote off` để quay về gọi Plane trực tiếp từ máy.

Cloud Run yêu cầu project có billing account hoạt động. Free tier tính theo mức sử dụng, không phải giới hạn chi tiêu; dùng vượt quota miễn phí có thể phát sinh phí.

### Prototype backend Cloudflare Workers

Worker stateless có thể deploy tại `https://plane-mcp.apetechs.workers.dev/mcp`. MCP local gửi UUID project từ config riêng của từng user trong mỗi request; Worker kiểm tra UUID và task-code prefix với Plane bằng token của user đó. Worker không lưu danh sách project dùng chung.

Sau khi cài và cấu hình Plane MCP local (bao gồm token Plane riêng của user trong kho credential hệ điều hành), chạy `plane-mcp remote https://plane-mcp.apetechs.workers.dev/mcp`, rồi reload Codex hoặc Antigravity. Để phát triển hoặc deploy Worker, cài Node.js 20+, vào `cloudflare/worker`, chạy `npm ci`, `npm test` và `npx wrangler deploy`. Chỉ URL Plane và workspace slug dùng chung nằm trong biến Worker; không đưa UUID project hoặc Plane token vào đó.

Local bridge lấy token của từng user từ credential store hệ điều hành và gửi qua HTTPS tới Worker cho mỗi lần gọi chưa có cache. Worker chuyển tiếp token tới Plane trong bộ nhớ rồi không lưu lại. Quyền của chính token Plane là ranh giới truy cập; Worker công khai này không áp dụng chính sách project riêng cho từng user ở phía công ty. Chỉ dùng Worker bạn quản lý và tin cậy. Local bridge cache kết quả tool thành công trong 60 giây theo từng token; `force_refresh=true` bỏ qua cache.

Cloudflare quảng bá Workers Free không cần thẻ, tối đa 100.000 request/ngày và giới hạn CPU 10 ms cho mỗi request ([giới hạn chính thức](https://developers.cloudflare.com/workers/platform/limits/), [Workers](https://www.cloudflare.com/products/workers/)). Rủi ro chính là giới hạn CPU: danh sách task lớn hoặc task có nhiều ảnh có thể vượt ngưỡng. Ảnh inline lớn hơn 128 KiB hiện bị bỏ qua; hãy benchmark trước khi chuyển user khác sang dùng.

### Tạo task kèm ảnh

Hai tool ghi cho phép trợ lý (Codex, Claude, ...) tạo task và đính kèm ảnh:

- `create_plane_task(name, description, project, priority, image_paths)` tạo task trong đúng một project đã cấu hình, rồi đính kèm từng ảnh.
- `attach_images_to_plane_task(task_identifier, image_paths)` thêm ảnh vào task có sẵn.

Quy tắc: API token của Plane phải có quyền **ghi** (token chỉ đọc từ lần setup đầu có thể chưa đủ). `image_paths` là tệp nằm trên cùng máy với tiến trình MCP (png, jpg, gif, webp, bmp, mỗi tệp tối đa 20 MiB); mọi tệp được kiểm tra, kể cả nội dung thật, trước khi ghi bất cứ thứ gì, nên đường dẫn sai sẽ không tạo gì cả. Nếu một ảnh tải lên lỗi, task vẫn được báo là đã tạo và các tệp lỗi được liệt kê; đừng gọi lại một cách mù quáng vì có thể tạo trùng task. Các tool này luôn gọi thẳng Plane từ tiến trình local, kể cả khi bật `plane-mcp remote`, vì backend cloud không đọc được tệp trên máy người dùng. Hãy yêu cầu trợ lý nêu rõ tên project.

### Máy yếu và kiểm tra cấu hình

Installer chạy lại an toàn: nếu lần cài trước bị lỗi hoặc bị ngắt, chạy lại đúng lệnh đó sẽ làm tiếp phần còn thiếu (mã nguồn, `uv`, Python, thư viện, setup) thay vì làm lại từ đầu. Bản cài cũ không do installer tạo sẽ được chuyển sang `plane-mcp.backup-<giờ>`, không bị xóa.

Chế độ local chạy tiến trình Plane MCP trên máy người dùng: tải danh sách task, phân tích HTML và ghi ảnh ra đĩa. Model không chạy trên máy nên không dùng GPU. Đo trên một máy Mac, liệt kê task chỉ tốn khoảng 0,05 giây CPU và 65 MB RAM, nên thời gian chờ chủ yếu là mạng tới Plane. `plane-mcp setup` sẽ hỏi có dùng Cloudflare Worker của công ty không; khi dùng, phần tải dữ liệu Plane và xử lý HTML chạy trên cloud, tiến trình local chỉ là cầu nối mỏng, đổi lại thêm một chặng mạng (chậm hơn vài trăm mili giây đến vài giây trong cùng bài đo). Đổi qua lại bất cứ lúc nào:

```sh
plane-mcp remote https://YOUR-SERVICE/mcp   # việc nặng chạy trên cloud
plane-mcp remote off                        # quay lại chạy local
```

Token vẫn chỉ nằm trong kho credential của hệ điều hành; tiến trình local chỉ còn là cầu nối mỏng.

Sau `plane-mcp setup` (hỏi API token và lưu vào kho credential), chạy:

```sh
plane-mcp doctor
```

Lệnh này kiểm tra file cấu hình, token đã được lưu (không in ra), Plane API và từng project, và ở chế độ remote thì thêm `/healthz` của backend, kèm thời gian từng bước. Có lỗi thì lệnh trả mã khác 0. Hãy so thời gian khi có và không có `plane-mcp remote` trên máy yếu trước khi triển khai cho cả công ty. Thông tin project, state, member, label được cache 10 phút; danh sách và chi tiết task cache 60 giây (`force_refresh=true` để bỏ qua cache).

### Cập nhật

Khi muốn kiểm tra có bản mới, chạy:

```sh
plane-mcp update
```

Updater đọc file `VERSION` nhỏ trên GitHub rồi hỏi `Update now? [y/N]`. Chọn Yes để tải source theo tag tương ứng và cài; No giữ nguyên bản hiện tại. Cách update này không cần Git hay Cloudflare. Config user cùng token trong credential store được giữ nguyên; nếu cài đặt lỗi, updater khôi phục các file source. Sau khi cập nhật, restart MCP host. Việc kiểm tra chạy khi gọi lệnh này hoặc chạy lại installer; MCP client không có hộp thoại xác nhận cập nhật dùng chung.

Không có dịch vụ chạy nền nên user không nhận popup khi MCP đang nghỉ. Để kiểm tra, chạy `plane-mcp update`; maintainer phát hành `VERSION` và tag trùng nhau (ví dụ `0.2.3` và `v0.2.3`), sau đó user chọn có cập nhật hay không.

Các lệnh khác:

```sh
plane-mcp doctor
plane-mcp status
plane-mcp setup
plane-mcp remote https://YOUR-SERVICE.run.app/mcp
plane-mcp remote off
```

### Khắc phục sự cố

- `plane-mcp: command not found`: thêm `~/.local/bin` vào `PATH`, sau đó mở terminal mới. Trên Windows, mở PowerShell mới sau khi cài hoặc chạy trực tiếp `%USERPROFILE%\.local\bin\plane-mcp.cmd`.
- Lỗi token: chạy lại `plane-mcp setup` và nhập Plane API token hợp lệ.
- Không tìm thấy project: kiểm tra project UUID và quyền token trên Plane.
- Prefix task bị từ chối: chạy lại setup và thêm project tương ứng bằng UUID.
- Codex hoặc Antigravity không hiện server: kiểm tra đường dẫn executable, rồi reload/khởi động lại MCP server.
- Update báo file local đã thay đổi: không ghi đè thủ công thư mục cài do updater quản lý. Sao lưu phần cần giữ, cài lại, và giữ config user trong `~/.config`.

### Phát triển

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
PLANE_MCP_CONFIG=/path/to/test-config.json PLANE_API_TOKEN=test-token .venv/bin/python -m unittest discover -s tests -v
cd cloudflare/worker && npm ci && npm test && npx wrangler deploy --dry-run
```

### Riêng tư và giới hạn

- Ở chế độ local, Plane token được gửi thẳng tới Plane API host đã cấu hình. Ở chế độ Cloud Run hoặc Cloudflare Worker, token cũng đi qua MCP service HTTPS đã cấu hình trong bộ nhớ; token vẫn nằm trong credential store của OS và không được ghi vào JSON hay lưu lại bởi MCP.
- MCP kiểm tra allowlist trước khi đọc task hoặc liệt kê work items. Quyền Plane của token vẫn là lớp giới hạn truy cập bên dưới.
- GitHub được dùng để tải installer/source và kiểm tra file `VERSION`. Cơ chế update trong bản này không dùng Cloudflare hay Git. Cloud hosting vẫn là tùy chọn.
- Cập nhật chạy qua terminal. MCP clients không có hộp thoại xác nhận cập nhật chung, nên bản này không hiện modal có nút bấm ngay trong Codex hay Antigravity.
- Linux cần phiên desktop đăng nhập và Secret Service provider hoạt động. Linux headless không có dịch vụ này chưa được hỗ trợ trong luồng setup an toàn.
- **Giấy phép:** chưa được chỉ định. Repo công khai không tự cấp quyền phân phối lại hay chỉnh sửa phần mềm; maintainer cần thêm license trước khi cấp các quyền đó.
