#!/bin/sh
set -eu

OWNER="ydanh-dev/plane_mcp"
DEST="$HOME/.local/share/plane-mcp"
BIN="$HOME/.local/bin"

if [ -f "$DEST/manage.py" ] && [ -x "$DEST/.venv/bin/python" ] && { [ -f "$DEST/.plane-mcp-managed" ] || [ -d "$DEST/.git" ]; }; then
  echo "Plane MCP is already installed at $DEST"
  if [ -t 0 ]; then
    "$DEST/.venv/bin/python" "$DEST/manage.py" update
  elif ( : </dev/tty ) 2>/dev/null; then
    "$DEST/.venv/bin/python" "$DEST/manage.py" update </dev/tty
  else
    echo "Interactive update check requires a terminal." >&2
    exit 1
  fi
  exit 0
fi
if [ -e "$DEST" ]; then
  echo "Refusing to overwrite incomplete or unmanaged path: $DEST" >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST")" "$BIN" "$DEST"
TEMP_DIR=$(mktemp -d)
INSTALL_COMPLETE=0
cleanup() {
  result=$?
  rm -rf "$TEMP_DIR"
  if [ "$INSTALL_COMPLETE" -eq 0 ]; then rm -rf "$DEST"; fi
  exit "$result"
}
trap cleanup EXIT HUP INT TERM

VERSION=$(curl -fsSL "https://raw.githubusercontent.com/$OWNER/main/VERSION")
printf '%s\n' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || {
  echo "GitHub returned an invalid Plane MCP version." >&2
  exit 1
}
curl -fsSL "https://github.com/$OWNER/archive/refs/tags/v$VERSION.tar.gz" -o "$TEMP_DIR/source.tar.gz"
tar -xzf "$TEMP_DIR/source.tar.gz" --strip-components=1 -C "$DEST"
if [ "$(cat "$DEST/VERSION")" != "$VERSION" ]; then
  echo "Downloaded source version does not match the GitHub version manifest." >&2
  exit 1
fi

curl -fsSL https://astral.sh/uv/install.sh -o "$TEMP_DIR/uv-install.sh"
UV_INSTALL_DIR="$DEST/.tools" UV_NO_MODIFY_PATH=1 sh "$TEMP_DIR/uv-install.sh"
UV="$DEST/.tools/uv"
UV_PYTHON_INSTALL_DIR="$DEST/.python" "$UV" python install 3.12
UV_PYTHON_INSTALL_DIR="$DEST/.python" "$UV" venv --python 3.12 "$DEST/.venv"
"$UV" pip install --python "$DEST/.venv/bin/python" -r "$DEST/requirements.txt"

cat > "$BIN/plane-mcp" <<'EOF'
#!/bin/sh
exec "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/manage.py" "$@"
EOF
chmod 755 "$BIN/plane-mcp"
touch "$DEST/.plane-mcp-managed"
INSTALL_COMPLETE=1

# `curl | sh` uses stdin for the installer, so pass the terminal only to setup.
if [ ! -t 0 ]; then
  if ! ( : </dev/tty ) 2>/dev/null; then
    echo "Interactive setup requires a terminal." >&2
    exit 1
  fi
  "$BIN/plane-mcp" setup </dev/tty
else
  "$BIN/plane-mcp" setup
fi
