"""Central red/black/white theme for overlay_app.py -- palette, fonts,
spacing, the ttk style setup, and small widget factory functions.

Why this exists: before this module, every widget call in overlay_app.py
repeated its own bg=/fg=/font= literals -- correct, but it meant the app's
look was scattered across ~35 call sites, and the ttk widgets (Notebook,
Combobox, Separator, Frame, Treeview) had no style configuration at all
beyond `style.theme_use("clam")`, so they rendered in clam's default light
grey right next to hand-colored dark tk widgets. This module fixes both:
one palette to tweak, and one apply() call that actually themes every ttk
widget the app uses. The button()/label()/etc. factories below are what call
sites use instead of raw tk.Button(...bg=...fg=...).

Buttons deliberately do NOT hover-animate (no bg swap on mouse-enter) -- the
only feedback is Tk's own built-in press state (activebackground, which only
kicks in while the mouse button is actually held down), plus a hand cursor
for affordance. That's standard, instant, and non-distracting rather than a
custom animated hover effect.
"""
import tkinter as tk
from tkinter import ttk

# --------------------------------------------------------------------------- palette (red / black / white)
BG = "#121214"          # window / tab background (near-black)
PANEL = "#1a1a1d"       # slightly-raised panel/card background
INPUT = "#232326"       # entry/listbox/text/spinbox/treeview fields
INPUT_FOCUS = "#2b2b2f"
BORDER = "#3a3a3f"
CONSOLE_BG = "#0b0b0d"  # code/output areas -- darker than INPUT for contrast

FG = "#f5f5f5"          # primary text -- white
MUTED = "#9c9ca1"       # secondary text -- neutral grey (no color tint)

RED = "#c8102e"         # the one accent color: primary actions, selection, focus
RED_HOVER = "#d81b3a"
RED_PRESSED = "#a10d25"
RED_MUTED = "#7a2530"   # for a quiet destructive outline, not a full-saturation fill

# Back-compat names used around the app for the two semantic button tiers --
# both map onto the same red family now that the palette is strictly
# red/black/white (no separate green "success" hue).
ACCENT = RED
ACCENT_HOVER = RED_HOVER
GOOD = RED
GOOD_HOVER = RED_HOVER
BAD = RED
BAD_HOVER = RED_HOVER

# --------------------------------------------------------------------------- fonts
FONT_BASE = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_HEADER = ("Segoe UI", 13, "bold")
FONT_MUTED = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)

# --------------------------------------------------------------------------- spacing
PAD_SM = 4
PAD = 8
PAD_LG = 16

# (bg, active_bg, fg) per button kind. "active_bg" only shows while the
# button is physically pressed down -- see module docstring.
_BUTTON_COLORS = {
    "default": (INPUT, BORDER, FG),
    "accent": (RED, RED_HOVER, FG),
    "good": (RED, RED_HOVER, FG),
}


def apply(root):
    """Call once, right after creating the Tk root. Sets the window
    background and configures every ttk style the app touches -- without
    this, ttk.Notebook/Combobox/Separator/Frame/Treeview fall back to clam's
    default light palette regardless of how the rest of the window is colored."""
    root.configure(bg=BG)

    # ttk.Combobox's dropdown is a plain Tk Listbox under the hood, not
    # themeable via ttk.Style -- these *option_add calls are the only way to
    # color it (otherwise it's a jarring white popup on an otherwise dark app).
    root.option_add("*TCombobox*Listbox.background", INPUT)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", RED)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.font", FONT_BASE)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=FG, font=FONT_BASE)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BG)

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(8, 8, 8, 0))
    style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED,
                    padding=(14, 8), borderwidth=0, font=FONT_BASE)
    style.map(
        "TNotebook.Tab",
        background=[("selected", RED), ("active", BORDER)],
        foreground=[("selected", FG), ("active", FG)],
    )

    style.configure(
        "TCombobox", fieldbackground=INPUT, background=INPUT, foreground=FG,
        arrowcolor=MUTED, bordercolor=BORDER, lightcolor=INPUT, darkcolor=INPUT,
        borderwidth=1, padding=4, relief="flat",
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", INPUT), ("focus", INPUT_FOCUS)],
        foreground=[("disabled", MUTED)],
        bordercolor=[("focus", RED)],
    )

    style.configure(
        "Vertical.TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BG,
        arrowcolor=MUTED, relief="flat", width=12,
    )
    style.map("Vertical.TScrollbar", background=[("active", RED)])

    # Treeview -- used by the Host tab's categorized map list.
    style.configure(
        "Treeview", background=INPUT, fieldbackground=INPUT, foreground=FG,
        borderwidth=0, rowheight=24, font=FONT_BASE,
    )
    style.map(
        "Treeview",
        background=[("selected", RED)],
        foreground=[("selected", FG)],
    )
    # Category rows use this tag (bold, muted-until-selected) to read as
    # section headers rather than pickable items -- see HostTab._populate_map_tree.
    style.configure("Treeview.Heading", background=PANEL, foreground=FG, borderwidth=0)


