#!/bin/sh
set -eu

REPO="https://github.com/ydanh-dev/plane_mcp.git"
DEST="$HOME/.local/share/plane-mcp"
BIN="$HOME/.local/bin"

if [ -e "$DEST/.git" ]; then
  echo "Plane MCP is already installed at $DEST"
  "$DEST/.venv/bin/python" "$DEST/manage.py" update
  exit 0
fi
if [ -e "$DEST" ]; then
  echo "Refusing to overwrite existing non-managed path: $DEST" >&2
  exit 1
fi

PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    PYTHON=$(command -v "$candidate")
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "Python 3.10 or newer is required." >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "Git is required to install and update versioned releases." >&2
  exit 1
fi

mkdir -p "$(dirname "$DEST")" "$BIN"
git clone --depth 1 "$REPO" "$DEST"
"$PYTHON" -m venv "$DEST/.venv"
"$DEST/.venv/bin/python" -m pip install --upgrade pip
"$DEST/.venv/bin/python" -m pip install -r "$DEST/requirements.txt"
cat > "$BIN/plane-mcp" <<'EOF'
#!/bin/sh
exec "$HOME/.local/share/plane-mcp/.venv/bin/python" "$HOME/.local/share/plane-mcp/manage.py" "$@"
EOF
chmod 755 "$BIN/plane-mcp"
"$BIN/plane-mcp" setup
