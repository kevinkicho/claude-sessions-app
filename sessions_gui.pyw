"""Tkinter GUI for editing sessions.json.

Dynamic session list — start with 3 rows by default. A minimal ✕ per row
removes that session; a single centered ＋ below the list adds one.

Each row: folder picker, auto-claude toggle, link-memory toggle, launch,
remove. Tooltips explain every field; a Help button opens a readme.

Per-session wrappers (ses1.cmd, ses2.cmd, ...) are created in WRAPPER_DIR
(default: %USERPROFILE%\\.local\\bin) automatically whenever the GUI saves.
"""
import ctypes
import json
import os
import re
import subprocess
import sys
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
# Wrapper scripts (ses1.cmd, ses2.cmd, ...) go here so they're on PATH.
# Override with env var CLAUDE_SESSIONS_BIN if you want a different location.
WRAPPER_DIR = Path(os.environ.get("CLAUDE_SESSIONS_BIN", str(Path.home() / ".local" / "bin")))

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


if __name__ == "__main__":
    SessionsApp().mainloop()
