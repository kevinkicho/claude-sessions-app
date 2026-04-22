#!/data/data/com.termux/files/usr/bin/bash
# All-in-one SSH key rotation helper for Termux.
#
# Usage:
#   rotate-key               swap in a new key (local-file or token mode)
#   rotate-key <token>       force token mode: fetch over Tailnet
#   rotate-key install       first-time bootstrap on this device
#
# The script auto-updates itself whenever a newer copy lands in /sdcard/Download,
# so PC-side rotations ship the latest logic without any manual reinstall.

set -e

SELF="$HOME/rotate-key.sh"
UPDATE_SRC="/sdcard/Download/rotate-key.sh"

# --- first-time install ---
if [ "${1:-}" = "install" ]; then
    mkdir -p "$HOME"
    cp "$0" "$SELF"
    chmod +x "$SELF"
    if ! grep -q '^alias rotate-key=' "$HOME/.bashrc" 2>/dev/null; then
        echo 'alias rotate-key="bash ~/rotate-key.sh"' >> "$HOME/.bashrc"
    fi

    # Enable Termux's RUN_COMMAND intent so the PC GUI can trigger rotations
    # headlessly (no need to open Termux on the phone). Idempotent.
    mkdir -p "$HOME/.termux"
    TP="$HOME/.termux/termux.properties"
    if ! grep -q '^allow-external-apps' "$TP" 2>/dev/null; then
        echo "allow-external-apps = true" >> "$TP"
        echo "Enabled allow-external-apps in $TP"
    fi
    if command -v termux-reload-settings >/dev/null 2>&1; then
        termux-reload-settings 2>/dev/null || true
    fi

    # Remove the copy from Downloads so it doesn't linger as a stale duplicate.
    [ "$0" = "$UPDATE_SRC" ] && rm -f "$UPDATE_SRC"
    echo "rotate-key installed. Open a new Termux tab, or run:  source ~/.bashrc"
    echo "Then, to rotate after PC pushes a key, just type:  rotate-key"
    echo "The PC GUI can now also trigger rotations headlessly via RUN_COMMAND."
    exit 0
fi

# --- self-update if a newer rotate-key.sh is sitting in Downloads ---
if [ -f "$UPDATE_SRC" ] && [ "$UPDATE_SRC" -nt "$SELF" ]; then
    cp "$UPDATE_SRC" "$SELF"
    chmod +x "$SELF"
    rm -f "$UPDATE_SRC"
    exec bash "$SELF" "$@"
fi

# --- rotation logic ---
TOKEN="${1:-}"
PC_URL="${STT_SERVER:-http://100.125.88.85:8080}"
SSH_DIR="$HOME/.ssh"
KEY="$SSH_DIR/id_ed25519"
NEW="$SSH_DIR/id_ed25519.new"
BACKUP="$SSH_DIR/id_ed25519.old"

mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Prefer locally-pushed key (adb push from PC).
LOCAL=""
for candidate in "$HOME/storage/downloads/id_ed25519" "/sdcard/Download/id_ed25519"; do
    [ -f "$candidate" ] && { LOCAL="$candidate"; break; }
done

if [ -n "$LOCAL" ]; then
    echo "Found locally-pushed key at $LOCAL"
    cp "$LOCAL" "$NEW"
    chmod 600 "$NEW"
    rm -f "$LOCAL"
elif [ -n "$TOKEN" ]; then
    echo "Fetching new key from ${PC_URL}/keyfile ..."
    http_code=$(curl -sS -o "$NEW" -w "%{http_code}" \
        -H "X-Rotation-Token: $TOKEN" \
        "${PC_URL}/keyfile" 2>/dev/null || echo 000)
    if [ "$http_code" != "200" ]; then
        echo "ERROR: server returned HTTP $http_code" >&2
        [ -f "$NEW" ] && cat "$NEW" >&2
        rm -f "$NEW"
        exit 1
    fi
    [ -s "$NEW" ] || { echo "ERROR: empty key file" >&2; exit 1; }
    chmod 600 "$NEW"
else
    cat >&2 <<USAGE
usage:
  rotate-key              # when PC rotate-ssh.bat adb-pushed the new key
  rotate-key <token>      # remote device; fetches over Tailnet
  rotate-key install      # one-time bootstrap (installs this script)
USAGE
    exit 1
fi

[ -f "$KEY" ] && cp "$KEY" "$BACKUP"
mv "$NEW" "$KEY"

echo "Key installed; testing SSH..."
if ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 \
    kevin@100.125.88.85 'echo ROTATED_OK' 2>&1; then
    rm -f "$BACKUP"
    echo "SUCCESS: key rotated and working."
    # Best-effort toast/notification so the user sees confirmation on the
    # device even if the PC triggered this headlessly. termux-toast requires
    # the termux-api package; silently skip if unavailable.
    if command -v termux-toast >/dev/null 2>&1; then
        termux-toast -b "#16a34a" -c "#ffffff" -g top \
            "SSH key rotated · $(date +%H:%M) · connected to PC" 2>/dev/null || true
    fi
    if command -v termux-notification >/dev/null 2>&1; then
        termux-notification --title "SSH key rotated" \
            --content "connected to PC — $(date +'%b %d %H:%M')" \
            --priority default 2>/dev/null || true
    fi
else
    echo "ERROR: SSH test failed. Rolling back to previous key..." >&2
    [ -f "$BACKUP" ] && mv "$BACKUP" "$KEY"
    if command -v termux-toast >/dev/null 2>&1; then
        termux-toast -b "#dc2626" -c "#ffffff" \
            "SSH key rotation FAILED · rolled back" 2>/dev/null || true
    fi
    exit 1
fi
