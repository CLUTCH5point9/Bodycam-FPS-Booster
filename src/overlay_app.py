"""Bodycam FPS Booster -- a separate desktop window (NOT injected into the game
process) holding the Performance & Rendering tools and nothing else.

Unlike the full overlay this one is an ordinary program: it opens when you run
it, there is no Insert hotkey and no tray icon, and closing the window closes
the program.

Run with:  python src/overlay_app.py
Requires the game to be running with the ClaudeBridge UE4SS mod loaded.
"""
import logging
import logging.handlers
import os
import socket
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import perf_api as api
import install_bridge
import ui_theme as ui
from ui_theme import PANEL, FG, MUTED, GOOD, BAD, PAD, PAD_SM

from ui_common import AsyncRunner
from tab_performance import PerformanceTab

# A --windowed PyInstaller build has no console: print() output (and, in some
# builds, an unhandled exception's default stderr traceback) goes nowhere.
# Log to a capped file instead so a crash/error is diagnosable after the fact.
_log_handler = logging.handlers.RotatingFileHandler(
    os.path.join(api._CONFIG_DIR, "overlay.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8")
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(_log_handler)
logging.getLogger().setLevel(logging.INFO)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Bodycam FPS Booster")
        self.root.geometry("1280x820")
        self.root.minsize(900, 650)
        self.root.attributes("-topmost", True)
        # Windowed maximized (user request 2026-09-13): a normal window with a
        # title bar, filling the work area. "zoomed" is the Windows maximized
        # state; the 1280x820 geometry above is what Restore drops back to.
        self._maximize()
        # An ordinary program: the X closes it for good, no tray, no hotkey.
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.root.report_callback_exception = self._log_tk_exception

        ui.apply(self.root)

        self.runner = AsyncRunner(self.root)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=PAD_SM, pady=(PAD_SM, 0))
        self.notebook = nb
        def add_tab(attr, factory, title):
            tab = factory(nb, self)
            setattr(self, attr, tab)
            nb.add(tab, text=title)

        add_tab("performance_tab", PerformanceTab, "Performance & Rendering")

        # Bottom status bar: one row, status text (left, expands) + a small
        # connection dot + label (right) -- replaces two separately-packed
        # full-width labels with a single denser bar.
        bottom = ui.frame(self.root, panel=True)
        # Reserve footer space before the expanding notebook; otherwise tall
        # tabs consume it and command results disappear below the window.
        bottom.pack(fill="x", side="bottom", before=nb)
        ttk.Separator(bottom, orient="horizontal").pack(fill="x", side="top")

        self.status_var = tk.StringVar(value="Starting...")
        self.status_lbl = ui.label(bottom, textvariable=self.status_var, bg=PANEL, anchor="w")
        self.status_lbl.pack(fill="x", side="left", expand=True, padx=(PAD, PAD_SM), pady=PAD_SM + 1)

        conn_frame = ui.frame(bottom, panel=True)
        conn_frame.pack(side="right", padx=PAD, pady=PAD_SM, before=self.status_lbl)
        self.conn_dot = tk.Canvas(conn_frame, width=10, height=10, bg=PANEL, highlightthickness=0)
        self.conn_dot.pack(side="left", padx=(0, 6))
        self._conn_dot_id = self.conn_dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.conn_var = tk.StringVar(value="checking...")
        ui.label(conn_frame, textvariable=self.conn_var, bg=PANEL, muted=True).pack(side="left")

        self._run_setup_check()
        self.status("Ready.")

    def quit_app(self):
        """Closes the program for good: this build has no tray to hide in."""
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)

    def _run_setup_check(self):
        """Runs once at startup: makes sure ClaudeBridge (and, if it's already
        on this machine, the bundled UE4SS copy) is in place before the first
        connection poll. See install_bridge.py / docs/DOCUMENTATION.md §5.4."""
        def prompt_for_path():
            messagebox.showinfo(
                "Bodycam not found",
                "Couldn't auto-find your Bodycam install. Pick its Binaries\\Win64 folder next.")
            path = filedialog.askdirectory(title="Select Bodycam's Binaries\\Win64 folder")
            return path or None

        def work():
            return install_bridge.ensure_setup(prompt_for_path=prompt_for_path, on_status=self.status)

        def done(result):
            if result["reason"] == "needs_ue4ss":
                messagebox.showwarning(
                    "UE4SS not installed",
                    "UE4SS isn't installed in your Bodycam folder, and no bundled copy was "
                    "available to deploy. I've opened the official release page -- install it, "
                    "then restart this app.")
                self.status("Waiting on UE4SS install.", bad=True)
            elif result["reason"] == "not_found":
                self.status("Couldn't locate your Bodycam install.", bad=True)
            elif result["reason"] == "reinstalled_ue4ss":
                # The documented repair: the user deleted the game's ue4ss
                # folder, so the bundled copy went back in on this launch.
                messagebox.showinfo(
                    "UE4SS reinstalled",
                    "The UE4SS folder was missing from your Bodycam install, so I put the bundled copy and "
                    "the ClaudeBridge mod back.\n\nStart Bodycam now and wait for CONNECTED in the status bar.")
                self.status("Reinstalled UE4SS and ClaudeBridge. Start Bodycam.")
            elif result["reason"] == "installed_ue4ss_and_bridge":
                messagebox.showinfo(
                    "Setup complete",
                    "Deployed UE4SS + ClaudeBridge. Fully restart Bodycam (not just Ctrl+R) "
                    "for it to load.")
                self.status("UE4SS + ClaudeBridge installed -- restart Bodycam.")
            elif result["reason"] == "installed_bridge":
                self.status("ClaudeBridge installed on top of existing UE4SS -- restart Bodycam if it's running.")
            elif result["reason"] == "updated_bridge":
                messagebox.showinfo(
                    "ClaudeBridge updated",
                    "This version of the overlay ships a newer ClaudeBridge mod, and it has been put into "
                    "your Bodycam install.\n\nFully restart Bodycam (if it is running) for it to load.")
                self.status("ClaudeBridge updated -- restart Bodycam if it's running.")
            self._poll_connection()

        def err(e):
            self.status(f"Setup check failed: {e}", bad=True)
            self._poll_connection()

        self.runner.run(work, done, err)

    def status(self, text, bad=False):
        self.status_var.set(text)
        self.status_lbl.configure(fg=BAD if bad else FG)

    def on_error(self, prefix="ERROR"):
        """Shorthand for the common AsyncRunner error handler: show the
        exception in the status bar with a prefix, e.g. as the third argument
        to self.runner.run(work, done, ...)."""
        return lambda e: self.status(f"{prefix}: {e}", bad=True)

    def _log_tk_exception(self, exc_type, exc_value, tb):
        """Replaces Tkinter's default callback-exception handler (which prints
        to stderr -- invisible in a --windowed build) so an exception raised
        directly in a widget command/binding still ends up in overlay.log."""
        logging.error("Unhandled Tk callback exception", exc_info=(exc_type, exc_value, tb))

    def _poll_connection(self):
        def work():
            return api.is_connected()

        def done(ok):
            self.conn_var.set("CONNECTED" if ok else "not responding")
            self.conn_dot.itemconfigure(self._conn_dot_id, fill=GOOD if ok else BAD)
            self.root.after(5000, self._poll_connection)

        self.runner.run(work, done, lambda e: self.root.after(5000, self._poll_connection))

    def toggle(self):
        self.root.after(0, self._toggle_ui)

    def _maximize(self):
        try:
            self.root.state("zoomed")
        except tk.TclError:  # not Windows: fall back to the plain geometry
            pass

    def _toggle_ui(self):
        if self.root.state() == "withdrawn":
            self.root.deiconify()
            self._maximize()     # withdraw/deiconify drops the maximized state
            self.root.lift()
            self.root.focus_force()
        else:
            self.root.withdraw()

    def hide(self):
        self.root.withdraw()

    def run(self):
        self.root.mainloop()


# Arbitrary fixed local port used purely as a single-instance lock -- binding
# it is the mutex. See docs/DOCUMENTATION.md §5.6 for what breaks without it.
_SINGLE_INSTANCE_PORT = 47821


def _acquire_single_instance_lock():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s  # keep this alive for the process lifetime -- closing it releases the lock
    except OSError:
        s.close()
        return None


if __name__ == "__main__":
    _lock_socket = _acquire_single_instance_lock()
    if _lock_socket is None:
        import tkinter.messagebox as _mb
        _root = tk.Tk()
        _root.withdraw()
        _mb.showwarning(
            "Already running",
            "Bodycam Overlay is already running (check your system tray / taskbar, "
            "or press Insert). This copy will now close instead of opening a second, "
            "conflicting instance.")
        sys.exit(0)
    App().run()
