#!/bin/bash
# One-command helper to set up the recommended grok() wrapper
# that ensures --continue (session memory / previous context) for Grok Build.
#
# Run this from inside WSL (e.g. after launching a session or in any bash):
#   bash /mnt/c/Users/kevin/Desktop/claude-sessions-app/setup-grok-helper.sh

echo "Setting up recommended grok() wrapper for AI Sessions (with --continue)..."

# Remove old versions (gkd or plain grok) to avoid conflicts
sed -i '/^# Added by sessions app for Grok Build support/,/^}$/d' ~/.bashrc 2>/dev/null || true

cat >> ~/.bashrc << 'BASHFUNC'

# Added by AI Sessions (claude-sessions-app) for Grok Build support.
# Uses --continue so auto-start resumes previous session + utilizes Grok's
# built-in persistent memory / context for the workspace (like --continue for OpenCode).
grok() {
    command grok --continue "$@"
}
BASHFUNC

echo "Reloading ~/.bashrc..."
source ~/.bashrc

echo ""
echo "Verification:"
if type grok >/dev/null 2>&1; then
    echo "  ✓ grok() wrapper is now defined (with --continue)"
else
    echo "  ✗ Something went wrong. Check ~/.bashrc manually."
fi

echo ""
echo "You can test with: grok --help"
echo "Next time you Launch a grok session with Auto-start, it will resume previous context."
