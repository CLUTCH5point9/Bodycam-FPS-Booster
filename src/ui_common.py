"""The two shared UI pieces this app uses: the background runner that keeps
the Tk loop responsive, and the scrollable panel.

Everything else that used to live here -- the console and shell mixin, the
saved-command widgets, the picker and save dialogs -- was removed with the
tabs that used them. None of it belongs in a performance tool.
"""
import logging
import queue
import threading
import tkinter as tk
import ui_theme as ui
from ui_theme import BG, PANEL, INPUT, FG, MUTED, ACCENT, GOOD, BAD, PAD, PAD_SM, PAD_LG
class AsyncRunner:
    """Runs blocking calls off the Tk main thread; delivers results back via after()."""

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self._poll()

    def run(self, fn, on_done=None, on_error=None):
        def worker():
            try:
                result = fn()
                self.q.put(("ok", result, on_done, on_error))
            except Exception as e:  # noqa: BLE001
                self.q.put(("err", e, on_done, on_error))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, payload, on_done, on_error = self.q.get_nowait()
                if kind == "ok" and on_done:
                    on_done(payload)
                elif kind == "err" and on_error:
                    on_error(payload)
                elif kind == "err":
                    logging.error("Unhandled async error", exc_info=payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)
def _make_scrollable(parent, panel=False):
    """Standard scrollable-frame pattern: a Canvas + inner Frame that grows
    with its contents and scrolls with a Scrollbar or the mouse wheel (bound
    only while the cursor is over this canvas, so it doesn't hijack scrolling
    on other tabs). Returns the inner frame to pack widgets into. Pass
    panel=True to use the PANEL background instead of BG (matches a
    panel=True parent frame, e.g. HostTab's right column)."""
    bg = PANEL if panel else BG
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0)
    vsb = ui.scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = ui.frame(canvas, panel=panel)
    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    def _wheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return inner
