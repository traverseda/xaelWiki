#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${XAEL_REPO_URL:-https://github.com/traverseda/xaelWiki.git}"
BRANCH="${XAEL_BRANCH:-main}"
INSTALL_DIR="${XAEL_INSTALL_DIR:-$HOME/.local/share/xaelwiki}"
NOTES_DIR="${XAEL_NOTES_DIR:-$INSTALL_DIR/notes}"

if ! command -v git >/dev/null 2>&1; then
    echo "error: git is required to install xaelwiki" >&2
    exit 1
fi

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    echo "==> cloning xaelwiki into $INSTALL_DIR"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
else
    echo "==> xaelwiki already present, updating from $BRANCH"
    git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "==> installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

echo "==> installing python dependencies"
uv sync --project "$INSTALL_DIR"

mkdir -p "$NOTES_DIR"
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
    echo "==> initializing notes git repo"
    git init -b main "$INSTALL_DIR"
    git -C "$INSTALL_DIR" config user.name "xaelwiki"
    git -C "$INSTALL_DIR" config user.email "xaelwiki@local"
fi

ENV_FILE="${XAEL_ENV_FILE:-$HOME/.config/xaelwiki/env}"
if [[ ! -s "$ENV_FILE" ]]; then
    echo "==> generating auth token in $ENV_FILE"
    mkdir -p "$(dirname "$ENV_FILE")"
    umask 077
    printf 'XAEL_AUTH_TOKEN=%s\n' "$(openssl rand -hex 32)" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
fi

prepare_user_bus() {
    local runtime_dir="/run/user/$(id -u)"
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$runtime_dir}"
    export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
    if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
        echo "==> user systemd manager not running; enabling linger"
        if command -v loginctl >/dev/null 2>&1 && loginctl enable-linger "$(id -un)" 2>/dev/null; then
            for _ in $(seq 1 20); do
                [[ -S "$XDG_RUNTIME_DIR/bus" ]] && break
                sleep 0.5
            done
        fi
        if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
            echo "error: cannot reach the user systemd bus ($XDG_RUNTIME_DIR/bus)" >&2
            echo "this container has no running user session. as root, run:" >&2
            echo "  loginctl enable-linger $(id -un)" >&2
            echo "then rerun this installer as $(id -un)." >&2
            exit 1
        fi
    fi
}

echo "==> installing systemd user service"
prepare_user_bus
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$INSTALL_DIR/deploy/xaelwiki.user.service" > "$UNIT_DIR/xaelwiki.service"
systemctl --user daemon-reload
systemctl --user enable --now xaelwiki

echo
echo "xaelwiki installed and running as a user service."
echo
echo "  systemctl --user status xaelwiki"
echo "  journalctl --user -u xaelwiki -f"
echo
echo "token file: $ENV_FILE (mode 600, never commit or paste it)"
echo "notes live in $NOTES_DIR"
echo "install again to update: curl -fsSL https://raw.githubusercontent.com/traverseda/xaelWiki/main/install.sh | bash"
