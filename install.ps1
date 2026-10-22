$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$owner = "ydanh-dev/plane_mcp"
$dest = Join-Path $HOME ".local\share\plane-mcp"
$bin = Join-Path $HOME ".local\bin"
$venvPython = Join-Path $dest ".venv\Scripts\python.exe"

if ((Test-Path (Join-Path $dest "manage.py")) -and (Test-Path $venvPython) -and
    ((Test-Path (Join-Path $dest ".plane-mcp-managed")) -or (Test-Path (Join-Path $dest ".git")))) {
    Write-Host "Plane MCP is already installed at $dest"
    & $venvPython (Join-Path $dest "manage.py") update
    if ($LASTEXITCODE -ne 0) { throw "Plane MCP update check failed." }
    return
}
if (Test-Path $dest) { throw "Refusing to overwrite incomplete or unmanaged path: $dest" }

$version = (Invoke-RestMethod "https://raw.githubusercontent.com/$owner/main/VERSION").Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "GitHub returned an invalid Plane MCP version." }

$work = Join-Path ([IO.Path]::GetTempPath()) ("plane-mcp-" + [guid]::NewGuid().ToString("N"))
$complete = $false
try {
    New-Item -ItemType Directory -Force -Path $work, $dest, $bin | Out-Null
    $archive = Join-Path $work "source.zip"
    $extracted = Join-Path $work "source"
    Invoke-WebRequest "https://github.com/$owner/archive/refs/tags/v$version.zip" -OutFile $archive
    Expand-Archive -Path $archive -DestinationPath $extracted
    $source = Get-ChildItem $extracted -Directory | Select-Object -First 1
    if (-not $source -or -not (Test-Path (Join-Path $source.FullName "manage.py"))) {
        throw "GitHub returned an unexpected Plane MCP source archive."
    }
    if ((Get-Content (Join-Path $source.FullName "VERSION") -Raw).Trim() -ne $version) {
        throw "Downloaded source version does not match the GitHub version manifest."
    }
    Copy-Item -Path (Join-Path $source.FullName "*") -Destination $dest -Recurse -Force

    $env:UV_INSTALL_DIR = Join-Path $dest ".tools"
    $env:UV_NO_MODIFY_PATH = "1"
    $uvInstaller = Join-Path $work "uv-install.ps1"
    Invoke-WebRequest "https://astral.sh/uv/install.ps1" -OutFile $uvInstaller
    & $uvInstaller

    $uv = Join-Path $dest ".tools\uv.exe"
    if (-not (Test-Path $uv)) { throw "Could not install the local Python package manager." }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $dest ".python"
    & $uv python install 3.12
    if ($LASTEXITCODE -ne 0) { throw "Could not install the private Python runtime." }
    & $uv venv --python 3.12 (Join-Path $dest ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Plane MCP environment." }
    & $uv pip install --python $venvPython -r (Join-Path $dest "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Could not install Plane MCP dependencies." }

    $shim = Join-Path $bin "plane-mcp.cmd"
    @"
@echo off
"$venvPython" "$dest\manage.py" %*
"@ | Set-Content -Encoding ascii $shim
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathEntries = @($userPath -split ';' | Where-Object { $_ })
    if ($pathEntries -notcontains $bin) {
        [Environment]::SetEnvironmentVariable("Path", (($pathEntries + $bin) -join ';'), "User")
    }
    $env:Path = "$bin;$env:Path"
    New-Item -ItemType File -Force -Path (Join-Path $dest ".plane-mcp-managed") | Out-Null
    $complete = $true

    Write-Host "Installed Plane MCP. Python and dependencies are kept inside $dest."
    & $venvPython (Join-Path $dest "manage.py") setup
    if ($LASTEXITCODE -ne 0) { throw "Plane MCP setup did not complete. Run `"$shim`" setup to try again." }
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    if (-not $complete) { Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue }
}
