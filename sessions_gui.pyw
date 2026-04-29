"""Tkinter GUI for editing sessions.json.

Dynamic session list — start with 3 rows by default. A minimal ✕ per row
removes that session; a single centered ＋ below the list adds one.

Each row: folder picker, auto-claude toggle, link-memory toggle, launch,
remove. Tooltips explain every field; a Help button opens a readme.

Per-session wrappers (ses1.cmd, ses2.cmd, ...) are created in
%USERPROFILE%\\.local\\bin automatically whenever the GUI saves.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

try:
    import sv_ttk
    HAS_SV_TTK = True
except ImportError:
    HAS_SV_TTK = False

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "sessions.json"
LAUNCHER = HERE / "session_launch.py"
WRAPPER_DIR = Path.home() / ".local" / "bin"

DEFAULT_ROWS = 3

# Sun Valley dark palette, used to color classic tk widgets so they blend with
# the themed ttk widgets.
DARK = {
    "bg":       "#1c1c1c",
    "surface":  "#242424",
    "surface2": "#2b2b2b",
    "border":   "#3a3a3a",
    "fg":       "#f0f0f0",
    "fg_mute":  "#9b9b9b",
    "accent":   "#60cdff",
    "danger":   "#ff6467",
    "tooltip_bg": "#2e2e2e",
    "tooltip_fg": "#f0f0f0",
}

sys.path.insert(0, str(HERE))
from session_launch import ensure_memory_symlink  # noqa: E402


def apply_dark_title_bar(window: tk.Misc) -> None:
    """Tell Windows DWM to render this window's caption (title bar) in dark mode.
    Works on Windows 10 build 19041+ and Windows 11. No-op elsewhere."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        # Attribute index for immersive dark mode:
        #   20 on Windows 11 (build 22000+)
        #   19 on Windows 10 1909–21H2 (build 18985+)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                return
    except Exception:
        pass


HELP_TEXT = """Claude Sessions — how it works

Each row is a named tmux session inside WSL Ubuntu.
Typing the session name (e.g. ses1) in any terminal attaches to it.

- First call creates the tmux session in the folder you picked.
- Later calls attach to the same session. Your laptop, phone, and tablet
  can all attach at once and see the same live screen.

Fields

Name
  Session name. Same string you type in any terminal (PowerShell, cmd, or
  from Termux over SSH).

Folder
  Where Claude Code opens when the session is first created. Pick any
  project directory on the Windows filesystem.

Auto-klaud
  When on, the session's first run executes `klaud`, which is
  `claude --resume --dangerously-skip-permissions` if there is an existing
  conversation for that folder, otherwise a fresh
  `claude --dangerously-skip-permissions`.

Link memory (default: on)
  Creates a symlink inside WSL at ~/.claude/projects/<wsl-slug> pointing
  to the Windows side's ~/.claude/projects/<win-slug>. Effect: Claude Code
  running in WSL and Claude Code running in native Windows see the same
  per-project history for that folder. Safe and idempotent — never
  overwrites a real directory.

Launch
  Opens a new console window and runs the session. Saves changes first.

✕ (per row)
  Removes this row from sessions.json and the sesN.cmd wrapper.
  Running tmux sessions and Claude memory are NOT touched.

＋ (below all rows)
  Appends the next session (sesN+1) with link memory on by default.
  No practical limit on how many you can add.

Buttons

Save
  Writes sessions.json. For any row with Link memory on, also creates or
  refreshes the symlink in WSL.

Reload
  Discards unsaved UI changes and re-reads sessions.json from disk.

Phone / tablet setup

On each Android device in Termux, run this once (replace USER and
PC-TAILNET-IP with your Windows username and the PC's Tailscale IP):

  for i in $(seq 1 50); do
    grep -q "^alias ses$i=" ~/.bashrc ||
    echo "alias ses$i='ssh -t USER@PC-TAILNET-IP ses$i'" >> ~/.bashrc
  done
  source ~/.bashrc

That covers ses1..ses50 across your devices.
"""


class Tooltip:
    """Tiny hover tooltip for any widget."""
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self.tip: tk.Toplevel | None = None
        self._after_id: str | None = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self.tip,
            text=self.text,
            background=DARK["tooltip_bg"],
            foreground=DARK["tooltip_fg"],
            relief="solid",
            borderwidth=1,
            wraplength=360,
            font=("TkDefaultFont", 9),
            padx=8,
            pady=4,
            justify="left",
        ).pack()

    def _hide(self, _=None):
        self._cancel()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


def default_entry() -> dict:
    return {"folder": "", "auto_claude": True, "symlink_memory": True}


def _ses_num(name: str) -> int:
    m = re.match(r"ses(\d+)$", name)
    return int(m.group(1)) if m else 0


def load_config() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    if not cfg:
        for i in range(1, DEFAULT_ROWS + 1):
            cfg[f"ses{i}"] = default_entry()
    for name, entry in list(cfg.items()):
        for k, v in default_entry().items():
            entry.setdefault(k, v)
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: cfg[k] for k in sorted(cfg.keys(), key=_ses_num)}
    CONFIG_PATH.write_text(json.dumps(ordered, indent=2), encoding="utf-8")


def ensure_wrapper(name: str) -> None:
    WRAPPER_DIR.mkdir(parents=True, exist_ok=True)
    wrapper = WRAPPER_DIR / f"{name}.cmd"
    if not wrapper.exists():
        wrapper.write_text(
            f'@echo off\r\npython "{LAUNCHER}" {name} %*\r\n',
            encoding="ascii",
        )


def remove_wrapper(name: str) -> None:
    wrapper = WRAPPER_DIR / f"{name}.cmd"
    try:
        wrapper.unlink(missing_ok=True)
    except Exception:
        pass


TOOLTIPS = {
    "Name": "Session name. Type this in any terminal to attach (e.g. ses1).",
    "Folder": "The directory Claude Code opens in when the session is first created.",
    "Browse": "Pick a folder from a file dialog.",
    "Auto-klaud": "If on, running the session auto-starts klaud: resume an existing Claude conversation for that folder, or start a fresh one with --dangerously-skip-permissions.",
    "Link memory": "Creates a WSL symlink so Windows and WSL Claude share the same per-project conversation history for this folder. Safe and idempotent.",
    "Launch": "Saves changes and opens a new console that runs this session.",
    "Remove row": "Removes this row and its sesN.cmd wrapper. Does not touch Claude memory or a running tmux session.",
    "Add row": "Appends the next session (sesN+1) with default settings (link memory on).",
}


def make_flat_icon_button(parent, text, command, tooltip=None, fg=None):
    """A borderless, fill-less icon button colored to blend with the dark theme."""
    if fg is None:
        fg = DARK["fg_mute"]
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        relief="flat",
        borderwidth=0,
        highlightthickness=0,
        bg=DARK["bg"],
        fg=fg,
        activebackground=DARK["surface2"],
        activeforeground=DARK["fg"],
        cursor="hand2",
        font=("TkDefaultFont", 14),
        padx=6,
        pady=0,
        takefocus=0,
    )
    if tooltip:
        Tooltip(btn, tooltip)
    return btn


