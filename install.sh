#!/usr/bin/env bash
# Installs or upgrades anthias-weather on the Anthias PC. Run from this folder:
#   sudo ./install.sh
# Re-running it upgrades the program and keeps your existing config.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo ./install.sh" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/anthias-weather
CONF_DIR=/etc/anthias-weather
STATE_DIR=/var/lib/anthias-weather
SVC_USER=anthias-weather

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "anthias-weather needs Python 3.11 or newer (Debian 12 and 13 include it)." >&2
  exit 1
fi

if ! id "$SVC_USER" &>/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi

install -d -m 755 "$APP_DIR"
install -m 755 "$HERE/anthias_weather.py" "$APP_DIR/anthias_weather.py"
install -m 644 "$HERE/README.md" "$APP_DIR/README.md"

install -d -m 750 -o root -g "$SVC_USER" "$CONF_DIR"
if [[ ! -f "$CONF_DIR/config.toml" ]]; then
  install -m 640 -o root -g "$SVC_USER" "$HERE/config.example.toml" "$CONF_DIR/config.toml"
  NEW_CONFIG=1
else
  NEW_CONFIG=0
fi

install -d -m 755 -o "$SVC_USER" -g "$SVC_USER" "$STATE_DIR"

cat > /usr/local/bin/anthias-weather <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /opt/anthias-weather/anthias_weather.py --config /etc/anthias-weather/config.toml "$@"
EOF
chmod 755 /usr/local/bin/anthias-weather

install -m 644 "$HERE/anthias-weather.service" /etc/systemd/system/anthias-weather.service
systemctl daemon-reload

if [[ $NEW_CONFIG -eq 1 ]]; then
  cat <<EOF

Installed. Next steps:
  1. sudo nano $CONF_DIR/config.toml     (set your location, email and alert asset names)
  2. sudo anthias-weather list-assets    (shows the exact asset names)
  3. sudo anthias-weather check          (tests Anthias and the NWS connection)
  4. sudo systemctl enable --now anthias-weather
EOF
else
  systemctl try-restart anthias-weather || true
  echo "Upgraded. Your config at $CONF_DIR/config.toml was kept."
fi
