#!/usr/bin/env bash
# Install / update nhl-scoreboard on a Raspberry Pi as a systemd service.
#
#   curl -fsSL https://raw.githubusercontent.com/<owner>/nhl-scoreboard/main/scripts/install.sh | bash
#   ./scripts/install.sh            # from a checkout (re-runnable; updates in place)
#
# Steps: apt deps -> clone/update to /opt/scoreboard -> venv -> pip install ->
# rgbmatrix (prebuilt wheel if it fits this Python, else build from source) ->
# systemd unit (runs as root: the matrix driver needs GPIO) -> start.
set -euo pipefail

REPO_URL="${SCOREBOARD_REPO:-https://github.com/kas21/nhl-scoreboard.git}"   # public; the app updates itself from here
BRANCH="${SCOREBOARD_BRANCH:-main}"
APP_DIR="${SCOREBOARD_DIR:-/opt/scoreboard}"
CONFIG_DIR="${SCOREBOARD_CONFIG_DIR:-/etc/scoreboard}"
SERVICE=scoreboard
MATRIX_SRC=/opt/rpi-rgb-led-matrix

section() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()      { printf '\033[1;32m    ✓ %s\033[0m\n' "$*"; }
die()     { printf '\033[1;31m    ✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || exec sudo -E bash "$0" "$@"

section "System packages"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-dev build-essential libjpeg-dev zlib1g-dev libfreetype6-dev avahi-daemon >/dev/null
ok "installed"

section "Source -> $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" checkout -q "$BRANCH" && git -C "$APP_DIR" pull -q --ff-only
    ok "updated ($(git -C "$APP_DIR" rev-parse --short HEAD))"
elif [ -f "$(dirname "$0")/../pyproject.toml" ] && [ "$(cd "$(dirname "$0")/.." && pwd)" != "$APP_DIR" ] && [ -z "${SCOREBOARD_CLONE:-}" ]; then
    # running from a checkout that isn't /opt/scoreboard: use it in place
    APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    ok "using checkout at $APP_DIR"
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "cloned"
fi

section "Python environment"
PY=python3
[ -x "$APP_DIR/.venv/bin/python" ] || $PY -m venv "$APP_DIR/.venv"
VPY="$APP_DIR/.venv/bin/python"
"$VPY" -m pip install -q --upgrade pip wheel
"$VPY" -m pip install -q -e "$APP_DIR"
ok "$("$VPY" --version) at $APP_DIR/.venv"

section "rgbmatrix driver"
if "$VPY" -c 'import rgbmatrix' 2>/dev/null; then
    ok "already installed"
else
    PYTAG="cp$("$VPY" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')"
    WHEEL="https://github.com/falkyre/nhl-led-scoreboard-img/releases/download/latest-trixie/rgbmatrix-0.0.1-${PYTAG}-${PYTAG}-linux_$(uname -m).whl"
    if ! "$VPY" -m pip install -q "$WHEEL" 2>/dev/null || ! "$VPY" -c 'import rgbmatrix' 2>/dev/null; then
        echo "    no prebuilt wheel for $PYTAG/$(uname -m); building from source (~3 min on a Pi 4)"
        [ -d "$MATRIX_SRC" ] || git clone -q https://github.com/hzeller/rpi-rgb-led-matrix.git "$MATRIX_SRC"
        "$VPY" -m pip install -q "$MATRIX_SRC"          # upstream ships a pyproject at the repo root
    fi
    "$VPY" -c 'import rgbmatrix' || die "rgbmatrix failed to install"
    ok "installed"
fi

section "Service"
mkdir -p "$CONFIG_DIR"
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=LED scoreboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$APP_DIR/.venv/bin/scoreboard --config $CONFIG_DIR/config.json --output hardware
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=$APP_DIR

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q $SERVICE
systemctl restart $SERVICE
sleep 2
if systemctl is-active -q $SERVICE; then
    ok "running — open http://$(hostname).local:8080"
else
    journalctl -u $SERVICE -n 30 --no-pager
    die "service failed to start"
fi