class SessionsApp(tk.Tk):
    # Cap on rows-area height — beyond this, a scrollbar appears instead of
    # growing the window further.
    MAX_ROWS_HEIGHT = 500

    def __init__(self):
        super().__init__()
        self.title("Claude Sessions")
        self.minsize(900, 220)
        if HAS_SV_TTK:
            sv_ttk.set_theme("dark")
        self.configure(bg=DARK["bg"])
        self.option_add("*Background", DARK["bg"])
        self.option_add("*Foreground", DARK["fg"])
        self.option_add("*Toplevel.Background", DARK["bg"])
        # sv-ttk's dark theme leaves `background` unset on several widgets,
        # which makes them fall back to SystemButtonFace (white) on Windows.
        # Force dark backgrounds explicitly so the whole UI blends.
        style = ttk.Style(self)
        for s in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton",
                  "TSeparator", "TPanedwindow"):
            style.configure(s, background=DARK["bg"])
        style.configure("TLabel", foreground=DARK["fg"])
        style.configure("TCheckbutton", foreground=DARK["fg"])
        apply_dark_title_bar(self)
        self.config_data = load_config()
        self._row_state: list[dict] = []
        self._add_btn_widget: tk.Widget | None = None
        self._build_ui()
        self._repopulate_rows()
        # Set an initial size now that rows are laid out.
        self.geometry(f"1200x{self._fitted_height()}")

    # ---- building ----

    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=(16, 10, 16, 4))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="?  Help", command=self._show_help, width=10).pack(side=tk.LEFT)
        diagnose_btn = ttk.Button(toolbar, text="🔧 Diagnose",
                                  command=self._open_diagnostics, width=12)
        diagnose_btn.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(diagnose_btn,
                "Run a self-check of every prerequisite (WSL, Ubuntu, tmux, claude, "
                "klaud, PATH, sessions, OpenSSH, authorized_keys, Tailscale, ADB). "
                "Shows a fix hint for anything missing.")
        rotate_toolbar_btn = ttk.Button(toolbar, text="🔑 Rotate SSH",
                                        command=self._open_rotation, width=14)
        rotate_toolbar_btn.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(rotate_toolbar_btn,
                "Open the SSH key rotation panel. Three numbered steps: set up a "
                "device (one-time), rotate the key on the PC (UAC), dispatch rotate-key "
                "on each connected device.")
        ttk.Label(
            toolbar,
            text="Type a session name (e.g. ses1) in any terminal — PC, SSH, or Termux — to attach.",
            foreground="#555",
        ).pack(side=tk.LEFT, padx=12)

        body_wrap = ttk.Frame(self, padding=(16, 4))
        body_wrap.pack(fill=tk.X)
        self._body_wrap = body_wrap

        self._canvas = tk.Canvas(body_wrap, highlightthickness=0, bg=DARK["bg"], bd=0, height=0)
        self._scroll = ttk.Scrollbar(body_wrap, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scroll.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        # Scrollbar packing is managed dynamically by _fit_canvas_height.

        self._rows_frame = ttk.Frame(self._canvas)
        self._rows_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.create_window((0, 0), window=self._rows_frame, anchor="nw",
                                   tags="rows_frame")
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure("rows_frame", width=e.width))
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self._build_headers()

        footer = ttk.Frame(self, padding=(16, 6, 16, 14))
        footer.pack(fill=tk.X)

        self.status = ttk.Label(footer, text="")
        self.status.pack(side=tk.LEFT)

        save_btn = ttk.Button(footer, text="Save", command=self._save, width=10)
        save_btn.pack(side=tk.RIGHT)
        Tooltip(save_btn, "Writes sessions.json. Creates/refreshes symlinks for rows with Link memory on. Ensures each session has its wrapper command.")

        reload_btn = ttk.Button(footer, text="Reload", command=self._reload, width=10)
        reload_btn.pack(side=tk.RIGHT, padx=(0, 6))
        Tooltip(reload_btn, "Discards unsaved UI changes and re-reads sessions.json from disk.")

    def _build_headers(self):
        headers = ("Name", "Folder", "Browse", "Auto-klaud", "Link memory", "Launch", "")
        for col, text in enumerate(headers):
            lbl = ttk.Label(self._rows_frame, text=text, font=("TkDefaultFont", 9, "bold"))
            lbl.grid(row=0, column=col, sticky=tk.W, padx=(0, 6), pady=(0, 6))
            if text and text in TOOLTIPS:
                Tooltip(lbl, TOOLTIPS[text])
        self._rows_frame.grid_columnconfigure(1, weight=1)

    # ---- row management ----

    def _repopulate_rows(self):
        """Full rebuild — only called on Reload. Add/Remove are surgical."""
        for state in self._row_state:
            for w in state["widgets"]:
                try:
                    w.destroy()
                except Exception:
                    pass
        self._row_state.clear()
        if self._add_btn_widget is not None:
            try:
                self._add_btn_widget.destroy()
            except Exception:
                pass
            self._add_btn_widget = None

        names = sorted(self.config_data.keys(), key=_ses_num)
        for idx, name in enumerate(names, start=1):
            self._build_row(idx, name, self.config_data[name])

        self._place_add_button()
        self.after(0, self._fit_window)

    def _place_add_button(self):
        """Create the ＋ button if it doesn't exist, and grid it in the row
        immediately after the last session row. Idempotent."""
        row_idx = len(self._row_state) + 1
        if self._add_btn_widget is None:
            btn = make_flat_icon_button(
                self._rows_frame, "＋", self._add_row,
                tooltip=TOOLTIPS["Add row"], fg=DARK["accent"],
            )
            btn.configure(font=("TkDefaultFont", 18, "bold"))
            self._add_btn_widget = btn
        self._add_btn_widget.grid(row=row_idx, column=0, columnspan=7, pady=(10, 4))

    def _build_row(self, row_idx: int, name: str, entry: dict):
        folder_var = tk.StringVar(value=entry.get("folder", ""))
        auto_var = tk.BooleanVar(value=bool(entry.get("auto_claude", True)))
        link_var = tk.BooleanVar(value=bool(entry.get("symlink_memory", True)))

        widgets = []

        lbl_name = ttk.Label(self._rows_frame, text=name, width=7)
        lbl_name.grid(row=row_idx, column=0, sticky=tk.W, pady=3)
        widgets.append(lbl_name)
        Tooltip(lbl_name, f"Type '{name}' in any terminal to attach.")

        entry_folder = ttk.Entry(self._rows_frame, textvariable=folder_var)
        entry_folder.grid(row=row_idx, column=1, sticky=tk.EW, padx=(0, 6), pady=3)
        widgets.append(entry_folder)
        Tooltip(entry_folder, TOOLTIPS["Folder"])

        btn_browse = ttk.Button(self._rows_frame, text="Browse…", width=9,
                                command=lambda v=folder_var: self._browse(v))
        btn_browse.grid(row=row_idx, column=2, padx=(0, 10), pady=3)
        widgets.append(btn_browse)
        Tooltip(btn_browse, TOOLTIPS["Browse"])

        chk_auto = ttk.Checkbutton(self._rows_frame, variable=auto_var)
        chk_auto.grid(row=row_idx, column=3, padx=(0, 10), pady=3)
        widgets.append(chk_auto)
        Tooltip(chk_auto, TOOLTIPS["Auto-klaud"])

        chk_link = ttk.Checkbutton(self._rows_frame, variable=link_var)
        chk_link.grid(row=row_idx, column=4, padx=(0, 10), pady=3)
        widgets.append(chk_link)
        Tooltip(chk_link, TOOLTIPS["Link memory"])

        btn_launch = ttk.Button(self._rows_frame, text=f"Launch {name}", width=12,
                                command=lambda n=name: self._launch(n))
        btn_launch.grid(row=row_idx, column=5, pady=3)
        widgets.append(btn_launch)
        Tooltip(btn_launch, TOOLTIPS["Launch"])

        btn_remove = make_flat_icon_button(
            self._rows_frame, "✕", lambda n=name: self._remove_row(n),
            tooltip=TOOLTIPS["Remove row"], fg=DARK["fg_mute"],
        )
        btn_remove.grid(row=row_idx, column=6, padx=(6, 0), pady=3)
        widgets.append(btn_remove)

        self._row_state.append({
            "name": name,
            "folder_var": folder_var,
            "auto_var": auto_var,
            "link_var": link_var,
            "widgets": widgets,
        })

    def _add_row(self):
        """Surgical add: create only the new row's widgets, shift the ＋ button."""
        self._commit_to_config()
        nums = [_ses_num(n) for n in self.config_data.keys()]
        next_num = (max(nums) if nums else 0) + 1
        name = f"ses{next_num}"
        self.config_data[name] = default_entry()
        ensure_wrapper(name)

        new_row_idx = len(self._row_state) + 1
        self._build_row(new_row_idx, name, self.config_data[name])
        self._place_add_button()
        self._fit_window()
        self._flash_status(f"Added {name}. Pick a folder, then Save.")

    def _remove_row(self, name: str):
        if not messagebox.askyesno(
            "Remove session",
            f"Remove {name}?\n\nThis deletes the row from sessions.json and removes "
            f"the {name}.cmd wrapper command. It does NOT kill a running tmux session "
            f"or delete any Claude memory.",
            parent=self,
        ):
            return
        self._commit_to_config()
        # Surgical remove: only destroy this row's widgets; re-grid the rest
        # in-place so their positions stay continuous.
        target = next((s for s in self._row_state if s["name"] == name), None)
        if target is None:
            return
        for w in target["widgets"]:
            try:
                w.destroy()
            except Exception:
                pass
        self._row_state.remove(target)
        self.config_data.pop(name, None)
        save_config(self.config_data)
        remove_wrapper(name)
        # Shift remaining rows up to fill the gap.
        for idx, state in enumerate(self._row_state, start=1):
            for w in state["widgets"]:
                info = w.grid_info()
                if info:
                    w.grid_configure(row=idx)
        self._place_add_button()
        self._fit_window()
        self._flash_status(f"Removed {name}.")

    # ---- actions ----

    def _browse(self, var: tk.StringVar):
        initial = var.get() or os.path.expanduser("~")
        if not Path(initial).exists():
            initial = os.path.expanduser("~")
        folder = filedialog.askdirectory(initialdir=initial, parent=self)
        if folder:
            var.set(os.path.normpath(folder))

    def _commit_to_config(self):
        for st in self._row_state:
            self.config_data[st["name"]] = {
                "folder": st["folder_var"].get().strip(),
                "auto_claude": bool(st["auto_var"].get()),
                "symlink_memory": bool(st["link_var"].get()),
            }

    def _apply_symlinks(self) -> list[str]:
        msgs = []
        for name, info in self.config_data.items():
            if info.get("symlink_memory") and info.get("folder"):
                ok, msg = ensure_memory_symlink(info["folder"])
                prefix = "✓" if ok else "✗"
                msgs.append(f"{prefix} {name}")
        return msgs

    def _save(self):
        self._commit_to_config()
        save_config(self.config_data)
        for name in self.config_data:
            ensure_wrapper(name)
        msgs = self._apply_symlinks()
        if msgs:
            self._flash_status("Saved.  " + "  ".join(msgs))
        else:
            self._flash_status("Saved.")

    def _reload(self):
        self.config_data = load_config()
        self._repopulate_rows()
        self._flash_status("Reloaded from disk.")

    def _launch(self, name: str):
        self._save()
        if not LAUNCHER.exists():
            messagebox.showerror("Launcher missing", f"Could not find {LAUNCHER}", parent=self)
            return
        try:
            CREATE_NEW_CONSOLE = 0x00000010
            subprocess.Popen(
                [sys.executable, str(LAUNCHER), name],
                creationflags=CREATE_NEW_CONSOLE,
            )
            self._flash_status(f"Launched {name}.")
        except Exception as exc:
            messagebox.showerror("Launch failed", str(exc), parent=self)

    def _open_rotation(self):
        RotationDialog(self)

    def _open_diagnostics(self):
        DiagnosticsDialog(self)

    def _show_help(self):
        win = tk.Toplevel(self)
        win.title("Claude Sessions — Help")
        win.geometry("720x600")
        win.configure(bg=DARK["bg"])
        apply_dark_title_bar(win)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(
            frame,
            wrap="word",
            font=("TkDefaultFont", 10),
            bg=DARK["surface"],
            fg=DARK["fg"],
            insertbackground=DARK["fg"],
            selectbackground=DARK["accent"],
            selectforeground=DARK["bg"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=10,
        )
        text.insert("1.0", HELP_TEXT)
        text.configure(state="disabled")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))

    def _on_mousewheel(self, event):
        if self._canvas.winfo_exists():
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _flash_status(self, msg: str):
        self.status.config(text=msg)
        self.after(5000, lambda: self.status.config(text=""))

    # ---- window sizing ----

    def _fit_canvas_height(self):
        """Size the scrollable canvas to the rows-frame height, up to a cap.
        Show the scrollbar only when content exceeds the cap."""
        self._rows_frame.update_idletasks()
        req = self._rows_frame.winfo_reqheight()
        target = min(req, self.MAX_ROWS_HEIGHT)
        self._canvas.configure(height=target)
        if req > self.MAX_ROWS_HEIGHT:
            if not self._scroll.winfo_ismapped():
                self._scroll.pack(side="right", fill="y")
        else:
            if self._scroll.winfo_ismapped():
                self._scroll.pack_forget()

    def _fitted_height(self) -> int:
        """Total window height to tightly fit current content."""
        self._fit_canvas_height()
        self.update_idletasks()
        # reqheight of the root = sum of all packed children's reqheights.
        return self.winfo_reqheight()

    def _fit_window(self):
        """Resize the window height to fit current content, preserving width.
        Forces a paint pass before resizing so the newly-exposed area shows our
        dark background instead of Windows' default white brush."""
        current_w = self.winfo_width() if self.winfo_width() > 1 else 1200
        target_h = self._fitted_height()
        # Paint pending updates first, then apply the geometry change, then
        # immediately flush again so DWM composites with our dark content.
        self.update_idletasks()
        self.geometry(f"{current_w}x{target_h}")
        self.update_idletasks()


