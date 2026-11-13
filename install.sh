#!/bin/sh
# Safe to run again at any time: it repairs a broken or half-finished install, updates a complete one,
# and finishes setup if it never completed. The same command always works.
set -eu

OWNER="ydanh-dev/plane_mcp"
DEST="$HOME/.local/share/plane-mcp"
BIN="$HOME/.local/bin"
CONFIG="$HOME/.config/plane-mcp/config.json"
PY="$DEST/.venv/bin/python"
UV="$DEST/.tools/uv"

BACKUP=""
TEMP_DIR=$(mktemp -d)
cleanup() {
  result=$?
  rm -rf "$TEMP_DIR"
  # A failed run keeps its progress so the next run resumes; only a user's old install is put back.
  if [ "$result" -ne 0 ] && [ -n "$BACKUP" ] && [ -e "$BACKUP" ]; then
    rm -rf "$DEST"
    mv "$BACKUP" "$DEST"
  fi
  exit "$result"
}
trap cleanup EXIT HUP INT TERM

# Run a command with a terminal on stdin (`curl | sh` uses stdin for the script itself).
run_interactive() {
  if [ -t 0 ]; then
    "$@"
  elif ( : </dev/tty ) 2>/dev/null; then
    "$@" </dev/tty
  else
    echo "This step needs a terminal. Run the installer again from a terminal window." >&2
    return 1
  fi
}

is_complete() {
  [ -f "$DEST/manage.py" ] && [ -x "$BIN/plane-mcp" ] && "$PY" -c "import mcp, keyring" 2>/dev/null
}

configured() {
  "$PY" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("projects") else 1)' "$CONFIG" 2>/dev/null
}

# An older or hand-made install: keep it, never delete it. It is put back if this install fails.
if [ -e "$DEST" ] && [ ! -f "$DEST/.plane-mcp-managed" ] && [ ! -d "$DEST/.git" ]; then
  BACKUP="$DEST.backup-$(date +%Y%m%d%H%M%S)"
  echo "Found an existing Plane MCP at $DEST that this installer did not create." >&2
  echo "Moving it to $BACKUP (nothing is deleted)." >&2
  mv "$DEST" "$BACKUP"
fi
mkdir -p "$(dirname "$DEST")" "$BIN" "$DEST"
touch "$DEST/.plane-mcp-managed"  # from here on this folder is ours, even if a step fails

if is_complete; then
  echo "Plane MCP is already installed at $DEST"
  run_interactive "$PY" "$DEST/manage.py" update
else
  echo "Installing or repairing Plane MCP at $DEST"
  if [ ! -f "$DEST/manage.py" ]; then
    echo "• downloading Plane MCP"
    VERSION=$(curl -fsSL "https://raw.githubusercontent.com/$OWNER/main/VERSION")
    printf '%s\n' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || {
      echo "GitHub returned an invalid Plane MCP version." >&2
      exit 1
    }
    curl -fsSL "https://github.com/$OWNER/archive/refs/tags/v$VERSION.tar.gz" -o "$TEMP_DIR/source.tar.gz"
    tar -xzf "$TEMP_DIR/source.tar.gz" --strip-components=1 -C "$DEST"
    if [ "$(cat "$DEST/VERSION")" != "$VERSION" ]; then
      rm -f "$DEST/manage.py"  # force a clean download next run
      echo "Downloaded source version does not match the GitHub version manifest." >&2
      exit 1
    fi
  fi
  if [ ! -x "$UV" ]; then
    echo "• installing uv"
    curl -fsSL https://astral.sh/uv/install.sh -o "$TEMP_DIR/uv-install.sh"
    UV_INSTALL_DIR="$DEST/.tools" UV_NO_MODIFY_PATH=1 sh "$TEMP_DIR/uv-install.sh"
  fi
  if ! "$PY" -c "pass" 2>/dev/null; then  # missing or broken virtual environment
    echo "• creating Python 3.12 environment"
    UV_PYTHON_INSTALL_DIR="$DEST/.python" "$UV" python install 3.12
    rm -rf "$DEST/.venv"  # only ever our own folder; uv refuses to reuse a half-made one
    UV_PYTHON_INSTALL_DIR="$DEST/.python" "$UV" venv --python 3.12 "$DEST/.venv"
  fi
  if ! "$PY" -c "import mcp, keyring" 2>/dev/null; then
    echo "• installing dependencies"
    "$UV" pip install --python "$PY" -r "$DEST/requirements.txt"
  fi
  cat > "$BIN/plane-mcp" <<'EOF'
#!/bin/sh
exec "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/manage.py" "$@"
EOF
  chmod 755 "$BIN/plane-mcp"
fi

if [ -n "$BACKUP" ]; then
  echo "Your previous install is kept at $BACKUP. Re-register the MCP server in your client if it still points at the old files." >&2
  BACKUP=""  # the install itself succeeded; a later setup problem must not roll it back
fi

if ! configured; then
  run_interactive "$BIN/plane-mcp" setup
fi
