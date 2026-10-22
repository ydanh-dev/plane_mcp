# Plane MCP

User-wide MCP server for Plane. It runs locally by default and can optionally use a Cloud Run backend; a Cloudflare Workers backend prototype is also included. One installation serves multiple configured Plane projects and clients including Codex CLI/Desktop and Antigravity.

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

For manual source review, download [Plane MCP v0.2.0 (.zip)](https://github.com/ydanh-dev/plane_mcp/releases/download/v0.2.0/plane-mcp-v0.2.0.zip).

On Windows, download and inspect `install.ps1`, then run it in PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 -OutFile "$env:TEMP\plane-mcp-install.ps1"
notepad "$env:TEMP\plane-mcp-install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\plane-mcp-install.ps1"
```

Linux requires an active Secret Service implementation on the desktop session. If no secure credential backend is available, setup stops instead of writing the Plane token to a plain-text file.

### First-time setup

This installer is configured for ApeTech's self-hosted Plane (`https://plane.apetechs.co/api/v1`, workspace `apetechs`), so those values are not prompted. Setup asks for your display name (optional), API token, and each project's UUID. It reads the project name and task-code prefix from Plane; a short project key is optional and defaults to that prefix. Press Enter at the UUID prompt when finished. On a fresh setup, the first project becomes the default, so there is no separate default-project prompt.

The resulting JSON contains configuration only, not the API token. Protect the file because it identifies the projects this installation may access:

| Data | Where it is stored | Protection |
| --- | --- | --- |
| User and project settings | `~/.config/plane-mcp/config.json` | File permissions on macOS/Linux; user-profile ACL on Windows |
| Plane API token | OS credential store, service `plane-mcp` | macOS Keychain, Windows Credential Manager, or Linux Secret Service |

Project keys are optional personal aliases such as `np`, `busan`, or `ntssv2`. The Plane identifier is the prefix in task codes and is read automatically; enter the full project UUID from its Plane URL. Only configured project UUIDs can be read. A task code with an unknown prefix is rejected before an API request.

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

After deployment, run `plane-mcp remote https://YOUR-SERVICE.run.app/mcp` and reload Codex or Antigravity. The local MCP process is a small bridge: it reads each user's token from the OS credential store and sends it over HTTPS to the configured service; the service forwards it to the configured Plane host for that request and does not save it. Use only a Cloud Run service you control and trust. Run `plane-mcp remote off` to return to local Plane API calls.

Cloud Run requires an active billing account. Its free tier is usage-based, not a spending cap, so usage beyond the free quota can cost money.

### Experimental Cloudflare Workers backend

The stateless Worker is deployed at `https://plane-mcp.apetechs.workers.dev/mcp` with the NP, Busan, and NTSSv2 projects allowlisted. Its health endpoint and MCP initialization/tool discovery are verified. A live Plane API request and end-to-end task latency benchmark have not been run.

After installing and configuring the local Plane MCP (including the user's own Plane token in the OS credential store), run `plane-mcp remote https://plane-mcp.apetechs.workers.dev/mcp`, then reload Codex or Antigravity. To develop the Worker, install Node.js 20+, then from `cloudflare/worker` run `npm ci` and `npm test`. For redeploys, pass the production `PLANE_PROJECTS_JSON` and `PLANE_DEFAULT_PROJECT` as Wrangler `--var` values; copy the existing values from the Cloudflare dashboard because project IDs are intentionally not committed here. Never put Plane tokens in these variables.

The local bridge still reads each user's Plane token from the OS credential store and sends it to the configured Worker over HTTPS for each uncached call. The Worker forwards it to Plane in memory and does not store it. Only deploy to an account you control and trust. The local bridge caches successful tool responses for 60 seconds per token; `force_refresh=true` bypasses that cache.

Cloudflare advertises a Free plan with no card required, 100,000 requests/day, and a 10 ms CPU limit per request ([official limits](https://developers.cloudflare.com/workers/platform/limits/), [Workers overview](https://www.cloudflare.com/products/workers/)). The CPU cap is the main risk: large task lists or image-heavy details may exceed it. Inline images over 128 KiB are currently skipped; benchmark before asking other users to switch.

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
plane-mcp remote https://YOUR-SERVICE.run.app/mcp
plane-mcp remote off
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
cd cloudflare/worker && npm ci && npm test && npx wrangler deploy --dry-run
```

### Privacy and limitations

- In local mode, Plane credentials are sent directly to the configured Plane API host. In Cloud Run or Cloudflare Worker mode, the token also passes through the configured HTTPS MCP service in memory; it remains in the OS credential store locally and is not written to JSON or persisted by this MCP.
- Project allowlisting is enforced by the MCP before reading task details or listing work items. Plane permissions remain the underlying access boundary.
- GitHub is used to download source and check release tags. Cloud hosting is optional; the Cloudflare Worker is only a local-tested prototype and the operator controls any deployment.
- Updating is a terminal workflow. MCP clients do not share a common update-confirmation dialog, so this release does not display a clickable update modal inside Codex or Antigravity.
- Linux setup needs a logged-in desktop session with a working Secret Service provider. Headless Linux without that service is not supported by the secure setup flow.
- **License:** not yet specified. Public visibility does not itself grant permission to redistribute or modify this software; the maintainer must add a license before granting those rights.

## Tiếng Việt

### Giới thiệu

Plane MCP dùng chung ở cấp user và mặc định chạy cục bộ. Có thể chuyển phần xử lý Plane sang Cloud Run; repo cũng có prototype backend Cloudflare Workers. Một lần cài đặt vẫn dùng được với nhiều project Plane và MCP client như Codex CLI/Desktop, Antigravity.

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

Nếu muốn xem source thủ công, tải [Plane MCP v0.2.0 (.zip)](https://github.com/ydanh-dev/plane_mcp/releases/download/v0.2.0/plane-mcp-v0.2.0.zip).

Trên Windows, tải và đọc `install.ps1`, sau đó chạy bằng PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/ydanh-dev/plane_mcp/main/install.ps1 -OutFile "$env:TEMP\plane-mcp-install.ps1"
notepad "$env:TEMP\plane-mcp-install.ps1"
powershell -ExecutionPolicy Bypass -File "$env:TEMP\plane-mcp-install.ps1"
```

Linux cần Secret Service đang hoạt động trong phiên desktop. Nếu không có credential backend bảo mật, setup sẽ dừng thay vì ghi Plane token vào file text thường.

### Cấu hình lần đầu

Installer này đã cấu hình sẵn Plane self-host của ApeTech (`https://plane.apetechs.co/api/v1`, workspace `apetechs`), nên không hỏi hai mục đó. Setup hỏi display name (có thể để trống), API token và UUID của từng project. Tên project cùng identifier/prefix task được đọc tự động từ Plane; project key ngắn là tùy chọn và mặc định lấy theo prefix. Nhấn Enter tại câu hỏi UUID khi đã nhập xong. Khi cài mới, project đầu tiên tự động làm project mặc định.

File JSON chỉ chứa cấu hình, không chứa API token. Hãy bảo vệ file này vì nó xác định các project MCP được phép truy cập:

| Dữ liệu | Nơi lưu | Bảo vệ |
| --- | --- | --- |
| Cấu hình user và project | `~/.config/plane-mcp/config.json` | Quyền file trên macOS/Linux; ACL user profile trên Windows |
| Plane API token | Kho credential của OS, service `plane-mcp` | Keychain trên macOS, Credential Manager trên Windows, Secret Service trên Linux |

Project key là bí danh cá nhân tùy chọn, ví dụ `np`, `busan`, `ntssv2`. Plane tự cung cấp identifier/prefix; nhập đầy đủ project UUID trong URL Plane. MCP chỉ đọc các UUID đã khai báo. Mã task có prefix chưa cấu hình sẽ bị từ chối trước khi gọi API.

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

Sau khi deploy, chạy `plane-mcp remote https://YOUR-SERVICE.run.app/mcp`, rồi reload Codex hoặc Antigravity. MCP local chỉ làm cầu nối nhẹ: đọc token của từng user từ credential store của OS và gửi qua HTTPS tới service đã cấu hình; service dùng token đó để gọi Plane rồi không lưu lại. Chỉ dùng Cloud Run service mà bạn quản lý và tin cậy. Chạy `plane-mcp remote off` để quay về gọi Plane trực tiếp từ máy.

Cloud Run yêu cầu project có billing account hoạt động. Free tier tính theo mức sử dụng, không phải giới hạn chi tiêu; dùng vượt quota miễn phí có thể phát sinh phí.

### Prototype backend Cloudflare Workers

Worker stateless đã được deploy tại `https://plane-mcp.apetechs.workers.dev/mcp`, với allowlist gồm NP, Busan và NTSSv2. Đã xác minh health endpoint, MCP initialization và danh sách tools. Chưa gọi Plane API thật và chưa benchmark thời gian đọc task end-to-end.

Sau khi cài và cấu hình Plane MCP local (bao gồm token Plane riêng của user trong kho credential hệ điều hành), chạy `plane-mcp remote https://plane-mcp.apetechs.workers.dev/mcp`, rồi reload Codex hoặc Antigravity. Để phát triển Worker, cài Node.js 20+, vào `cloudflare/worker`, chạy `npm ci` và `npm test`. Khi deploy lại, truyền `PLANE_PROJECTS_JSON` và `PLANE_DEFAULT_PROJECT` production bằng tham số Wrangler `--var`; lấy giá trị hiện tại trong Cloudflare dashboard vì project ID được cố ý giữ ngoài repo. Tuyệt đối không đưa Plane token vào các biến này.

Local bridge vẫn lấy token của từng user từ credential store hệ điều hành và gửi qua HTTPS tới Worker cho mỗi lần gọi chưa có cache. Worker chuyển tiếp token tới Plane trong bộ nhớ rồi không lưu lại. Chỉ deploy lên tài khoản bạn quản lý và tin cậy. Local bridge cache kết quả tool thành công trong 60 giây theo từng token; `force_refresh=true` bỏ qua cache.

Cloudflare quảng bá Workers Free không cần thẻ, tối đa 100.000 request/ngày và giới hạn CPU 10 ms cho mỗi request ([giới hạn chính thức](https://developers.cloudflare.com/workers/platform/limits/), [Workers](https://www.cloudflare.com/products/workers/)). Rủi ro chính là giới hạn CPU: danh sách task lớn hoặc task có nhiều ảnh có thể vượt ngưỡng. Ảnh inline lớn hơn 128 KiB hiện bị bỏ qua; hãy benchmark trước khi chuyển user khác sang dùng.

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
plane-mcp remote https://YOUR-SERVICE.run.app/mcp
plane-mcp remote off
```

### Khắc phục sự cố

- `plane-mcp: command not found`: thêm `~/.local/bin` vào `PATH`, sau đó mở terminal mới.
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
- GitHub được dùng để tải source và kiểm tra version tags. Cloud hosting là tùy chọn; Cloudflare Worker hiện chỉ là prototype đã test local, operator tự quản lý việc deploy.
- Cập nhật chạy qua terminal. MCP clients không có hộp thoại xác nhận cập nhật chung, nên bản này không hiện modal có nút bấm ngay trong Codex hay Antigravity.
- Linux cần phiên desktop đăng nhập và Secret Service provider hoạt động. Linux headless không có dịch vụ này chưa được hỗ trợ trong luồng setup an toàn.
- **Giấy phép:** chưa được chỉ định. Repo công khai không tự cấp quyền phân phối lại hay chỉnh sửa phần mềm; maintainer cần thêm license trước khi cấp các quyền đó.