ADB_PATH = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
SSH_KEY_PATH = HERE / "ssh_key"
SSH_PUB_PATH = HERE / "ssh_key.pub"
TOKENS_PATH = HERE / "rotation-tokens.json"
SWAP_PS1 = HERE / "swap-authorized-keys.ps1"
ROTATE_KEY_SH = HERE / "rotate-key.sh"

def _run(cmd, timeout=30):
    """Run a command, return (ok, stdout+stderr). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return False, str(e)


class RotationDialog(tk.Toplevel):
    """Interactive SSH key rotation window.

    "Rotate keys" — generates a fresh ed25519 keypair, triggers the elevated
    swap of administrators_authorized_keys (one UAC click), issues a 10-min
    rotation token, and pushes the new private key to every ADB-connected
    device at /sdcard/Download/id_ed25519.

    "Push current to connected" — re-pushes the existing key without rotating,
    useful when you plug in a fresh device after a rotation.
    """

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("Rotate SSH Keys")
        self.geometry("720x620")
        self.minsize(620, 520)
        self.configure(bg=DARK["bg"])
        apply_dark_title_bar(self)

        self._token: str | None = None
        self._token_expires_at = 0
        self._is_busy = False

        self._build_ui()
        self.after(100, self._refresh_devices_async)
        self._tick()

    # ---- UI construction ----

    def _build_ui(self):
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)

        ttk.Label(outer, text="Rotate SSH Keys",
                  font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text=("Generates a new keypair on this PC, installs the new public key "
                  "(one UAC prompt), pushes the new private key to connected Android "
                  "devices via ADB, and issues a token so remote devices can fetch "
                  "over Tailnet. On each device, run 'rotate-key' in Termux."),
            wraplength=660, justify="left", foreground="#aaa",
        ).pack(anchor="w", pady=(4, 10))

        # --- device list ---
        dev = ttk.LabelFrame(outer, text=" Connected ADB devices ")
        dev.pack(fill=tk.X, pady=4)
        self.devices_box = tk.Listbox(
            dev, height=4, bg=DARK["surface"], fg=DARK["fg"],
            selectbackground=DARK["accent"], selectforeground=DARK["bg"],
            relief="flat", borderwidth=0, highlightthickness=0, font=("Consolas", 10),
        )
        self.devices_box.pack(fill=tk.X, padx=8, pady=(6, 4))
        row = ttk.Frame(dev)
        row.pack(fill=tk.X, padx=8, pady=(0, 6))
        rescan_btn = ttk.Button(row, text="Rescan", width=10,
                                command=self._refresh_devices_async)
        rescan_btn.pack(side=tk.LEFT)
        Tooltip(rescan_btn,
                "Re-query adb for currently-connected devices. Useful after "
                "plugging in a new device or approving a USB-debugging prompt.")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(row, textvariable=self.status_var,
                  foreground="#888").pack(side=tk.LEFT, padx=10)

        # --- Step 1 — one-time device setup ---
        step1 = ttk.LabelFrame(outer, text=" Step 1 — Set up a device (one-time per device, ever) ")
        step1.pack(fill=tk.X, pady=(10, 4))
        ttk.Label(
            step1, foreground="#9b9b9b", wraplength=660, justify="left",
            text="Requires: (a) device plugged in via USB with ADB debugging "
                 "allowed; (b) Termux installed from F-Droid or GitHub "
                 "(f-droid.org/packages/com.termux or github.com/termux/"
                 "termux-app/releases) — NOT the Play Store build, which is "
                 "deprecated and ships with stale packages; (c) Termux has "
                 "been granted storage access on the device (open Termux once "
                 "and run  termux-setup-storage  — accept the prompt). Without "
                 "that, Termux can't read /sdcard/rk.sh.",
        ).pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Label(
            step1, foreground="#d9d9d9", wraplength=660, justify="left",
            text=("The button below pushes rotate-key.sh to /sdcard/rk.sh on the "
                  "device — that's the ONLY way the file gets there. If /sdcard/rk.sh "
                  "doesn't exist on a device, it means this button hasn't been "
                  "clicked for that device yet (or the file was deleted).\n\n"
                  "After the button succeeds, open Termux on the device once and "
                  "paste the following two commands — the first grants storage "
                  "access (approve the prompt), the second installs rotate-key:"),
        ).pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(
            step1, foreground=DARK["accent"], font=("Consolas", 10, "bold"),
            text="    termux-setup-storage\n    bash /sdcard/rk.sh install",
        ).pack(anchor="w", padx=8, pady=(0, 4))
        self.step1_btn = ttk.Button(
            step1, text="1. Push rotate-key script to connected device(s)",
            command=self._start_step1,
        )
        self.step1_btn.pack(fill=tk.X, padx=8, pady=(0, 8))
        Tooltip(
            self.step1_btn,
            "Pushes rotate-key.sh to /sdcard/rk.sh and the current SSH key to "
            "/sdcard/Download/id_ed25519 on every ADB-connected device. Safe to "
            "run repeatedly — idempotent. If /sdcard/rk.sh is missing on a "
            "device, running this button creates it.",
        )

        # --- Step 2 — rotate key (two sub-buttons: keygen, then push per device) ---
        step2 = ttk.LabelFrame(outer, text=" Step 2 — Rotate the SSH key, then push to each device ")
        step2.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(
            step2, foreground="#9b9b9b", wraplength=660, justify="left",
            text="Two sub-steps, because you usually only have one free USB port:\n"
                 "  2a. Click 'Generate new SSH key' ONCE per rotation. One UAC "
                 "prompt; replaces administrators_authorized_keys and issues a "
                 "10-min token.\n"
                 "  2b. Plug in a device, click 'Push current key', unplug, plug "
                 "in the next device, click again. Repeat for every device. The "
                 "push button is safe to click many times — it sends the "
                 "currently-staged key to whatever is connected right now.",
        ).pack(anchor="w", padx=8, pady=(6, 4))
        self.rotate_btn = ttk.Button(
            step2, text="2a. 🔑 Generate new SSH key (UAC swap)",
            command=self._start_rotation,
        )
        self.rotate_btn.pack(fill=tk.X, padx=8, pady=(0, 4))
        Tooltip(
            self.rotate_btn,
            "Keygen + UAC swap of authorized_keys + issue 10-min token. Does "
            "NOT push to any device — that's 2b. Running this while a session "
            "is already active will generate a NEW key and invalidate the old "
            "one; re-push to every device afterward.",
        )
        self.push_btn = ttk.Button(
            step2, text="2b. 📤 Push current key to connected device(s)",
            command=self._start_push,
        )
        self.push_btn.pack(fill=tk.X, padx=8, pady=(0, 8))
        Tooltip(
            self.push_btn,
            "Pushes the currently-staged private key to every connected ADB "
            "device. Click once per device as you cycle cables. Idempotent — "
            "re-pushing the same key to the same device is a no-op.",
        )

        # --- Step 3 — user-side instructions (can't be automated via ADB) ---
        step3 = ttk.LabelFrame(outer, text=" Step 3 — On each device, apply the rotation ")
        step3.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(
            step3, foreground="#9b9b9b", wraplength=660, justify="left",
            text="There's no PC-side button here on purpose — triggering rotate-key "
                 "from ADB requires Termux's RUN_COMMAND permission, which Android "
                 "won't grant to the shell user. So this step lives on the device.",
        ).pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Label(
            step3, foreground="#d9d9d9", wraplength=660, justify="left",
            text=("After Step 2 succeeds on the PC, on each device:\n"
                  "  1. Fully close Termux (swipe it out of Recents), then reopen "
                  "it — this picks up the freshly pushed key from /sdcard/Download.\n"
                  "  2. In Termux, run:"),
        ).pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(
            step3, foreground=DARK["accent"], font=("Consolas", 10, "bold"),
            text="    rotate-key",
        ).pack(anchor="w", padx=8, pady=(0, 2))
        ttk.Label(
            step3, foreground="#9b9b9b", wraplength=660, justify="left",
            text="For remote devices not on USB, use  rotate-key <token>  with the "
                 "token shown in the box below (valid 10 minutes).",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        # --- session / token row ---
        token = ttk.LabelFrame(outer, text=" Rotation session & remote token ")
        token.pack(fill=tk.X, pady=(10, 4))
        token_row = ttk.Frame(token)
        token_row.pack(fill=tk.X, padx=8, pady=6)
        self.token_var = tk.StringVar(value="(no active session)")
        self.token_entry = ttk.Entry(token_row, textvariable=self.token_var,
                                     state="readonly", width=40)
        self.token_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        Tooltip(
            self.token_entry,
            "The token remote devices (not connected via USB) use with "
            "`rotate-key <token>` to fetch the current key over Tailnet.",
        )
        self.copy_btn = ttk.Button(token_row, text="Copy", width=8,
                                   command=self._copy_token, state="disabled")
        self.copy_btn.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(self.copy_btn, "Copy the current token to the Windows clipboard.")
        self.stop_btn = ttk.Button(token_row, text="Stop session", width=14,
                                   command=self._stop_session, state="disabled")
        self.stop_btn.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(
            self.stop_btn,
            "End the active 10-min rotation session right now. Next Step 2 click "
            "will generate a fresh keypair and do a full rotation.",
        )
        self.countdown_var = tk.StringVar(value="")
        ttk.Label(token, textvariable=self.countdown_var,
                  foreground="#aaa").pack(anchor="w", padx=8, pady=(0, 6))

        # --- log ---
        log_frame = ttk.LabelFrame(outer, text=" Log ")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.log_text = tk.Text(
            log_frame, bg=DARK["surface"], fg=DARK["fg"],
            insertbackground=DARK["fg"], selectbackground=DARK["accent"],
            relief="flat", borderwidth=0, highlightthickness=0,
            wrap="word", font=("Consolas", 9),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.log_text.configure(state="disabled")

        # --- footer ---
        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(footer, text="Close", command=self.destroy, width=10).pack(side=tk.RIGHT)

    # ---- log helper ----

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, msg: str):
        self.status_var.set(msg)

    # ---- device detection ----

    def _refresh_devices_async(self):
        if self._is_busy:
            return
        threading.Thread(target=self._refresh_devices, daemon=True).start()

    def _refresh_devices(self):
        if not ADB_PATH.exists():
            self.after(0, lambda: self._log(f"adb not found at {ADB_PATH}"))
            return
        ok, out = _run([str(ADB_PATH), "devices"])
        devices = []
        for line in out.splitlines():
            if "\tdevice" in line:
                devices.append(line.split("\t", 1)[0].strip())
        self.after(0, self._show_devices, devices)

    def _show_devices(self, devices: list[str]):
        self.devices_box.delete(0, "end")
        if not devices:
            self.devices_box.insert("end", "  (none — plug a device in with USB debugging on)")
        else:
            for d in devices:
                self.devices_box.insert("end", f"  {d}")
        self._set_status(f"{len(devices)} device(s) detected")

    # ---- rotation flow ----

    def _start_rotation(self):
        if self._is_busy:
            return
        if not SWAP_PS1.exists():
            self._log(f"ERROR: missing helper {SWAP_PS1}")
            return
        self._set_buttons_busy(True)
        self._log("=== Starting rotation ===")
        threading.Thread(target=self._do_rotation, daemon=True).start()

    def _do_rotation(self):
        try:
            self._do_rotation_inner()
            self.after(0, lambda: self._set_status("Rotation complete."))
            self.after(0, lambda: self._log("=== Rotation complete ==="))
        except Exception as e:
            self.after(0, lambda m=str(e): self._log(f"ERROR: {m}"))
        finally:
            self.after(0, lambda: self._set_buttons_busy(False))

    def _set_buttons_busy(self, busy: bool):
        self._is_busy = busy
        state = "disabled" if busy else "normal"
        self.rotate_btn.configure(state=state)
        self.push_btn.configure(state=state)
        self.step1_btn.configure(state=state)
        # stop_btn enabled state is driven by session state, not busy state,
        # so leave it alone here (but disable it entirely while busy to prevent
        # mid-operation cancellation).
        if busy:
            self.stop_btn.configure(state="disabled")
        else:
            self.stop_btn.configure(
                state="normal" if self._is_session_active() else "disabled")

    def _is_session_active(self) -> bool:
        return bool(self._token) and self._token_expires_at > time.time()

    # ---- Step 1: push rotate-key.sh (+ current key) to connected devices ----

    def _start_step1(self):
        if self._is_busy:
            return
        if not ROTATE_KEY_SH.exists():
            self._log(f"ERROR: {ROTATE_KEY_SH} is missing")
            return
        self._set_buttons_busy(True)
        self._log("=== Step 1: pushing rotate-key script to connected devices ===")
        threading.Thread(target=self._do_step1, daemon=True).start()

    def _do_step1(self):
        try:
            if not ADB_PATH.exists():
                self.after(0, lambda: self._log(f"adb not found at {ADB_PATH}"))
                return
            _, listout = _run([str(ADB_PATH), "devices"])
            devices = [line.split("\t", 1)[0].strip()
                       for line in listout.splitlines() if "\tdevice" in line]
            if not devices:
                self.after(0, lambda: self._log(
                    "No ADB devices connected. Plug a device in via USB (with "
                    "USB debugging allowed) and click again."))
                return
            for dev in devices:
                self.after(0, lambda d=dev: self._log(f"--- {d} ---"))
                # Check Termux installed.
                _, pm = _run([str(ADB_PATH), "-s", dev, "shell",
                              "pm", "list", "packages", "com.termux"], timeout=5)
                if "package:com.termux" not in pm:
                    self.after(0, lambda d=dev: self._log(
                        f"  ✗ Termux NOT installed on {d}; install it first."))
                    continue
                # Push the script to both the short path and the self-update slot.
                ok1, _ = _run([str(ADB_PATH), "-s", dev, "push",
                               str(ROTATE_KEY_SH), "/sdcard/rk.sh"], timeout=10)
                ok2, _ = _run([str(ADB_PATH), "-s", dev, "push",
                               str(ROTATE_KEY_SH), "/sdcard/Download/rotate-key.sh"], timeout=10)
                # Also push the current key if we have one.
                key_pushed = False
                if SSH_KEY_PATH.exists():
                    ok3, _ = _run([str(ADB_PATH), "-s", dev, "push",
                                   str(SSH_KEY_PATH), "/sdcard/Download/id_ed25519"], timeout=10)
                    key_pushed = ok3
                if ok1 and ok2:
                    self.after(0, lambda d=dev, kp=key_pushed: self._log(
                        f"  ✓ {d}: script pushed"
                        f"{' + current key' if kp else ''}. "
                        f"Now in Termux on that device, paste:  bash /sdcard/rk.sh install"))
                else:
                    self.after(0, lambda d=dev: self._log(f"  ✗ {d}: push failed"))
        finally:
            self.after(0, lambda: self._set_buttons_busy(False))

    def _stop_session(self):
        """End the current rotation window early. Invalidates the token so the
        next Step 2 click starts fresh (keygen + UAC)."""
        self._token = None
        self._token_expires_at = 0
        self.token_var.set("(no active session)")
        self.copy_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")
        self.countdown_var.set("")
        # Also wipe tokens on disk so server can't honor any leftover values.
        try:
            TOKENS_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        self._log("Rotation session ended — next Step 2 click will generate a new key.")
        self._set_status("Session ended.")

    def _do_rotation_inner(self):
        """Keygen + UAC swap of authorized_keys + token + push new key to connected."""
        # 1. Generate new ed25519 keypair.
        self.after(0, lambda: self._set_status("Generating new keypair..."))
        if SSH_PUB_PATH.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            SSH_PUB_PATH.rename(SSH_PUB_PATH.with_name(
                SSH_PUB_PATH.name + f".rotated-{stamp}"))
        for p in (SSH_KEY_PATH, SSH_PUB_PATH):
            try: p.unlink(missing_ok=True)
            except Exception: pass
        comment = f"stt-app-rotated-{time.strftime('%Y-%m-%d')}"
        ok, out = _run([
            "ssh-keygen", "-t", "ed25519", "-f", str(SSH_KEY_PATH),
            "-N", "", "-C", comment, "-q",
        ], timeout=15)
        if not ok or not SSH_KEY_PATH.exists():
            self.after(0, lambda m=out.strip(): self._log(f"ssh-keygen failed: {m}"))
            return
        pub = SSH_PUB_PATH.read_text(encoding="utf-8").strip()
        self.after(0, lambda p=pub: self._log(f"generated: {p}"))

        # 2. Elevated swap — UAC prompt.
        self.after(0, lambda: self._set_status("Waiting for UAC... click Yes."))
        self.after(0, lambda: self._log("triggering UAC for authorized_keys swap..."))
        ps_cmd = (
            f"Start-Process powershell -Wait -Verb RunAs -ArgumentList "
            f"@('-NoProfile','-ExecutionPolicy','Bypass','-File',"
            f"'{SWAP_PS1}','-PubKeyPath','{SSH_PUB_PATH}')"
        )
        ok, out = _run(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=120)
        if not ok:
            self.after(0, lambda m=out.strip(): self._log(f"elevated swap failed: {m}"))
            return
        self.after(0, lambda: self._log("authorized_keys swap complete"))

        # 3. Issue a rotation token.
        import secrets
        token = secrets.token_urlsafe(24).replace("_", "").replace("-", "")[:32]
        expires = int(time.time()) + 600
        existing = []
        if TOKENS_PATH.exists():
            try:
                raw = TOKENS_PATH.read_text(encoding="utf-8").strip()
                if raw:
                    data = json.loads(raw)
                    if isinstance(data, list):
                        existing = [t for t in data if t.get("expires_at", 0) > time.time()]
            except Exception:
                existing = []
        existing.append({"token": token, "expires_at": expires,
                         "issued": time.strftime("%Y-%m-%dT%H:%M:%S")})
        TOKENS_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        self._token = token
        self._token_expires_at = expires
        self.after(0, lambda t=token: (self.token_var.set(t),
                                       self.copy_btn.configure(state="normal"),
                                       self._log(f"token issued: {t} (10 min)")))

        self.after(0, lambda: self._log(
            "=== Keygen complete. Now click 2b for each device you want to update. ==="))

    def _start_push(self):
        """Step 2b: push the currently-staged private key to every connected
        ADB device. Safe to click once per device as USB cables are swapped."""
        if self._is_busy:
            return
        if not SSH_KEY_PATH.exists():
            self._log(f"ERROR: no staged private key at {SSH_KEY_PATH}. "
                      f"Click 2a (generate) first.")
            return
        self._set_buttons_busy(True)
        self._log("=== Step 2b: pushing current key to connected devices ===")
        threading.Thread(target=self._do_push, daemon=True).start()

    def _do_push(self):
        try:
            self._push_to_connected()
        finally:
            self.after(0, lambda: self._set_buttons_busy(False))

    def _push_to_connected(self):
        if not ADB_PATH.exists():
            self.after(0, lambda: self._log(f"adb not found at {ADB_PATH} — skipping push"))
            return
        ok, out = _run([str(ADB_PATH), "devices"])
        devices = [line.split("\t", 1)[0].strip()
                   for line in out.splitlines() if "\tdevice" in line]
        if not devices:
            self.after(0, lambda: self._log(
                "no connected devices to push to — plug one in and click 2b again"))
            return
        for dev in devices:
            self.after(0, lambda d=dev: self._log(f"pushing to {d}..."))
            ok, out = _run([str(ADB_PATH), "-s", dev, "push",
                           str(SSH_KEY_PATH), "/sdcard/Download/id_ed25519"], timeout=30)
            if ok:
                self.after(0, lambda d=dev: self._log(
                    f"  ✓ {d} — in Termux run: rotate-key"))
            else:
                self.after(0, lambda d=dev, m=out.strip(): self._log(
                    f"  ✗ {d} — {m}"))
        self.after(0, self._refresh_devices_async)

    # ---- token utilities ----

    def _copy_token(self):
        if not self._token:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self._token)
            self._set_status("Token copied to clipboard.")
            self._log("token copied to clipboard")
        except Exception as e:
            self._log(f"clipboard error: {e}")

    def _tick(self):
        if self._token and self._token_expires_at:
            remaining = int(self._token_expires_at - time.time())
            if remaining > 0:
                m, s = divmod(remaining, 60)
                self.countdown_var.set(
                    f"Session active — token expires in {m}:{s:02d}. "
                    f"Step 2 re-clicks within this window reuse this key (no new keygen).")
                if not self._is_busy:
                    self.stop_btn.configure(state="normal")
            else:
                self.countdown_var.set("Session ended (token expired).")
                self._token = None
                self.token_var.set("(no active session)")
                self.copy_btn.configure(state="disabled")
                self.stop_btn.configure(state="disabled")
                try: TOKENS_PATH.unlink(missing_ok=True)
                except Exception: pass
        self.after(1000, self._tick)


DIAG_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗", "info": "ℹ"}
DIAG_COLOR = {
    "ok":   "#7ee787",   # green
    "warn": "#f9c74f",   # amber
    "fail": DARK["danger"],
    "info": DARK["fg_mute"],
}


class DiagnosticsDialog(tk.Toplevel):
    """Self-check window. Runs every prerequisite check in a background thread
    and reports ✓/⚠/✗ with a one-line fix hint."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.title("Self-Diagnose")
        self.geometry("780x620")
        self.minsize(620, 480)
        self.configure(bg=DARK["bg"])
        apply_dark_title_bar(self)
        self._is_running = False
        self._build_ui()
        self.after(100, self._run_all)

    def _build_ui(self):
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill=tk.BOTH, expand=True)
        ttk.Label(outer, text="Self-Diagnose",
                  font=("TkDefaultFont", 13, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text=("Checks every prerequisite for Claude Sessions on this PC. "
                  "Green = ok, amber = warning (works but degraded), red = "
                  "blocking. Each failure includes a one-line fix hint."),
            wraplength=720, justify="left", foreground="#aaa",
        ).pack(anchor="w", pady=(4, 8))

        self.summary_var = tk.StringVar(value="Running checks…")
        ttk.Label(outer, textvariable=self.summary_var,
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w", pady=(0, 6))

        results_frame = ttk.Frame(outer)
        results_frame.pack(fill=tk.BOTH, expand=True)
        self.results = tk.Text(
            results_frame, bg=DARK["surface"], fg=DARK["fg"],
            insertbackground=DARK["fg"], selectbackground=DARK["accent"],
            relief="flat", borderwidth=0, highlightthickness=0,
            wrap="word", font=("Consolas", 10),
        )
        scroll = ttk.Scrollbar(results_frame, orient="vertical",
                               command=self.results.yview)
        self.results.configure(yscrollcommand=scroll.set)
        self.results.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for level, color in DIAG_COLOR.items():
            self.results.tag_configure(level, foreground=color,
                                       font=("Consolas", 10, "bold"))
        self.results.tag_configure("name", foreground=DARK["fg"],
                                   font=("Consolas", 10, "bold"))
        self.results.tag_configure("detail", foreground=DARK["fg_mute"])
        self.results.tag_configure("fix", foreground=DARK["accent"])
        self.results.configure(state="disabled")

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X, pady=(10, 0))
        self.recheck_btn = ttk.Button(footer, text="Re-check",
                                      command=self._run_all, width=12)
        self.recheck_btn.pack(side=tk.LEFT)
        Tooltip(self.recheck_btn, "Run every check again from scratch.")
        self.copy_btn = ttk.Button(footer, text="Copy log",
                                   command=self._copy_log, width=12)
        self.copy_btn.pack(side=tk.LEFT, padx=(6, 0))
        Tooltip(self.copy_btn, "Copy the full diagnostic output to the clipboard.")
        ttk.Button(footer, text="Close", command=self.destroy,
                   width=10).pack(side=tk.RIGHT)

    def _append(self, level: str, name: str, detail: str, fix: str | None = None):
        icon = DIAG_ICON.get(level, "•")
        self.results.configure(state="normal")
        self.results.insert("end", f"{icon}  ", level)
        self.results.insert("end", f"{name}\n", "name")
        if detail:
            self.results.insert("end", f"     {detail}\n", "detail")
        if fix:
            self.results.insert("end", f"     Fix: {fix}\n", "fix")
        self.results.insert("end", "\n")
        self.results.see("end")
        self.results.configure(state="disabled")

    def _clear(self):
        self.results.configure(state="normal")
        self.results.delete("1.0", "end")
        self.results.configure(state="disabled")

    def _copy_log(self):
        text = self.results.get("1.0", "end").strip()
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.summary_var.set(self.summary_var.get() + "  (log copied)")
        except Exception:
            pass

    def _run_all(self):
        if self._is_running:
            return
        self._is_running = True
        self.recheck_btn.configure(state="disabled")
        self.summary_var.set("Running checks…")
        self._clear()
        threading.Thread(target=self._run_all_inner, daemon=True).start()

    def _run_all_inner(self):
        levels: list[str] = []

        def add(level: str, name: str, detail: str, fix: str | None = None):
            levels.append(level)
            self.after(0, lambda: self._append(level, name, detail, fix))

        # 1. Python version
        v = sys.version_info
        if v >= (3, 10):
            add("ok", "Python ≥ 3.10",
                f"running {v.major}.{v.minor}.{v.micro}")
        else:
            add("fail", "Python ≥ 3.10",
                f"running {v.major}.{v.minor}.{v.micro}",
                "Install Python 3.10 or newer from python.org")

        # 2. sv-ttk
        if HAS_SV_TTK:
            add("ok", "sv-ttk installed", "dark theme available")
        else:
            add("warn", "sv-ttk installed",
                "GUI runs without it but won't be themed",
                "pip install sv-ttk")

        # 3. WSL + Ubuntu reachable (combined — fastest sanity check)
        ok, out = _run(["wsl", "-d", "Ubuntu", "--", "echo", "ok"], timeout=15)
        wsl_ok = ok and "ok" in out
        if wsl_ok:
            add("ok", "WSL + Ubuntu reachable",
                "wsl -d Ubuntu responds")
        else:
            add("fail", "WSL + Ubuntu reachable",
                (out.strip() or "no output")[:200],
                "wsl --install -d Ubuntu  (in elevated PowerShell)")

        # 4. tmux in WSL
        if wsl_ok:
            ok, out = _run(["wsl", "-d", "Ubuntu", "--", "which", "tmux"],
                           timeout=10)
            if ok and out.strip():
                add("ok", "tmux installed in Ubuntu", out.strip())
            else:
                add("fail", "tmux installed in Ubuntu", "not found",
                    "wsl -d Ubuntu -- sudo apt install -y tmux")
        else:
            add("info", "tmux installed in Ubuntu",
                "skipped — WSL/Ubuntu not reachable")

        # 5. claude CLI in WSL
        if wsl_ok:
            ok, out = _run(["wsl", "-d", "Ubuntu", "--", "which", "claude"],
                           timeout=10)
            if ok and out.strip():
                add("ok", "claude CLI installed in Ubuntu", out.strip())
            else:
                add("fail", "claude CLI installed in Ubuntu", "not found",
                    "wsl -d Ubuntu -- sudo npm install -g @anthropic-ai/claude-code")
        else:
            add("info", "claude CLI installed in Ubuntu",
                "skipped — WSL/Ubuntu not reachable")

        # 6. klaud function
        if wsl_ok:
            ok, out = _run(
                ["wsl", "-d", "Ubuntu", "--", "bash", "-ic", "type klaud"],
                timeout=10,
            )
            if ok and ("function" in out or "klaud is" in out):
                add("ok", "klaud function defined in WSL",
                    "found in interactive shell")
            else:
                add("warn", "klaud function defined in WSL",
                    "not found in ~/.bashrc",
                    "Add the klaud() function — see README §Setup Part A step 3")
        else:
            add("info", "klaud function defined in WSL",
                "skipped — WSL/Ubuntu not reachable")

        # 7. WRAPPER_DIR exists
        if WRAPPER_DIR.exists():
            add("ok", "Wrapper directory exists", str(WRAPPER_DIR))
        else:
            add("warn", "Wrapper directory exists",
                f"{WRAPPER_DIR} missing",
                "Will be created on first Save — but make sure it's on PATH")

        # 8. WRAPPER_DIR on PATH
        path_env = os.environ.get("PATH", "")
        on_path = False
        try:
            wrapper_resolved = WRAPPER_DIR.resolve()
            for p in path_env.split(";"):
                p = p.strip()
                if not p:
                    continue
                try:
                    if Path(p).resolve() == wrapper_resolved:
                        on_path = True
                        break
                except Exception:
                    continue
        except Exception:
            pass
        if on_path:
            add("ok", "Wrapper directory on PATH",
                "sesN commands will resolve in any terminal")
        else:
            add("fail", "Wrapper directory on PATH",
                f"{WRAPPER_DIR} not in PATH — typing ses1 won't work",
                "Add it via System Properties → Environment Variables, "
                "then reopen terminals")

        # 9. sessions.json valid
        cfg: dict = {}
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                add("ok", "sessions.json valid",
                    f"{len(cfg)} session(s) configured")
            except Exception as e:
                add("fail", "sessions.json valid", f"parse error: {e}",
                    "Delete sessions.json and re-save from the GUI")
        else:
            add("info", "sessions.json",
                "not yet — will be created on first Save")

        # 10. Per-session folder + wrapper
        for name in sorted(cfg.keys(), key=_ses_num):
            info = cfg[name]
            folder = (info.get("folder") or "").strip()
            wrapper = WRAPPER_DIR / f"{name}.cmd"
            issues = []
            if not folder:
                issues.append("no folder set")
            elif not Path(folder).exists():
                issues.append(f"folder missing: {folder}")
            if not wrapper.exists():
                issues.append(f"wrapper missing: {wrapper.name}")
            if issues:
                add("warn", f"Session {name}", "; ".join(issues),
                    "Open the row, fix the folder, click Save")
            else:
                add("ok", f"Session {name}", folder)

        # 11. OpenSSH Server
        ok, out = _run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Service sshd -ErrorAction SilentlyContinue).Status"],
            timeout=10,
        )
        status = (out or "").strip()
        if "Running" in status:
            add("ok", "OpenSSH Server running",
                "phones can SSH in to this PC")
        elif status:
            add("warn", "OpenSSH Server running",
                f"service status: {status}",
                "Start-Service sshd  (in elevated PowerShell)")
        else:
            add("warn", "OpenSSH Server installed",
                "service not found",
                "Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0")

        # 12. authorized_keys (admin or user)
        admin_keys = Path(r"C:\ProgramData\ssh\administrators_authorized_keys")
        user_keys = Path.home() / ".ssh" / "authorized_keys"
        try:
            if admin_keys.exists():
                try:
                    raw = admin_keys.read_text(encoding="utf-8").strip()
                    keys = [l for l in raw.splitlines()
                            if l.strip() and not l.startswith("#")]
                    if keys:
                        add("ok", "Admin authorized_keys configured",
                            f"{len(keys)} key(s) in {admin_keys.name}")
                    else:
                        add("warn", "Admin authorized_keys configured",
                            "file exists but is empty",
                            "See README §Part C step 6 to add a phone's pubkey")
                except PermissionError:
                    add("info", "Admin authorized_keys",
                        "exists but unreadable (normal — admin file)")
            elif user_keys.exists():
                add("info", "User authorized_keys present", str(user_keys))
            else:
                add("warn", "authorized_keys configured",
                    "neither admin nor user keys file found",
                    "Add your phone's pubkey — README §Part C step 6")
        except Exception as e:
            add("info", "authorized_keys", f"check skipped: {e}")

        # 13. Tailscale (info)
        ok, out = _run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Service Tailscale -ErrorAction SilentlyContinue).Status"],
            timeout=8,
        )
        ts_status = (out or "").strip()
        if "Running" in ts_status:
            add("info", "Tailscale running",
                "available for remote phone access")
        else:
            add("info", "Tailscale",
                "service not detected (optional — only needed for remote access)")

        # 14. ADB (info — only matters for rotation panel)
        if ADB_PATH.exists():
            add("info", "ADB found", str(ADB_PATH))
        else:
            add("info", "ADB",
                f"not at {ADB_PATH} (optional — only needed for SSH key rotation)")

        # Summary
        n_fail = sum(1 for r in levels if r == "fail")
        n_warn = sum(1 for r in levels if r == "warn")
        n_ok = sum(1 for r in levels if r == "ok")
        if n_fail:
            summary = f"✗  {n_fail} failed, {n_warn} warning(s), {n_ok} ok"
        elif n_warn:
            summary = f"⚠  {n_warn} warning(s), {n_ok} ok"
        else:
            summary = f"✓  All {n_ok} checks passed"
        self.after(0, lambda: self.summary_var.set(summary))
        self.after(0, lambda: self.recheck_btn.configure(state="normal"))
        self._is_running = False


if __name__ == "__main__":
    SessionsApp().mainloop()
