#!/usr/bin/env bash
# Auto-update xaelwiki: pull the pinned branch, reinstall deps, restart.
# Invoked by the systemd timer; arguments are baked in by install.sh.
#
# This always reinstalls deps and restarts the service on every run, even
# when the checkout is already current. That way a failed run can never
# strand the server on stale code: if a previous run reset the checkout but
# died before the restart (e.g. a transient `uv sync` failure), the next run
# heals it. A naive "already up to date -> exit" short-circuit deadlocks into
# that state permanently.
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

echo "==> fetching $BRANCH from origin"
git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"

if [[ "$SCOPE" == "system" ]]; then
    owner="$(stat -c '%U:%G' "$INSTALL_DIR")"
    chown -R "$owner" "$INSTALL_DIR"
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
