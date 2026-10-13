$ErrorActionPreference = "Stop"

$repo = "https://github.com/ydanh-dev/plane_mcp.git"
$dest = Join-Path $HOME ".local\share\plane-mcp"
$bin = Join-Path $HOME ".local\bin"
$python = Get-Command python -ErrorAction SilentlyContinue

if (-not $python) { throw "Python 3.10+ is required. Install Python and retry." }
& $python.Source -c "import sys; raise SystemExit(sys.version_info < (3, 10))"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ is required. Install Python and retry." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is required to install and update versioned releases." }

if (Test-Path (Join-Path $dest ".git")) {
    Write-Host "Plane MCP is already installed at $dest"
    & (Join-Path $dest ".venv\Scripts\python.exe") (Join-Path $dest "manage.py") update
    exit $LASTEXITCODE
}
if (Test-Path $dest) { throw "Refusing to overwrite existing non-managed path: $dest" }

New-Item -ItemType Directory -Force -Path (Split-Path $dest), $bin | Out-Null
git clone --depth 1 $repo $dest
if ($LASTEXITCODE -ne 0) { throw "Could not clone $repo" }
& $python.Source -m venv (Join-Path $dest ".venv")
if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment" }
$venvPython = Join-Path $dest ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $dest "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install Plane MCP dependencies" }

$shim = Join-Path $bin "plane-mcp.cmd"
@"
@echo off
"$venvPython" "$dest\manage.py" %*
"@ | Set-Content -Encoding ascii $shim
& $venvPython (Join-Path $dest "manage.py") setup
