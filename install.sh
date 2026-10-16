#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOTKEY="${HOTKEY:-<Super><Alt>a}"

echo "==> venv + PySide6"
if [ ! -x "$DIR/.venv/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then uv venv "$DIR/.venv"
  else python3 -m venv "$DIR/.venv"; fi
fi
if [ ! -x "$DIR/.venv/bin/PySide6" ] && ! "$DIR/.venv/bin/python" -c "import PySide6" 2>/dev/null; then
  if [ -x "$DIR/.venv/bin/pip" ]; then
    "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
  else
    uv pip install --python "$DIR/.venv/bin/python" -q -r "$DIR/requirements.txt"
  fi
fi
chmod +x "$DIR/shot"

echo "==> desktop file + icon"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$APPS" "$ICONS"
cat > "$ICONS/screenshot-tool.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#263238"/><rect x="10" y="22" width="44" height="28" rx="5" fill="#00bcd4"/><rect x="24" y="14" width="16" height="10" rx="3" fill="#00bcd4"/><circle cx="32" cy="36" r="9" fill="#263238"/><circle cx="32" cy="36" r="6" fill="#eceff1"/></svg>
EOF
cat > "$APPS/screenshot-tool.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Screenshot Tool
Comment=Flameshot-style region cropper (transparent frame -> annotate -> capture)
Exec=$DIR/shot --toggle
Icon=screenshot-tool
Terminal=false
Categories=Utility;Graphics;
EOF

echo "==> portal permission grant (app-id screenshot-tool)"
CUR=$(gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
  --object-path /org/freedesktop/impl/portal/PermissionStore \
  --method org.freedesktop.impl.portal.PermissionStore.Lookup screenshot screenshot 2>/dev/null || true)
if [[ "$CUR" == *"'screenshot-tool'"* ]]; then
  echo "    grant already present"
else
  gdbus call --session --dest org.freedesktop.impl.portal.PermissionStore \
    --object-path /org/freedesktop/impl/portal/PermissionStore \
    --method org.freedesktop.impl.portal.PermissionStore.SetPermission \
    screenshot true screenshot screenshot-tool "['yes']" >/dev/null
  echo "    granted"
fi

echo "==> GNOME hotkey: $HOTKEY"
python3 - "$DIR/shot" "$HOTKEY" <<'EOF'
import ast, subprocess, sys
launcher, hotkey = sys.argv[1], sys.argv[2]
base = "org.gnome.settings-daemon.plugins.media-keys"
path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/screenshot-tool0/"
cur = subprocess.run(["gsettings", "get", base, "custom-keybindings"],
                     capture_output=True, text=True).stdout.strip()
lst = ast.literal_eval(cur) if cur and cur != "@as []" else []
if path not in lst:
    lst.append(path)
    subprocess.run(["gsettings", "set", base, "custom-keybindings", str(lst)], check=True)
kb = base + ".custom-keybinding:" + path
subprocess.run(["gsettings", "set", kb, "name", "Screenshot Tool"], check=True)
subprocess.run(["gsettings", "set", kb, "command", launcher + " --toggle"], check=True)
subprocess.run(["gsettings", "set", kb, "binding", hotkey], check=True)
print("    bound", hotkey, "->", launcher)
EOF

echo
echo "Done. Try:  $DIR/shot        (toggle the cropper)"
echo "Hotkey:     $HOTKEY           (may need a re-login or a few seconds to register)"
echo "Settings:   ~/.config/screenshot-tool/settings.json"
