"""Launches a named WSL tmux session in a configured folder, and optionally
ensures a symlink so WSL-side tool (Claude Code / OpenCode / Grok Build) shares state
with the Windows side.

Usage: session_launch.py <sesN>

Reads sessions.json (next to this script) to find the folder, auto-start
flag, symlink flag, and tool. Then runs:

    wsl -d <distro> -- tmux new-session -A -s <sesN> -c <wsl-folder> [cmd]

`tmux new-session -A` attaches if the session exists; otherwise creates it.
The optional `[cmd]` only runs on creation, so tool sessions survive reattach.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from tools_config import get_tool, DEFAULT_TOOL

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "sessions.json"

# Customize this if your WSL distro isn't called "Ubuntu".
WSL_DISTRO = os.environ.get("CLAUDE_SESSIONS_DISTRO", "Ubuntu")


def windows_to_wsl(win_path: str) -> str:
    """Convert a Windows absolute path to /mnt/<drive>/... form."""
    p = Path(win_path).resolve()
    drive_letter = p.drive[0].lower() if p.drive else ""
    tail = str(p)[len(p.drive):].replace("\\", "/")
    return f"/mnt/{drive_letter}{tail}"


def ensure_memory_symlink(folder: str, tool_key: str | None = None) -> Tuple[bool, str]:
    """Create a symlink so the WSL-side tool shares memory with the Windows side.

    The symlink path is driven by the tool's ``memory_dir_template``.  When the
    template is ``None`` the tool doesn't have a per-project memory directory
    (e.g. OpenCode), and this function is a no-op."""
    folder = folder.strip().rstrip("\\/")
    if not folder:
        return False, "folder is empty"

    tool = get_tool(tool_key)
    template = tool.get("memory_dir_template")
    if not template:
        return True, "(skipped — tool has no per-project memory dir)"

    win_slug = tool["slug_windows"](folder)
    wsl_folder = windows_to_wsl(folder)
    wsl_slug = tool["slug_wsl"](wsl_folder)

    # Windows user's home translated to the WSL /mnt/c/... form.
    win_home_wsl = windows_to_wsl(str(Path.home()))
    src = f"{win_home_wsl}/{template.removeprefix('$HOME/')}"
    # Expand {slug} placeholder.
    src = src.replace("{slug}", win_slug)
    # Parent dir path for `mkdir -p` and the dst symlink.
    dst_parent = template.removeprefix("$HOME/").replace("{slug}", wsl_slug)
    dst_rel = dst_parent  # full relative path under $HOME for the symlink target
    # The parent of the symlink target (where the symlink should live).
    dst_parent_dir = "/".join(dst_parent.split("/")[:-1])

    bash = (
        f'mkdir -p "$HOME/{dst_parent_dir}" && '
        f'DST="$HOME/{dst_rel}" && '
        'if [ -e "$DST" ] && [ ! -L "$DST" ]; then '
        '  echo "ERROR: $DST exists and is not a symlink; skipped" >&2; exit 1; '
        'else '
        f'  ln -sfn "{src}" "$DST" && echo "linked $DST -> {src}"; '
        'fi'
    )
    try:
        result = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "--", "bash", "-c", bash],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        return False, f"wsl call failed: {exc}"

    if result.returncode == 0:
        return True, result.stdout.strip() or "linked"
    return False, (result.stderr or result.stdout).strip() or f"exit {result.returncode}"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[session_launch] failed to read {CONFIG_PATH}: {exc}", file=sys.stderr)
        return {}


def build_tmux_args(session_name: str, info: dict) -> list[str]:
    folder = (info.get("folder") or "").strip()
    auto = bool(info.get("auto_start", info.get("auto_claude", False)))
    tool_key = info.get("tool") or DEFAULT_TOOL
    tool = get_tool(tool_key)

    args = ["wsl", "-d", WSL_DISTRO, "--", "tmux", "new-session", "-A", "-s", session_name]

    if folder:
        if not Path(folder).exists():
            print(f"[session_launch] warn: folder {folder!r} does not exist", file=sys.stderr)
        args += ["-c", windows_to_wsl(folder)]

    if auto:
        fn = tool["function_name"]
        args.append(f"bash -ic '{fn}; exec bash'")

    return args


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: session_launch.py <sesN>", file=sys.stderr)
        return 2

    name = sys.argv[1]

    # Very visible diagnostics so the console window shows exactly what is happening
    print("=" * 70, file=sys.stderr)
    print(f"[session_launch] START: name={name}", file=sys.stderr)
    print(f"[session_launch] script file: {__file__}", file=sys.stderr)
    print(f"[session_launch] python: {sys.executable}", file=sys.stderr)

    cfg = load_config()
    info = cfg.get(name)
    if info is None:
        print(f"[session_launch] no config for {name!r}. Run the Sessions GUI and pick a folder.", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        return 1

    tool_key = info.get("tool") or DEFAULT_TOOL
    tool = get_tool(tool_key)
    print(f"[session_launch] from sessions.json: tool={tool_key}  function={tool.get('function_name')}", file=sys.stderr)
    print(f"[session_launch] auto-start={info.get('auto_start', info.get('auto_claude', False))}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)

    if info.get("symlink_memory") and info.get("folder"):
        tool_key2 = info.get("tool") or DEFAULT_TOOL
        ok, msg = ensure_memory_symlink(info["folder"], tool_key2)
        prefix = "[session_launch] symlink:" if ok else "[session_launch] symlink FAILED:"
        print(f"{prefix} {msg}", file=sys.stderr)

    args = build_tmux_args(name, info)
    print(f"[session_launch] running: {' '.join(args)}", file=sys.stderr)
    sys.stderr.flush()
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())
