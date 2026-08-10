#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${XAEL_REPO_URL:-https://github.com/traverseda/xaelWiki.git}"
BRANCH="${XAEL_BRANCH:-main}"

if [[ "$(id -u)" -eq 0 ]]; then
    SCOPE="system"
    SERVICE_USER="${XAEL_SERVICE_USER:-xaelwiki}"
    INSTALL_DIR="${XAEL_INSTALL_DIR:-/opt/xaelwiki}"
    NOTES_DIR="${XAEL_NOTES_DIR:-$INSTALL_DIR/notes}"
    UNIT_DIR="/etc/systemd/system"
    ENV_FILE="${XAEL_ENV_FILE:-/etc/xaelwiki/env}"
    UNIT_TEMPLATE="$INSTALL_DIR/deploy/xaelwiki.service"
else
    SCOPE="user"
    SERVICE_USER="$(id -un)"
    INSTALL_DIR="${XAEL_INSTALL_DIR:-$HOME/.local/share/xaelwiki}"
    NOTES_DIR="${XAEL_NOTES_DIR:-$INSTALL_DIR/notes}"
    UNIT_DIR="$HOME/.config/systemd/user"
    ENV_FILE="${XAEL_ENV_FILE:-$HOME/.config/xaelwiki/env}"
    UNIT_TEMPLATE="$INSTALL_DIR/deploy/xaelwiki.user.service"
fi

if ! command -v git >/dev/null 2>&1; then
    echo "error: git is required to install xaelwiki" >&2
    exit 1
fi

git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    echo "==> cloning xaelwiki into $INSTALL_DIR"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
else
    echo "==> xaelwiki already present, updating from $BRANCH"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
fi

if [[ "$SCOPE" == "system" ]]; then
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        useradd -m -r -s /bin/bash "$SERVICE_USER"
    fi
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "==> installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

echo "==> installing python dependencies"
uv sync --project "$INSTALL_DIR"

if [[ "$SCOPE" == "system" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
fi

mkdir -p "$NOTES_DIR"
if [[ "$SCOPE" == "system" ]]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$NOTES_DIR"
fi
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    echo "==> initializing notes git repo"
    git init -b main "$INSTALL_DIR"
    git -C "$INSTALL_DIR" config user.name "xaelwiki"
    git -C "$INSTALL_DIR" config user.email "xaelwiki@local"
fi

ENV_FILE="${ENV_FILE:-$HOME/.config/xaelwiki/env}"
if [[ ! -s "$ENV_FILE" ]]; then
    echo "==> generating auth token in $ENV_FILE"
    mkdir -p "$(dirname "$ENV_FILE")"
    umask 077
    printf 'XAEL_AUTH_TOKEN=%s\n' "$(openssl rand -hex 32)" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

if [[ "$SCOPE" == "user" ]]; then
    prepare_user_bus() {
        local runtime_dir="/run/user/$(id -u)"
        export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$runtime_dir}"
        export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
        if [[ -S "$XDG_RUNTIME_DIR/bus" ]]; then
            return
        fi
        local lingered="no"
        if command -v loginctl >/dev/null 2>&1; then
            lingered="$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true)"
        fi
        if [[ "$lingered" != "yes" ]]; then
            echo "==> user systemd manager not running; enabling linger"
            if command -v loginctl >/dev/null 2>&1 && loginctl enable-linger "$(id -un)" 2>/dev/null; then
                for _ in $(seq 1 20); do
                    [[ -S "$XDG_RUNTIME_DIR/bus" ]] && break
                    sleep 0.5
                done
            fi
        fi
        if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
            echo "==> linger set but the user manager is not up; trying to start it"
            for _ in $(seq 1 20); do
                if command -v systemctl >/dev/null 2>&1 && systemctl start "user@$(id -u).service" 2>/dev/null; then
                    [[ -S "$XDG_RUNTIME_DIR/bus" ]] && break
                fi
                sleep 0.5
            done
        fi
        if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
            echo "error: cannot reach the user systemd bus ($XDG_RUNTIME_DIR/bus)" >&2
            echo "this container has no running user session. as root, run:" >&2
            echo "  systemctl start user@$(id -u).service" >&2
            echo "  journalctl -u user@$(id -u).service --no-pager | tail -20  # why it failed" >&2
            echo "then rerun this installer as $(id -un)." >&2
            exit 1
        fi
    }
    echo "==> installing systemd user service"
    prepare_user_bus
    mkdir -p "$UNIT_DIR"
    sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$UNIT_TEMPLATE" > "$UNIT_DIR/xaelwiki.service"
    systemctl --user daemon-reload
    systemctl --user enable --now xaelwiki
else
    echo "==> installing systemd system service"
    mkdir -p "$UNIT_DIR"
    sed \
        -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
        -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
        "$UNIT_TEMPLATE" > "$UNIT_DIR/xaelwiki.service"
    systemctl daemon-reload
    systemctl enable --now xaelwiki
fi

echo
echo "xaelwiki installed and running as a systemd ${SCOPE} service."
echo
echo "  systemctl --${SCOPE} status xaelwiki"
echo "  journalctl --${SCOPE} -u xaelwiki -f"
echo
echo "token file: $ENV_FILE (mode 600, never commit or paste it)"
echo "notes live in $NOTES_DIR"
echo "install again to update: curl -fsSL https://raw.githubusercontent.com/traverseda/xaelWiki/main/install.sh | bash"
