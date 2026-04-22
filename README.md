# Claude Sessions

Manage multiple [Claude Code](https://claude.com/claude-code) conversations across your PC and Android devices. Each "session" is a named WSL tmux session pinned to a project folder. Type `ses1`, `ses2`, etc. in any terminal and attach. Works identically over SSH from a phone — including live mirrored view.

## Why I built this

I had several Claude Code projects running in parallel on a Windows PC, and I often wanted to check on or continue one from my phone or tablet while away from my desk. Plain remote desktop (VNC) over Tailscale worked but typing on Android's keyboard through a remote-desktop view was miserable. SSH from Termux gave me a real keyboard, but spinning up `tmux attach -t somename` with different names for each project got tedious, and Claude Code's per-project memory on Windows and WSL lived under different slugs so I'd lose context when crossing the OS boundary.

This tool fixes all three:

- Short, memorable commands (`ses1`, `ses2`, ...) that attach from anywhere.
- Each session is tied to a project folder, configured once in a GUI.
- Optional symlink so Claude's memory is shared between Windows-side and WSL-side Claude Code for a given folder.
- Multiple devices (laptop, phone, tablet) can attach to the same session simultaneously. Type on one; see it on the others in real time.

## How this was built

All of the code in this repository was written by [Claude Code](https://claude.com/claude-code) running Claude Opus 4.7 (Anthropic's CLI coding assistant). I ([@kevinkicho](https://github.com/kevinkicho)) described the need, tested each iteration on a real Galaxy S22 + Galaxy Tab S7 + Windows 11 setup, and fed concrete feedback (observed errors, wrong behavior, UI tweaks) back to Claude. Claude handled the architecture, the Python and Kotlin code, the shell/PowerShell scripting, and the OS-specific plumbing.

Treat this as working but lightly reviewed. PRs welcome.

## How it works

```
Windows PowerShell        SSH from phone Termux        SSH from tablet Termux
        |                          |                             |
        +------------- tmux attach --------+------- tmux attach -+
                                           |
                                  shared named session
                                 "ses1" (or ses2, ...)
                                           |
                                   WSL Ubuntu bash
                                 opened in folder X
                                           |
                              klaud (claude --resume
                              --dangerously-skip-permissions)
```

Under the hood:

1. A GUI (`sessions_gui.pyw`) edits `sessions.json`, which maps each name (ses1, ses2, ...) to a folder and two boolean toggles.
2. On save, the GUI generates `ses1.cmd`, `ses2.cmd`, ... in a directory on your Windows PATH.
3. Each `sesN.cmd` calls `session_launch.py`, which runs `wsl -d Ubuntu -- tmux new-session -A -s sesN -c <folder>`. `-A` attaches if the session already exists, creates otherwise.
4. If "Auto-klaud" is on, the session's first run executes `klaud`, a bash function that either resumes the most recent Claude conversation for that folder or starts a new one with `--dangerously-skip-permissions`.
5. If "Link memory" is on, a symlink is created inside WSL at `~/.claude/projects/<wsl-slug>` pointing to `/mnt/c/Users/<you>/.claude/projects/<win-slug>`. Effect: Windows-side and WSL-side Claude share the same per-project conversation history for that folder.

## Features

- Dark-mode GUI (Sun Valley theme) with tooltips and a built-in Help dialog
- Dynamic session list: starts with 3 rows, `+` button adds more with no hard limit
- Per-row `x` button removes a session (doesn't touch Claude memory or running tmux state)
- Per-row Launch button opens that session in a new console window
- All options persist to `sessions.json`
- Works alongside Windows and WSL Claude installs, sharing memory via symlink
- **SSH key rotation panel** — rotation of the SSH key used by your Android devices, with ADB push from the PC and a one-word command on each device

## SSH key rotation panel

Click **🔑 Rotate SSH** in the toolbar to open a dialog that drives the whole key-rotation flow. Two PC-side buttons + one device-side step:

- **Step 1 — Push rotate-key script to connected device(s)** — ADB-pushes `rotate-key.sh` to `/sdcard/rk.sh` and the current private key to `/sdcard/Download/id_ed25519` on every connected device. Idempotent — safe to re-run. One-time per device.
- **Step 2a — 🔑 Generate new SSH key (UAC swap)** — generates a new ed25519 keypair locally, triggers one UAC prompt to replace `C:\ProgramData\ssh\administrators_authorized_keys`, and issues a 10-minute rotation token. Does **not** push to any device — that's 2b. Click ONCE per rotation.
- **Step 2b — 📤 Push current key to connected device(s)** — pushes the currently-staged private key to whatever devices are connected right now. Split out from 2a because most setups only have one free USB port, so the normal workflow is: rotate once (2a) → plug in device 1, push (2b) → unplug → plug in device 2, push → repeat. Safe to click many times.
- **Step 3 — on each device** — close Termux (swipe out of Recents), reopen it, and type `rotate-key`. This can't be automated from the PC because Android blocks ADB from holding Termux's `RUN_COMMAND` permission; see [Why no "Run rotate-key" button?](#why-no-run-rotate-key-button) below.

The dialog also shows:

- Live list of ADB-connected devices (with a **Rescan** button).
- A **Remote token** field with copy-to-clipboard and a live expiry countdown, for updating devices not plugged in (they fetch over Tailnet from the PC's `/keyfile` endpoint — see [speech-to-text-app](https://github.com/kevinkicho/speech-to-text-app)).
- A scrolling **Log** of every step.

### Device-side `rotate-key`

Each Android device needs the `rotate-key` Termux command installed once. One-time bootstrap, run inside Termux after clicking Step 1 on the PC:

```
termux-setup-storage
bash /sdcard/rk.sh install
```

(The GUI's Step 1 button pushes `rotate-key.sh` to `/sdcard/rk.sh`; `termux-setup-storage` lets Termux actually read that file.) After install, every future rotation is:

```
rotate-key
```

The script self-updates from `/sdcard/Download/rotate-key.sh` on every run, so PC-side script improvements propagate automatically without a re-install.

### Why no "Run rotate-key" button?

An earlier version of the panel had a third button that dispatched `rotate-key` via Termux's `RUN_COMMAND` intent, aiming for fully headless rotation. It was removed because it can't work reliably on stock Android: the `RUN_COMMAND` intent requires the caller to hold `com.termux.permission.RUN_COMMAND`, and the ADB shell user (`com.android.shell`) can't request or be granted that permission, so Android always rejects the dispatch. The button's "failed" messages were misleading (it kept claiming Step 1 wasn't done when it actually was). Typing `rotate-key` once in Termux is the honest path.

### Files that ship with rotation

- `rotate-ssh.bat` — double-clickable CLI entry point.
- `rotate-ssh.ps1` — main PowerShell; `-TokenOnly` skips keygen+swap and just issues a fresh token for the current key.
- `swap-authorized-keys.ps1` — the elevated helper that replaces `administrators_authorized_keys` (invoked via `Start-Process -Verb RunAs`).
- `rotate-key.sh` — Termux client for the device side (install, self-update, local-file or token-fetch modes).

The private key, public key, token state, and rotation logs are kept outside the repo (in a local `tools/` directory that is gitignored and contains the actual secret material — never commit it).

## Requirements

**Windows PC (10 build 19041+ or 11):**
- WSL 2 with Ubuntu installed
- Python 3.10 or newer (for the GUI)
- `tmux` installed inside Ubuntu
- `claude` CLI available inside Ubuntu
- OpenSSH Server (Windows optional feature)

**Android device (phone or tablet):**
- Termux — install from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub releases](https://github.com/termux/termux-app/releases), **not the Play Store**. The Play Store build is deprecated (last updated 2020), ships with stale packages, and `pkg install` / `pkg update` against it often fails.
- Termux's `openssh` package

**Shared transport:**
- Tailscale (recommended) or any network that connects your phone to your PC

## Setup

### Part A — Windows side (one-time)

1. **Install WSL and Ubuntu** in an elevated PowerShell if not already:

```
wsl --install -d Ubuntu
```

Reboot if prompted. On first launch of Ubuntu, create a Linux username and password.

2. **Install tmux, Node, and Claude Code inside Ubuntu:**

```
wsl -d Ubuntu
sudo apt update
sudo apt install -y tmux nodejs npm
sudo npm install -g @anthropic-ai/claude-code
```

3. **Add the `klaud` function** to your WSL `~/.bashrc`:

```
cat >> ~/.bashrc << 'EOF'

klaud() {
    local slug
    slug=$(pwd | sed 's|/|-|g; s|_|-|g')
    local projdir="$HOME/.claude/projects/$slug"
    if compgen -G "$projdir/*.jsonl" > /dev/null; then
        command claude --resume --dangerously-skip-permissions "$@"
    else
        command claude --dangerously-skip-permissions "$@"
    fi
}
EOF
source ~/.bashrc
```

4. **Enable OpenSSH Server** in an elevated Windows PowerShell so your phone can SSH in:

```
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

5. **Install Python and the dark theme** in regular PowerShell:

```
pip install sv-ttk
```

6. **Clone this repo and set paths.**

```
git clone https://github.com/kevinkicho/claude-sessions-app.git
cd claude-sessions-app
```

The scripts expect:

- `session_launch.py` and `sessions_gui.pyw` somewhere on disk.
- `sessions.json` next to them (auto-created on first save).
- A directory on your Windows PATH where `sesN.cmd` wrappers will be written. By default this is `C:\Users\<YourUsername>\.local\bin\`.

If `.local\bin` isn't on your PATH, add it (this happens once, via System Properties → Environment Variables, or PowerShell):

```
[Environment]::SetEnvironmentVariable(
    'PATH',
    [Environment]::GetEnvironmentVariable('PATH','User') + ';C:\Users\' + $env:USERNAME + '\.local\bin',
    'User'
)
```

7. **Edit the hardcoded paths** in `session_launch.py` and `sessions_gui.pyw` to match your setup:

- `CONFIG_PATH` and `LAUNCHER` point at wherever you cloned the scripts.
- `WRAPPER_DIR` points at your PATH-bin directory.
- The Windows-side `.claude\projects` base is `C:\Users\<you>\.claude\projects\`.

8. **Launch the GUI:**

```
pythonw "path\to\sessions_gui.pyw"
```

Optionally create a Desktop shortcut that points at `pythonw.exe` with the script path as an argument (so no console window appears).

### Part B — First session

1. Double-click the GUI (or launch via `pythonw`).
2. For `ses1`, click Browse and select a project folder. Leave "Auto-klaud" and "Link memory" checked.
3. Click Save. The GUI creates `ses1.cmd` on your PATH and (if Link memory is on) the WSL symlink.
4. In any PowerShell or cmd window, type `ses1`. A new terminal opens, you're in that project's folder inside WSL, and Claude Code auto-resumes.

### Part C — Android setup (per device)

1. **Install Termux from [F-Droid](https://f-droid.org/packages/com.termux/) or [GitHub releases](https://github.com/termux/termux-app/releases)** — **not the Play Store** (that build is deprecated; `pkg update` fails against its frozen repos). If you already have the Play Store version, uninstall it first (signatures differ, so you can't upgrade in place). Open Termux once, agree to the startup message, and let the bootstrap finish.

2. **Install OpenSSH in Termux:**

```
pkg update
pkg install -y openssh
```

3. **Grant storage permission** (needed for sharing files between Termux and the rest of the phone):

```
termux-setup-storage
```

A system dialog pops up. Tap Allow.

4. **Generate an SSH key:**

```
ssh-keygen -t ed25519 -C "my-phone"
```

Press Enter to accept defaults (no passphrase for simplicity).

5. **Copy the public key:**

```
cat ~/.ssh/id_ed25519.pub
```

Long-press the output and Copy.

6. **On Windows (elevated PowerShell),** add the public key to the admin authorized-keys file (required for admin accounts):

```
$pubKey = 'paste your public key here inside these single quotes'
Add-Content -Path 'C:\ProgramData\ssh\administrators_authorized_keys' -Value $pubKey
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r
icacls C:\ProgramData\ssh\administrators_authorized_keys /grant "Administrators:F"
icacls C:\ProgramData\ssh\administrators_authorized_keys /grant "SYSTEM:F"
```

(If your Windows user is a non-administrator, use `C:\Users\<you>\.ssh\authorized_keys` instead.)

7. **Test SSH from Termux:**

```
ssh your-windows-user@your-pc-ip
```

Type `yes` on the first host-key prompt. You should land at `C:\Users\your-user>` without a password prompt.

8. **Add the `sesN` aliases** to Termux so typing `ses1`, `ses2`, etc. from Termux SSHs in and runs the corresponding Windows wrapper:

```
for i in $(seq 1 50); do
  grep -q "^alias ses$i=" ~/.bashrc ||
  echo "alias ses$i='ssh -t your-windows-user@your-pc-ip ses$i'" >> ~/.bashrc
done
source ~/.bashrc
```

Replace `your-windows-user` and `your-pc-ip` with your values. The range `1..50` is arbitrary; bump it if you plan to add more sessions.

9. **Keep Termux alive when you switch apps:**

```
termux-wake-lock
```

This pins a persistent notification. While it's visible, Android won't kill your Termux process when you briefly open another app.

10. Repeat for your second device (tablet, other phone, etc.). Reuse the same SSH key or generate a new one per device.

## Daily usage

**On the PC:** open any terminal, type `ses1`. Done.

**On a phone/tablet:** open Termux, type `ses1`. Done.

Multiple devices can attach at once. Typing on one mirrors live to the others.

## Configuration

`sessions.json` (auto-generated by the GUI):

```
{
  "ses1": {
    "folder": "C:\\Users\\you\\Desktop\\project-a",
    "auto_claude": true,
    "symlink_memory": true
  },
  "ses2": { ... },
  "ses3": { ... }
}
```

You can edit this file directly if you prefer; the GUI re-reads it when you click **Reload**.

## Tmux keybindings

The repo's `~/.tmux.conf` seed sets:

- Prefix key: `Ctrl-A` (easier than `Ctrl-B` on phone keyboards)
- `Ctrl-A` then `d`: detach without killing the session
- `Ctrl-A` then `c`: new window (tab-like)
- `Ctrl-A` then `?`: show all shortcuts
- Mouse mode on: scroll and click to focus
- 20K lines of scrollback

## File layout

```
claude-sessions-app/
  session_launch.py     # reads sessions.json, runs wsl tmux new-session
  sessions_gui.pyw      # dark-mode GUI (Tkinter + sv-ttk)
  sessions.json         # config (auto-created on first Save)
  README.md
  LICENSE
```

## Customization

- **Change the default Whisper-less wrapper dir:** edit `WRAPPER_DIR` in `sessions_gui.pyw` and `session_launch.py`.
- **Change the default WSL distro:** replace `Ubuntu` in `session_launch.py`'s `wsl -d Ubuntu` calls.
- **Change the shell that tmux launches:** default is bash; edit the `build_tmux_args` function.
- **Change the accent color in dark mode:** edit `DARK["accent"]` at the top of `sessions_gui.pyw`.

## Repository

https://github.com/kevinkicho/claude-sessions-app

## License

MIT.

## Credits

- [Claude Code](https://claude.com/claude-code) (Claude Opus 4.7) wrote all of the code in this repo. I described the need, provided feedback after each iteration, and tested on real hardware.
- Dark theme by [sv-ttk](https://github.com/rdbende/Sun-Valley-ttk-theme).
- [tmux](https://github.com/tmux/tmux) for the shared-session backbone.
