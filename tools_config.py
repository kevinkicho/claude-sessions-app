"""Tool registry for AI coding assistants supported by AI Sessions.

Add a new entry to TOOLS to support a new assistant. The rest of the codebase
reads from this registry, so no other files need changes for a new tool.
"""

import re

# ---------------------------------------------------------------------------
# Slug helpers (replicate the per-tool project-slug rules)
# ---------------------------------------------------------------------------


def _claude_slug_windows(folder: str) -> str:
    return re.sub(r"[:\\_]", "-", folder.rstrip("\\/"))


def _claude_slug_wsl(folder_wsl: str) -> str:
    return re.sub(r"[/_]", "-", folder_wsl.rstrip("/"))


def _default_slug_windows(folder: str) -> str:
    return re.sub(r"[:\\_]", "-", folder.rstrip("\\/"))


def _default_slug_wsl(folder_wsl: str) -> str:
    return re.sub(r"[/_]", "-", folder_wsl.rstrip("/"))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOLS = {
    "claude": {
        "display_name": "Claude Code",
        "cli_cmd": "claude",
        "function_name": "klaud",
        "function_body": (
            "klaud() {\n"
            "    local slug\n"
            '    slug=$(pwd | sed \'s|/|-|g; s|_|-|g\')\n'
            '    local projdir="$HOME/.claude/projects/$slug"\n'
            '    if compgen -G "$projdir/*.jsonl" > /dev/null; then\n'
            '        command claude --resume --dangerously-skip-permissions "$@"\n'
            "    else\n"
            '        command claude --dangerously-skip-permissions "$@"\n'
            "    fi\n"
            "}"
        ),
        # Symlink memory: $HOME/.claude/projects/<slug>
        "memory_dir_template": "$HOME/.claude/projects/{slug}",
        "slug_windows": _claude_slug_windows,
        "slug_wsl": _claude_slug_wsl,
        "check_install": "which claude",
        "check_function": "type klaud",
    },
    "opencode": {
        "display_name": "OpenCode",
        "cli_cmd": "opencode",
        "function_name": "ocd",
        "function_body": (
            "ocd() {\n"
            '    command opencode --continue -m "${OCD_MODEL:-opencode-go/deepseek-v4-pro}" "$@"\n'
            "}"
        ),
        # OpenCode stores sessions in its own database, not per-project dirs.
        # Set to None to skip memory symlinking for this tool.
        "memory_dir_template": None,
        "slug_windows": _default_slug_windows,
        "slug_wsl": _default_slug_wsl,
        "check_install": "which opencode",
        "check_function": "type ocd",
    },
    "grok": {
        "display_name": "Grok Build",
        "cli_cmd": "grok",
        "function_name": "grok",
        "function_body": (
            "grok() {\n"
            '    command grok --continue "$@"\n'
            "}"
        ),
        # Grok Build (xAI) is designed with strong built-in persistent memory
        # and session continuation across runs in the same workspace.
        # We explicitly pass --continue (like OpenCode) to ensure it resumes
        # the previous conversation / utilizes its sessions memory rather than
        # starting fresh. No per-project memory dir symlinking (memory_dir_template: None).
        "memory_dir_template": None,
        "slug_windows": _default_slug_windows,
        "slug_wsl": _default_slug_wsl,
        "check_install": "which grok",
        "check_function": "type grok",
    },
}

DEFAULT_TOOL = "claude"


def get_tool(tool_key: str | None) -> dict:
    """Return the tool dict for *tool_key*, falling back to DEFAULT_TOOL."""
    key = tool_key if tool_key and tool_key in TOOLS else DEFAULT_TOOL
    return TOOLS[key]


def tool_keys() -> list[str]:
    """List of registered tool keys in definition order (for UI dropdowns)."""
    return list(TOOLS.keys())
