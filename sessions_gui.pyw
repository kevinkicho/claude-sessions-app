"""Tkinter GUI for editing sessions.json.

Dynamic session list — start with 3 rows by default. A minimal ✕ per row
removes that session; a single centered ＋ below the list adds one.

Each row: folder picker, auto-claude toggle, link-memory toggle, launch,
remove. Tooltips explain every field; a Help button opens a readme.

Per-session wrappers (ses1.cmd, ses2.cmd, ...) are created in
C:\\Users\\kevin\\.local\\bin automatically whenever the GUI saves.
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
WRAPPER_DIR = Path(r"C:\Users\kevin\.local\bin")

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

On each Android device in Termux, run this once:

  for i in $(seq 1 50); do
    grep -q "^alias ses$i=" ~/.bashrc ||
    echo "alias ses$i='ssh -t kevin@100.125.88.85 ses$i'" >> ~/.bashrc
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
            text="Requires: the device plugged in via USB with ADB debugging allowed. "
                 "Pushes rotate-key.sh + the current private key. Then open Termux on "
                 "the device and paste this once — it enables headless rotations "
                 "forever after:",
        ).pack(anchor="w", padx=8, pady=(6, 2))
        ttk.Label(
            step1, foreground=DARK["accent"], font=("Consolas", 10, "bold"),
            text="    bash /sdcard/rk.sh install",
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
            "run repeatedly — idempotent.",
        )

        # --- Step 2 — rotate key ---
        step2 = ttk.LabelFrame(outer, text=" Step 2 — Rotate the SSH key ")
        step2.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(
            step2, foreground="#9b9b9b", wraplength=660, justify="left",
            text="Requires: one UAC prompt (accept to replace administrators_authorized_keys). "
                 "Generates a fresh ed25519 keypair, replaces the public key on Windows, "
                 "issues a 10-min token, and pushes the new private key to every connected "
                 "device. Click again within 10 minutes to reuse the current key instead "
                 "of generating another (useful when cycling through devices).",
        ).pack(anchor="w", padx=8, pady=(6, 4))
        self.rotate_btn = ttk.Button(
            step2, text="2. 🔑 Rotate SSH key (UAC swap + push to connected)",
            command=self._start_rotation,
        )
        self.rotate_btn.pack(fill=tk.X, padx=8, pady=(0, 8))
        Tooltip(
            self.rotate_btn,
            "Full rotation: keygen + UAC swap of authorized_keys + token + push "
            "new private key to connected ADB devices. Does NOT run rotate-key "
            "on the devices themselves — that's Step 3.",
        )

        # --- Step 3 — dispatch rotate-key on devices ---
        step3 = ttk.LabelFrame(outer, text=" Step 3 — Apply rotation on each device ")
        step3.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(
            step3, foreground="#9b9b9b", wraplength=660, justify="left",
            text="Requires: Step 1 has been completed on this device at least once. "
                 "Fires rotate-key over Termux RUN_COMMAND — fully headless, Termux "
                 "stays closed. If the device hasn't completed Step 1 yet, this will "
                 "report that; finish Step 1 there first and try again.",
        ).pack(anchor="w", padx=8, pady=(6, 4))
        self.runkey_btn = ttk.Button(
            step3, text="3. ▶ Run rotate-key on connected devices",
            command=self._start_run_rotate,
        )
        self.runkey_btn.pack(fill=tk.X, padx=8, pady=(0, 8))
        Tooltip(
            self.runkey_btn,
            "Dispatches 'rotate-key' via Termux's RUN_COMMAND intent. Verifies "
            "success by watching for /sdcard/Download/id_ed25519 to disappear "
            "(rotate-key consumes it as part of the swap).",
        )

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

    def _start_run_rotate(self):
        """Fire rotate-key on each connected device via Termux's RUN_COMMAND
        intent. Headless; requires the device to have completed Step 1."""
        if self._is_busy:
            return
        self._set_buttons_busy(True)
        self._log("=== Step 3: running rotate-key on connected devices ===")
        threading.Thread(target=self._do_run_rotate, daemon=True).start()

    def _do_run_rotate(self):
        try:
            if not ADB_PATH.exists():
                self.after(0, lambda: self._log(f"adb not found at {ADB_PATH}"))
                return
            _, out = _run([str(ADB_PATH), "devices"])
            devices = [line.split("\t", 1)[0].strip()
                       for line in out.splitlines() if "\tdevice" in line]
            if not devices:
                self.after(0, lambda: self._log(
                    "No ADB devices connected; plug one in and click again."))
                return
            for dev in devices:
                self.after(0, lambda d=dev: self._log(f"--- {d} ---"))

                # Verify Termux is installed.
                _, pmout = _run([str(ADB_PATH), "-s", dev, "shell",
                                 "pm", "list", "packages", "com.termux"], timeout=5)
                if "package:com.termux" not in pmout:
                    self.after(0, lambda d=dev: self._log(
                        f"  ✗ {d}: Termux is not installed"))
                    continue

                # Fire the RUN_COMMAND intent.
                self.after(0, lambda d=dev: self._log(
                    f"  dispatching rotate-key via Termux RUN_COMMAND ..."))
                _run([
                    str(ADB_PATH), "-s", dev, "shell",
                    "am", "startservice", "--user", "0",
                    "-n", "com.termux/com.termux.app.RunCommandService",
                    "-a", "com.termux.RUN_COMMAND",
                    "--es", "com.termux.RUN_COMMAND_PATH",
                    "/data/data/com.termux/files/home/rotate-key.sh",
                    "--ez", "com.termux.RUN_COMMAND_BACKGROUND", "true",
                ], timeout=6)

                # Confirm by watching the pushed key file being consumed.
                headless_ok = False
                for _ in range(5):
                    time.sleep(1.0)
                    _, lsout = _run([str(ADB_PATH), "-s", dev, "shell",
                                     "ls", "/sdcard/Download/id_ed25519"], timeout=5)
                    if "No such file" in lsout or "does not exist" in lsout:
                        headless_ok = True
                        break

                if headless_ok:
                    self.after(0, lambda d=dev: self._log(
                        f"  ✓ {d}: rotate-key ran headlessly (key consumed, SSH up)"))
                    self._post_android_toast(dev, "SSH rotated",
                                             "rotate-key completed headlessly")
                else:
                    self.after(0, lambda d=dev: self._log(
                        f"  ⚠ {d}: dispatch didn't complete in 5s. "
                        f"Step 1 ('bash /sdcard/rk.sh install') probably hasn't "
                        f"been run on this device yet. Do Step 1 first, then "
                        f"retry Step 3."))
                    self._post_android_toast(
                        dev, "Rotation needs Step 1",
                        "Open Termux, run: bash /sdcard/rk.sh install",
                    )
        finally:
            self.after(0, lambda: self._set_buttons_busy(False))

    def _set_buttons_busy(self, busy: bool):
        self._is_busy = busy
        state = "disabled" if busy else "normal"
        self.rotate_btn.configure(state=state)
        self.runkey_btn.configure(state=state)
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

    def _post_android_toast(self, dev: str, title: str, msg: str):
        """Post a native Android notification on the device (visible even if
        Termux is closed). Uses adb's `cmd notification post`, which runs in
        the shell user context and doesn't require any Termux package."""
        try:
            _run([
                str(ADB_PATH), "-s", dev, "shell",
                "cmd", "notification", "post",
                "-S", "bigtext",
                "-t", title,
                "stt-rotate", msg,
            ], timeout=5)
        except Exception:
            pass

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

        # 4. Push to connected devices.
        self._push_to_connected()

    def _push_to_connected(self):
        if not ADB_PATH.exists():
            self.after(0, lambda: self._log(f"adb not found at {ADB_PATH} — skipping push"))
            return
        ok, out = _run([str(ADB_PATH), "devices"])
        devices = [line.split("\t", 1)[0].strip()
                   for line in out.splitlines() if "\tdevice" in line]
        if not devices:
            self.after(0, lambda: self._log("no connected devices to push to"))
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


if __name__ == "__main__":
    SessionsApp().mainloop()
