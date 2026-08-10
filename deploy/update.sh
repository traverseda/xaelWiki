#!/usr/bin/env bash
# Auto-update xaelwiki: pull the pinned branch, reinstall deps, restart.
# Invoked by the systemd timer; arguments are baked in by install.sh.
set -euo pipefail

INSTALL_DIR="${1:?error: install dir required}"
BRANCH="${2:-main}"
SCOPE="${3:-user}"

export PATH="/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

if [[ "$SCOPE" == "user" ]]; then
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
    restart=("systemctl" "--user" "restart" "xaelwiki")
else
    restart=("systemctl" "restart" "xaelwiki")
fi

old_head="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo none)"

echo "==> fetching $BRANCH from origin"
git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"

if [[ "$SCOPE" == "system" ]]; then
    owner="$(stat -c '%U:%G' "$INSTALL_DIR")"
    chown -R "$owner" "$INSTALL_DIR"
fi

new_head="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
if [[ "$old_head" == "$new_head" ]]; then
    echo "==> already up to date"
    exit 0
fi

echo "==> installing python dependencies"
if [[ "$SCOPE" == "system" ]]; then
    service_user="$(stat -c '%U' "$INSTALL_DIR")"
    runuser -u "$service_user" -- bash -c \
        "export PATH='$PATH'; uv sync --project '$INSTALL_DIR'"
else
    uv sync --project "$INSTALL_DIR"
fi

echo "==> restarting xaelwiki"
"${restart[@]}"
