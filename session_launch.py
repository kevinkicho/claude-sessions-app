"""Launches a named WSL tmux session in a configured folder, and optionally
ensures a symlink so WSL-side Claude Code shares state with the Windows side.

Usage: session_launch.py <sesN>

Reads sessions.json (next to this script) to find the folder, auto-claude
flag, and symlink flag. Then runs:

    wsl -d <distro> -- tmux new-session -A -s <sesN> -c <wsl-folder> [cmd]

`tmux new-session -A` attaches if the session exists; otherwise creates it.
The optional `[cmd]` only runs on creation, so Claude sessions survive reattach.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

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


def claude_slug_windows(folder: str) -> str:
    """Replicate Claude Code's project-slug rule for a Windows path."""
    return re.sub(r'[:\\_]', '-', folder.rstrip("\\/"))


def claude_slug_wsl(folder_wsl: str) -> str:
    """Replicate Claude Code's project-slug rule for a WSL (/mnt/c/...) path."""
    return re.sub(r'[/_]', '-', folder_wsl.rstrip("/"))


def ensure_memory_symlink(folder: str) -> Tuple[bool, str]:
    """Create ~/.claude/projects/<wsl-slug> -> <win-home>/.claude/projects/<win-slug>
    inside WSL. Idempotent and refuses to clobber an existing real directory."""
    folder = folder.strip().rstrip("\\/")
    if not folder:
        return False, "folder is empty"

    win_slug = claude_slug_windows(folder)
    wsl_folder = windows_to_wsl(folder)
    wsl_slug = claude_slug_wsl(wsl_folder)

    # Windows user's home translated to the WSL /mnt/c/... form.
    win_home_wsl = windows_to_wsl(str(Path.home()))
    src = f"{win_home_wsl}/.claude/projects/{win_slug}"
    # Destination uses bash's $HOME so it works for any WSL user.
    dst_rel = f".claude/projects/{wsl_slug}"

    bash = (
        'mkdir -p "$HOME/.claude/projects" && '
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
    auto = bool(info.get("auto_claude"))

    args = ["wsl", "-d", WSL_DISTRO, "--", "tmux", "new-session", "-A", "-s", session_name]

    if folder:
        if not Path(folder).exists():
            print(f"[session_launch] warn: folder {folder!r} does not exist", file=sys.stderr)
        args += ["-c", windows_to_wsl(folder)]

    if auto:
        args.append("bash -ic 'klaud; exec bash'")

    return args


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: session_launch.py <sesN>", file=sys.stderr)
        return 2

    name = sys.argv[1]
    cfg = load_config()
    info = cfg.get(name)
    if info is None:
        print(f"[session_launch] no config for {name!r}. Run the Sessions GUI and pick a folder.", file=sys.stderr)
        return 1

    if info.get("symlink_memory") and info.get("folder"):
        ok, msg = ensure_memory_symlink(info["folder"])
        prefix = "[session_launch] symlink:" if ok else "[session_launch] symlink FAILED:"
        print(f"{prefix} {msg}", file=sys.stderr)

    args = build_tmux_args(name, info)
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())
