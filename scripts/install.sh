#!/usr/bin/env bash
# Install / update nhl-scoreboard on a Raspberry Pi as a systemd service.
#
#   curl -fsSL https://raw.githubusercontent.com/kas21/nhl-scoreboard/main/scripts/install.sh | sudo bash
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
warn()    { printf '\033[1;33m    ! %s\033[0m\n' "$*" >&2; }

# Piped through bash (`curl ... | bash`) there is no script file to re-run under sudo: $0 is
# just "bash", and `sudo bash bash` fails. Say so instead of failing three lines later.
PIPED=0
case "$0" in bash|sh|-bash|-sh|/dev/fd/*|/proc/self/fd/*) PIPED=1 ;; esac
if [ "$(id -u)" -ne 0 ]; then
    [ "$PIPED" -eq 0 ] || die "run this as root:  curl -fsSL .../install.sh | sudo bash"
    exec sudo -E bash "$0" "$@"
fi
[ "$(uname -m)" = aarch64 ] || die "64-bit Raspberry Pi OS is required (this is $(uname -m)): pydantic, pillow and uvicorn ship no 32-bit wheels"

section "System packages"
apt-get update -qq
# cmake: the rgbmatrix source build (any Python without a prebuilt wheel, i.e. Bookworm's 3.11) needs it
apt-get install -y -qq git python3 python3-venv python3-dev build-essential cmake libjpeg-dev zlib1g-dev libfreetype6-dev avahi-daemon >/dev/null
ok "installed"

section "Source -> $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch -q origin "$BRANCH" && git -C "$APP_DIR" checkout -q "$BRANCH" && git -C "$APP_DIR" pull -q --ff-only
    ok "updated ($(git -C "$APP_DIR" rev-parse --short HEAD))"
elif [ "$PIPED" -eq 0 ] && [ -f "$(dirname "$0")/../pyproject.toml" ] && [ "$(cd "$(dirname "$0")/.." && pwd)" != "$APP_DIR" ] && [ -z "${SCOREBOARD_CLONE:-}" ]; then
    # running from a checkout that isn't /opt/scoreboard: use it in place
    APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    ok "using checkout at $APP_DIR"
    # This is a development layout, so say what it costs. The service runs as root and
    # updating runs `pip install -e .`, so the updater refuses a checkout root does not
    # own -- which is this one, unless it was cloned as root.
    OWNER_UID="$(stat -c %u "$APP_DIR")"
    if [ "$OWNER_UID" -ne 0 ]; then
        warn "this checkout is owned by uid $OWNER_UID and the service runs as root, so the"
        warn "dashboard's one-click Update will refuse it (updating runs 'pip install -e .',"
        warn "which would let that user run code as root). Fine for development."
        warn "  permanent install instead:  SCOREBOARD_CLONE=1 $0"
        warn "  or allow it:                web.allow_unowned_checkout (System > Advanced)"
    fi
    if [ -n "$(find "$APP_DIR" -maxdepth 0 -perm /022)" ]; then
        warn "this checkout is group- or world-writable; the updater refuses that outright"
        warn "and allow_unowned_checkout does not waive it. Fix: chmod g-w,o-w $APP_DIR"
    fi
else
    git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "cloned"
fi

# service runs as root over a user-owned checkout; added once, not on every re-run
git config --system --get-all safe.directory 2>/dev/null | grep -qx "$APP_DIR" || git config --system --add safe.directory "$APP_DIR" 2>/dev/null || true

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

# ProtectHome hides /home from the service, which is right for the /opt install but would
# hide the checkout itself when someone runs from ~/scoreboard. Only ask for it when it
# costs nothing.
case "$APP_DIR" in
    /home/*|/root/*) PROTECT_HOME=no ;;
    *)               PROTECT_HOME=yes ;;
esac
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=LED scoreboard
After=network-online.target
Wants=network-online.target
# Restart=always with a 3 s gap never trips systemd's default start limit (5 in 10 s), so a
# release that dies at import re-initialised the panel every 3 s forever. Five failures in
# two minutes now stop the loop; the dashboard's roll-back (or journalctl) is the way out.
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
ExecStart=$APP_DIR/.venv/bin/scoreboard --config $CONFIG_DIR/config.json --output hardware
Restart=always
RestartSec=3
TimeoutStopSec=10
Environment=PYTHONUNBUFFERED=1
Environment=SCOREBOARD_CACHE_DIR=/var/cache/scoreboard
Environment=SCOREBOARD_DATA_DIR=/var/lib/scoreboard
CacheDirectory=scoreboard
StateDirectory=scoreboard
WorkingDirectory=$APP_DIR

# This has to be root — the matrix driver drives GPIO directly — so the point of these is
# to bound what a bad update or a compromised dependency can reach, not to pretend it is
# unprivileged. Anything the app legitimately writes stays writable: $CONFIG_DIR (config
# and its backups), /var/cache/scoreboard (logo cache), /var/lib/scoreboard (pictures you
# uploaded, which nothing can re-download), and $APP_DIR (the OTA checkout and its venv).
# /usr and /boot go read-only, /home and /root disappear — which is why neither directory
# may fall back to the home-relative default.
# RestrictRealtime is deliberately absent: the matrix driver's refresh thread asks for
# SCHED_FIFO, and refusing it (silently, a warning in the journal) means flicker.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full
ProtectHome=$PROTECT_HOME
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
ReadWritePaths=$CONFIG_DIR $APP_DIR

[Install]
WantedBy=multi-user.target
UNIT
# Pi OS keeps the journal in RAM: the reason a service crash-looped is gone after a reboot.
mkdir -p /var/log/journal && systemctl restart systemd-journald 2>/dev/null || true
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