def button(parent, text, kind="default", command=None, fg=None, width=None, outline=False, **kw):
    """A flat tk.Button, no hover animation (see module docstring). `kind`
    picks the fill: 'default' (neutral dark) or 'accent'/'good' (solid red,
    for the primary action on a screen). Pass outline=True for a destructive
    action instead (e.g. Delete/Remove) -- a quiet red-bordered button rather
    than a second competing solid color. Pass fg= to override just the text
    color."""
    if outline:
        bg, hover, base_fg = INPUT, INPUT_FOCUS, RED
        border_kw = dict(highlightthickness=1, highlightbackground=RED_MUTED, highlightcolor=RED)
    else:
        bg, hover, base_fg = _BUTTON_COLORS.get(kind, _BUTTON_COLORS["default"])
        border_kw = dict(highlightthickness=0)
    text_fg = fg or base_fg
    return tk.Button(
        parent, text=text, command=command, width=width,
        bg=bg, fg=text_fg, activebackground=hover, activeforeground=text_fg,
        disabledforeground=MUTED, relief="flat", bd=0, padx=12, pady=6,
        font=FONT_BASE, cursor="hand2", **border_kw, **kw,
    )


def label(parent, text="", muted=False, bold=False, header=False, **kw):
    font = kw.pop("font", None) or (FONT_HEADER if header else FONT_BOLD if bold else FONT_MUTED if muted else FONT_BASE)
    fg = kw.pop("fg", None) or (MUTED if muted else FG)
    bg = kw.pop("bg", BG)
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, **kw)


def frame(parent, panel=False, **kw):
    bg = kw.pop("bg", PANEL if panel else BG)
    return tk.Frame(parent, bg=bg, **kw)


def entry(parent, textvariable=None, width=None, **kw):
    return tk.Entry(
        parent, textvariable=textvariable, width=width,
        bg=INPUT, fg=FG, insertbackground=FG, disabledbackground=BG,
        relief="flat", bd=6, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=RED, font=FONT_BASE, **kw,
    )


def spinbox(parent, textvariable=None, width=None, **kw):
    return tk.Spinbox(
        parent, textvariable=textvariable, width=width,
        bg=INPUT, fg=FG, buttonbackground=INPUT, insertbackground=FG,
        relief="flat", bd=6, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=RED, font=FONT_BASE, **kw,
    )


def checkbutton(parent, text="", variable=None, command=None, **kw):
    bg = kw.pop("bg", BG)
    return tk.Checkbutton(
        parent, text=text, variable=variable, command=command,
        bg=bg, fg=FG, selectcolor=INPUT, activebackground=bg, activeforeground=FG,
        highlightthickness=0, bd=0, cursor="hand2", font=FONT_BASE, **kw,
    )


def radiobutton(parent, text="", variable=None, value=None, command=None, **kw):
    return tk.Radiobutton(
        parent, text=text, variable=variable, value=value, command=command,
        bg=kw.pop("bg", BG), fg=FG, selectcolor=INPUT, activebackground=BG,
        activeforeground=FG, highlightthickness=0, bd=0, cursor="hand2", font=FONT_BASE, **kw,
    )


def listbox(parent, **kw):
    return tk.Listbox(
        parent, bg=INPUT, fg=FG, selectbackground=RED, selectforeground=FG,
        activestyle="none", relief="flat", bd=0, highlightthickness=1,
        highlightbackground=BORDER, highlightcolor=RED, font=FONT_BASE, **kw,
    )


def text(parent, mono=True, **kw):
    return tk.Text(
        parent, bg=kw.pop("bg", CONSOLE_BG), fg=FG, insertbackground=FG,
        relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER,
        highlightcolor=RED, font=FONT_MONO if mono else FONT_BASE, **kw,
    )


def scrollbar(parent, **kw):
    return ttk.Scrollbar(parent, style="Vertical.TScrollbar", **kw)


def treeview(parent, **kw):
    tv = ttk.Treeview(parent, style="Treeview", **kw)
    tv.tag_configure("category", foreground=MUTED, font=FONT_BOLD)
    return tv


def info_banner(parent, text, title=None):
    """A quiet callout box (panel background, thin red left edge) for a short
    bit of "here's what this tab does and what to watch out for" guidance --
    used at the top of the Console/Shell/Plugins tabs, which hand the user
    raw, unsandboxed power without much else explaining what that means.
    Text rewraps automatically as the tab is resized."""
    outer = tk.Frame(parent, bg=PANEL)
    tk.Frame(outer, bg=RED, width=3).pack(side="left", fill="y")
    body = tk.Frame(outer, bg=PANEL)
    body.pack(side="left", fill="both", expand=True, padx=(PAD, PAD), pady=(PAD_SM + 2, PAD_SM + 2))
    if title:
        tk.Label(body, text=title, bg=PANEL, fg=FG, font=FONT_BOLD, anchor="w", justify="left").pack(fill="x")
    msg = tk.Label(body, text=text, bg=PANEL, fg=MUTED, font=FONT_MUTED, anchor="w", justify="left")
    msg.pack(fill="x", pady=(2, 0) if title else 0)
    body.bind("<Configure>", lambda e: msg.configure(wraplength=max(200, e.width - 4)))
    return outer
