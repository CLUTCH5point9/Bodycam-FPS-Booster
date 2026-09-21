"""Performance & Rendering tab (2026-09-19): everything that changes what YOUR
game draws and how much CPU / memory it spends. Nothing here reaches other
players: rendering, animation rates, particle quality, texture pools and the
Windows process priority are per machine.

Sections:
  * LOD / draw distance -- the two-level geometry + texture keeper
    (perf_api.set_lod_system), moved here from Hosting Utilities.
  * Live settings -- console variables probed live before being listed
    (perf_api.PERF_SETTINGS), grouped GPU / CPU / Memory, with Read current,
    Max performance preset, Apply and Restore.
  * Memory -- run the garbage collector now.
  * Process priority -- the game High / Normal, the overlay Below normal.
  * Startup (Engine.ini) -- the RHI-thread settings the engine only reads at
    start, written under [SystemSettings] with a backup; restart required.
"""
import tkinter as tk
from tkinter import ttk, messagebox

import perf_api as api
import ui_theme as ui
from ui_theme import PAD, PAD_SM, PAD_LG
from ui_common import _make_scrollable


class PerformanceTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        body = self.body = _make_scrollable(self)

        ui.label(body, text="Performance & Rendering", header=True).pack(anchor="w", padx=PAD, pady=(PAD, PAD_SM))
        ui.label(body, text="Everything on this tab changes your own game only: what it draws, how much CPU it spends on "
                            "animation, particles and loading, and how much memory it holds. Measure FPS before and after; "
                            "Restore puts the game's own values back.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- Presets: fill both the live boxes and the LOD boxes, then apply both
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Presets", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        preset_row = ui.frame(body)
        preset_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        self.preset_buttons = {}
        for name, live, lod, blurb in api.PERF_PRESETS:
            b = ui.button(preset_row, name, kind="accent", command=lambda n=name: self._apply_preset(n))
            b.pack(side="left", padx=(0, PAD_SM))
            self.preset_buttons[name] = b
        ui.button(preset_row, "Game defaults", outline=True, command=self._apply_game_defaults).pack(side="left", padx=(PAD_LG, 0))
        ui.label(body, text="  |  ".join(f"{name}: {blurb}" for name, _l, _d, blurb in api.PERF_PRESETS)
                            + "  |  Game defaults: puts every live setting and the LOD system back to the game's own values. "
                            "A preset fills the boxes below and applies them right away; tweak any box and press Apply to refine it.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- Upscaling (DLSS through the plugin's Blueprint library; TSR otherwise)
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Upscaling", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        dlss_row = ui.frame(body)
        dlss_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        self.dlss_buttons = {}
        for mode, label, scale in api.DLSS_MODES:
            b = ui.button(dlss_row, label, kind="accent" if mode == 6 else "default", command=lambda m=mode: self._set_dlss(m))
            b.pack(side="left", padx=(0, PAD_SM))
            self.dlss_buttons[mode] = b
        dlss_row2 = ui.frame(body)
        dlss_row2.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.button(dlss_row2, "Read status", command=self._read_dlss).pack(side="left", padx=(0, PAD_SM))
        ui.button(dlss_row2, "Restore game's mode", outline=True, command=self._restore_dlss).pack(side="left", padx=(0, PAD_LG))
        self.dlss_rr_var = tk.IntVar(value=0)
        self.dlss_rr_check = ui.checkbutton(dlss_row2, text="Ray reconstruction (DLSS denoiser for Lumen)", variable=self.dlss_rr_var,
                                            command=self._toggle_dlss_rr)
        self.dlss_rr_check.pack(side="left", padx=(0, PAD_LG))
        self.dlss_status_lbl = ui.label(dlss_row2, text="DLSS: press Read status", muted=True)
        self.dlss_status_lbl.pack(side="left")
        ui.label(body, text="NVIDIA DLSS renders the frame smaller and rebuilds it at your screen's size: Performance renders a quarter "
                            "of the pixels (50% on each axis), Balanced 58%, Quality 67%, DLAA the full size with DLSS as the "
                            "anti-aliasing. The mode sets the render scale itself, so the Render scale % box below is ignored "
                            "while DLSS is on. Off hands upscaling to Unreal's TSR at the Render scale % you set, which is what "
                            "AMD and Intel cards use here (the game's FSR 3 switches do not take at runtime). Ray reconstruction "
                            "replaces Lumen's denoiser with DLSS's; try it only with hardware ray tracing on.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- AMD FSR: the same ladder as DLSS, for cards DLSS will not run on
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="AMD / Intel upscaling", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        fsr_row = ui.frame(body)
        fsr_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        self.fsr_buttons = {}
        for key, label, scale, _f, _x in api.AMD_MODES:
            b = ui.button(fsr_row, label, kind="accent" if key == "performance" else "default",
                          command=lambda k=key: self._set_amd(k))
            b.pack(side="left", padx=(0, PAD_SM))
            self.fsr_buttons[key] = b
        fsr_row2 = ui.frame(body)
        fsr_row2.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.button(fsr_row2, "Read FSR status", command=self._read_fsr).pack(side="left", padx=(0, PAD_LG))
        self.fsr_status_lbl = ui.label(fsr_row2, text="Upscaler: press Read FSR status", muted=True)
        self.fsr_status_lbl.pack(side="left")
        ui.label(body, text="The same ladder as DLSS, for cards DLSS will not run on: Performance renders a quarter of the pixels "
                            "(50% on each axis), Balanced 59%, Quality 67%. These buttons try AMD FSR first and fall back to Intel "
                            "XeSS, which runs on AMD cards too, and the status line says which one actually engaged. Bodycam ships "
                            "the FSR plugin but never loads it, so on every machine tested FSR refuses to switch on and XeSS is what "
                            "does the work.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- the line between "press a preset" and "expert boxes"
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Do not touch the numbers below manually unless you know what you're doing.", bold=True).pack(anchor="w", padx=PAD, pady=(0, 2))
        ui.label(body, text="The presets above set every box on this tab to values that were measured on a real match. The boxes below are the "
                            "raw engine settings behind them: a single wrong value can halve your frame rate, blank out textures or "
                            "hide scenery. If you experiment, change one thing at a time, Measure FPS, and use Game defaults to get back.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # Everything past this point is collapsed behind one button: the boxes
        # are the raw engine settings and the presets above already set them
        # (user request 2026-09-20). Reassigning `body` puts every section
        # built below inside the panel instead of the tab.
        self.advanced_open = False
        self.advanced_button = ui.button(body, self._ADVANCED_SHUT, command=self._toggle_advanced)
        self.advanced_button.pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        self.advanced = ui.frame(body)
        body = self.advanced

        # ---- LOD / draw distance (the keeper in perf_api.set_lod_system)
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="LOD / draw distance", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        lod_row = ui.frame(body)
        lod_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        self.lod_vars = {}
        lod_row2 = ui.frame(body)
        lod_row2.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        for row, label, key, step in ((lod_row, "Level 1 beyond (m)", "lod1_m", 10), (lod_row, "textures x", "tex1", 0.05),
                                      (lod_row, "Level 2 beyond (m)", "lod2_m", 10), (lod_row, "textures x", "tex2", 0.05),
                                      (lod_row, "Far textures beyond (m)", "far_m", 10), (lod_row, "textures x", "tex3", 0.05),
                                      (lod_row, "Hide beyond (m, 0 = never)", "hide_m", 10),
                                      (lod_row2, "LOD distance x", "lod_scale", 0.5), (lod_row2, "Foliage %", "foliage", 5),
                                      (lod_row2, "Mip bias", "mip_bias", 1), (lod_row2, "View distance x", "view_scale", 0.1),
                                      (lod_row2, "Nanite edge px", "nanite_edge", 0.5), (lod_row2, "Nanite LOD bias", "nanite_bias", 0.5)):
            ui.label(row, text=label).pack(side="left", padx=(0, 2))
            var = tk.StringVar(value=f"{api.LOD_DEFAULTS[key]:g}")
            low, high = api.LOD_RANGES[key]
            ui.spinbox(row, from_=low, to=high, increment=step, textvariable=var, width=6).pack(side="left", padx=(0, PAD_SM))
            self.lod_vars[key] = var
        lod_buttons = ui.frame(body)
        lod_buttons.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.button(lod_buttons, "Apply LOD system", kind="accent", command=self._apply_lod).pack(side="left", padx=(0, PAD_SM))
        ui.button(lod_buttons, "Restore game defaults", outline=True, command=self._restore_lod).pack(side="left", padx=(0, PAD_LG))
        ui.button(lod_buttons, "Measure FPS", command=self._measure_fps).pack(side="left")
        self.fps_lbl = ui.label(lod_buttons, text="", muted=True)
        self.fps_lbl.pack(side="left", padx=(PAD_SM, 0))
        ui.label(body, text="Two detail levels by distance, as you move around: beyond Level 1 a scenery mesh swaps to its "
                            "low-poly proxy instead of its full Nanite geometry (the game ships one proxy per mesh; Nanite has no "
                            "finer per-object dial) and keeps only the given fraction of its texture resolution loaded (x0.5 = half), "
                            "beyond Level 2 it also stops casting shadows and drops to the second texture fraction (x0.25 = a "
                            "quarter), beyond Far textures it keeps only the third fraction (x0.1), and beyond Hide (off by "
                            "default) it is not drawn at all. A texture shared with something "
                            "nearer stays at the nearer object's resolution. Everything comes back as you approach; players, "
                            "weapons and held items are never touched. LOD distance x makes classic meshes drop detail sooner; "
                            "Foliage % thins foliage and grass; Mip bias drops texture detail; View distance x scales the game's "
                            "own cull distances; Nanite edge px is how big Nanite lets its triangles get on screen (the game uses "
                            "1.5) and Nanite LOD bias makes every near Nanite mesh coarser (0 = the game's detail, 3 is clearly "
                            "lower; it also coarsens a Nanite landscape such as WornHouse's ground, which the levels never touch). "
                            "The keeper only runs while you have a character in a match.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        self.lod_status_lbl = ui.label(body, text="LOD system: game defaults", muted=True)
        self.lod_status_lbl.pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- Live console settings, grouped
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Live settings", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        perf_buttons = ui.frame(body)
        perf_buttons.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.button(perf_buttons, "Read current", command=self._read_perf).pack(side="left", padx=(0, PAD_SM))
        ui.button(perf_buttons, "Apply settings", kind="accent", command=self._apply_perf).pack(side="left", padx=(0, PAD_SM))
        ui.button(perf_buttons, "Restore game values", outline=True, command=self._restore_perf).pack(side="left")
        self.perf_vars = {}
        self._perf_kind = {}
        rows_by_group = {}
        for row in api.PERF_SETTINGS:
            rows_by_group.setdefault(row[3], []).append(row)
        for group, title in api.PERF_GROUPS:
            ui.label(body, text=title, bold=True).pack(anchor="w", padx=PAD, pady=(PAD_SM, 2))
            grid = ui.frame(body)
            grid.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
            for i, (key, cvar, label, _g, kind, game, perf, lo, hi, step, help_text) in enumerate(rows_by_group.get(group, [])):
                cell = ui.frame(grid)
                cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, PAD_LG), pady=1)
                self._perf_kind[key] = kind
                if kind == "bool":
                    var = tk.IntVar(value=int(game))
                    ui.checkbutton(cell, text=label, variable=var).pack(side="left")
                else:
                    var = tk.StringVar(value=f"{game:g}")
                    ui.spinbox(cell, from_=lo, to=hi, increment=step, textvariable=var, width=7).pack(side="left", padx=(0, PAD_SM))
                    ui.label(cell, text=label).pack(side="left")
                ui.label(cell, text=cvar, muted=True).pack(side="left", padx=(PAD_SM, 0))
                self.perf_vars[key] = var
        ui.label(body, text="Read current fills the boxes with what your game is using right now (the tab starts with the "
                            "engine's usual defaults). Apply sets every box live and remembers the value each variable had "
                            "the first time, so Restore puts the game's own values back. Global illumination, reflections and "
                            "virtual shadow maps are the expensive lighting paths; the CPU group trims the per-frame work the "
                            "game thread does for animation, particles and streaming; the Memory group shrinks what is held; "
                            "the Textures & VRAM group scales what the texture streamer keeps resident at every distance, so "
                            "video memory stops spilling into system RAM (the sampling bias and anisotropy also cut GPU "
                            "texture bandwidth).",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        self.perf_status_lbl = ui.label(body, text="Live settings: not applied", muted=True)
        self.perf_status_lbl.pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- Memory now
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Memory", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        mem_row = ui.frame(body)
        mem_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.button(mem_row, "Free memory now (garbage collect)", command=self._collect_garbage).pack(side="left", padx=(0, PAD_SM))
        ui.label(mem_row, text="Runs the engine's garbage collector immediately (a short hitch); the interval box above sets how often the game does it itself.",
                 muted=True, wraplength=560, justify="left").pack(side="left")

        # ---- Windows process priority
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Process priority (Windows)", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        prio_row = ui.frame(body)
        prio_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.label(prio_row, text="Game:").pack(side="left", padx=(0, PAD_SM))
        self.priority_buttons = {}
        for level, text in (("high", "High"), ("above", "Above normal"), ("normal", "Normal")):
            b = ui.button(prio_row, text, command=lambda lv=level: self._set_priority(lv))
            b.pack(side="left", padx=(0, PAD_SM))
            self.priority_buttons[level] = b
        self.overlay_low_var = tk.IntVar(value=0)
        ui.checkbutton(prio_row, text="Keep this overlay below normal priority", variable=self.overlay_low_var,
                       command=self._toggle_own_priority).pack(side="left", padx=(PAD_LG, 0))
        ui.label(body, text="High tells Windows to schedule the game's threads ahead of everything else on the PC (browsers, Discord, "
                            "this overlay). It is the one real CPU-side change that needs no game restart; it resets when the "
                            "game is restarted.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))

        # ---- Startup settings (Engine.ini)
        ttk.Separator(body, orient="horizontal").pack(fill="x", padx=PAD, pady=(PAD_LG, PAD_SM))
        ui.label(body, text="Startup settings (Engine.ini, restart required)", header=True).pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        start_row = ui.frame(body)
        start_row.pack(fill="x", padx=PAD, pady=(0, PAD_SM))
        ui.label(start_row, text="RHI thread now:").pack(side="left", padx=(0, PAD_SM))
        ui.button(start_row, "On", command=lambda: self._rhi_thread(True)).pack(side="left", padx=(0, PAD_SM))
        ui.button(start_row, "Off", outline=True, command=lambda: self._rhi_thread(False)).pack(side="left", padx=(0, PAD_LG))
        ui.button(start_row, "Write parallel RHI setting to Engine.ini", command=lambda: self._write_startup(True)).pack(side="left", padx=(0, PAD_SM))
        ui.button(start_row, "Remove again", outline=True, command=lambda: self._write_startup(False)).pack(side="left", padx=(0, PAD_LG))
        self.startup_status_lbl = ui.label(start_row, text="", muted=True)
        self.startup_status_lbl.pack(side="left")
        ui.label(body, text="The RHI thread hands draw commands to DirectX on its own thread. On / Off issue the engine's "
                            "r.RHIThread.Enable command live; it is a command, not a setting, so nothing can be read back: Measure "
                            "FPS before and after. To have it on from start-up add -rhithread to Bodycam's Steam launch options; "
                            "never put r.RHIThread.Enable in Engine.ini, the game refuses to start with it there (this overlay "
                            "strips that line if it finds it). The Engine.ini button writes only r.RHICmdUseParallelAlgorithms=1 "
                            "under [SystemSettings], with a backup next to the file and its read-only flag kept; restart to apply.",
                 muted=True, wraplength=760, justify="left").pack(anchor="w", padx=PAD, pady=(0, PAD_SM))
        self._refresh_startup_status()

    # ------------------------------------------------------------------ LOD system
    def _lod_settings(self):
        settings = {}
        for key, var in self.lod_vars.items():
            try:
                settings[key] = float(var.get())
            except (tk.TclError, ValueError):
                messagebox.showwarning("Invalid value", f"Enter a number for {key}.")
                return None
        try:
            api._lod_settings(settings)
        except ValueError as exc:
            messagebox.showwarning("Out of range", str(exc))
            return None
        return settings

    def _apply_lod(self):
        settings = self._lod_settings()
        if settings is None:
            return

        def work():
            return api.set_lod_system(settings)

        def done(result):
            text = str(result).strip()
            self.lod_status_lbl.configure(text=text[:160])
            self.app.status(text)

        self.app.runner.run(work, done, self.app.on_error("ERROR applying the LOD system"))

    def _restore_lod(self):
        def work():
            return api.restore_lod_system()

        def done(result):
            self.lod_status_lbl.configure(text="LOD system: game defaults")
            self.app.status(str(result).strip())

        self.app.runner.run(work, done, self.app.on_error("ERROR restoring the LOD system"))

    def _measure_fps(self):
        self.fps_lbl.configure(text="measuring…")

        def work():
            return api.measure_fps()

        def done(fps):
            self.fps_lbl.configure(text=f"{fps:.0f} fps" if fps else "no reading")
            self.app.status(f"Your game is running at about {fps:.0f} fps right now." if fps else "Could not read the frame time.")

        self.app.runner.run(work, done, self.app.on_error("ERROR measuring FPS"))

    # ------------------------------------------------------------------ live settings
    def perf_values(self):
        """{key: float} from the boxes, validated; None (after a warning) on bad input."""
        values = {}
        for key, var in self.perf_vars.items():
            try:
                values[key] = float(var.get())
            except (tk.TclError, ValueError):
                messagebox.showwarning("Invalid value", f"Enter a number for {key}.")
                return None
        try:
            return api.perf_settings_clean(values)
        except ValueError as exc:
            messagebox.showwarning("Out of range", str(exc))
            return None

    def _fill_perf(self, values):
        for key, value in values.items():
            var = self.perf_vars.get(key)
            if var is None:
                continue
            if self._perf_kind[key] == "bool":
                var.set(1 if float(value) >= 0.5 else 0)
            else:
                var.set(f"{float(value):g}")

    def _fill_perf_preset(self):
        self._fill_perf({row[0]: row[6] for row in api.PERF_SETTINGS})
        self.app.status("Max performance preset filled in; press Apply settings to use it.")

    # ------------------------------------------------------------------ presets
    def _apply_preset(self, name):
        for preset, live, lod, _blurb in api.PERF_PRESETS:
            if preset == name:
                break
        else:
            return
        self._fill_perf(live)
        for key, value in lod.items():
            if key in self.lod_vars:
                self.lod_vars[key].set(f"{float(value):g}")
        values = self.perf_values()
        settings = self._lod_settings()
        if values is None or settings is None:
            return

        dlss_mode = api.PRESET_DLSS.get(name)
        if dlss_mode is not None:
            values.pop("screen_pct", None)      # DLSS sets the render scale itself; a fixed % would fight it

        def work():
            first = api.set_perf_settings(values)
            second = api.set_lod_system(settings)
            third = ""
            if dlss_mode is not None:
                st = api.set_dlss_mode(dlss_mode)
                if st.get("supported"):
                    third = " | DLSS " + self._dlss_text(st)
                else:
                    # no DLSS on this card: the same rung on FSR, or XeSS
                    rung = api.PRESET_AMD.get(name)
                    if rung is None:
                        third = " | DLSS not supported on this GPU (TSR at the render scale)"
                    else:
                        third = " | no DLSS on this GPU, " + self._fsr_text(api.set_amd_mode(rung))
            return f"{name} preset: {first} | {second}{third}"

        def done(result):
            text = str(result).strip()
            self.perf_status_lbl.configure(text=f"Live settings: {name} preset applied")
            self.lod_status_lbl.configure(text=f"LOD system: {name} preset applied")
            self.app.status(text)

        self.app.runner.run(work, done, self.app.on_error(f"ERROR applying the {name} preset"))

    def _apply_game_defaults(self):
        self._fill_perf({row[0]: row[5] for row in api.PERF_SETTINGS})
        for key, value in api.LOD_DEFAULTS.items():
            self.lod_vars[key].set(f"{float(value):g}")

        def work():
            first, second = api.restore_perf_settings(), api.restore_lod_system()
            st = api.restore_dlss()
            return f"{first} | {second} | DLSS {self._dlss_text(st)}"

        def done(result):
            self.perf_status_lbl.configure(text="Live settings: game values restored")
            self.lod_status_lbl.configure(text="LOD system: game defaults")
            self.app.status(str(result).strip())

        self.app.runner.run(work, done, self.app.on_error("ERROR restoring the game defaults"))

    def _read_perf(self):
        def work():
            return api.read_perf_settings()

        def done(values):
            self._fill_perf(values)
            self.app.status(f"Read {len(values)} live settings from the game.")

        self.app.runner.run(work, done, self.app.on_error("ERROR reading the settings"))

    def _apply_perf(self):
        values = self.perf_values()
        if values is None:
            return

        def work():
            return api.set_perf_settings(values)

        def done(result):
            text = str(result).strip()
            self.perf_status_lbl.configure(text=text[:160])
            self.app.status(text)

        self.app.runner.run(work, done, self.app.on_error("ERROR applying the settings"))

    def _restore_perf(self):
        def work():
            return api.restore_perf_settings()

        def done(result):
            self.perf_status_lbl.configure(text="Live settings: game values restored")
            self.app.status(str(result).strip())

        self.app.runner.run(work, done, self.app.on_error("ERROR restoring the settings"))

    # ------------------------------------------------------------------ the advanced panel
    _ADVANCED_NAME = "Super Duper Nerd Advanced Options"
    _ADVANCED_SHUT = "\u25b6  " + _ADVANCED_NAME
    _ADVANCED_OPEN = "\u25bc  " + _ADVANCED_NAME

    def _toggle_advanced(self):
        self.advanced_open = not self.advanced_open
        if self.advanced_open:
            self.advanced.pack(fill="both", expand=True)
        else:
            self.advanced.pack_forget()
        self.advanced_button.configure(text=self._ADVANCED_OPEN if self.advanced_open else self._ADVANCED_SHUT)

    # ------------------------------------------------------------------ FSR
    @staticmethod
    def _fsr_text(st):
        if not st:
            return "no reading"
        which = st.get("which")
        label = st.get("label", "")
        if which == "fsr":
            return f"FSR, {label}"
        if which == "off":
            return "off (TSR at the render scale)"
        if which == "xess":
            scale = st.get("screen")
            return f"XeSS, {label}" + (f", rendering at {scale:.0f}%" if scale and scale > 0 else "")
        if st.get("supported") is False and "rung" in st:
            return f"{label}: neither FSR nor XeSS would start on this machine"
        # a plain status read rather than a rung
        mode = int(st.get("mode") or 0)
        if st.get("supported") and mode > 0:
            scale = st.get("screen")
            return f"XeSS mode {mode}" + (f", rendering at {scale:.0f}%" if scale and scale > 0 else "")
        return "off (TSR at the render scale)"

    def _show_fsr(self, st):
        self.fsr_status_lbl.configure(text="Upscaler: " + self._fsr_text(st))

    def _read_fsr(self):
        self.app.runner.run(api.xess_status, self._show_fsr, self.app.on_error("ERROR reading the upscaler"))

    def _set_amd(self, key):
        def done(st):
            self._show_fsr(st)
            self.app.status("Upscaler: " + self._fsr_text(st))
        self.app.runner.run(lambda: api.set_amd_mode(key), done, self.app.on_error("ERROR setting the upscaler"))

    # ------------------------------------------------------------------ DLSS
    @staticmethod
    def _dlss_text(st):
        if not st or st.get("mode") in (None, -1):
            return "plugin not found"
        if not st.get("supported"):
            return "not supported on this GPU"
        names = {m: label for m, label, _s in api.DLSS_MODES}
        mode = int(st.get("mode") or 0)
        scale = st.get("screen")
        rr = ", ray reconstruction on" if st.get("rr_enabled") else ""
        return f"{names.get(mode, f'mode {mode}')}, rendering at {scale:.0f}%{rr}" if scale is not None else names.get(mode, f"mode {mode}")

    def _show_dlss(self, st):
        self.dlss_status_lbl.configure(text="DLSS: " + self._dlss_text(st))
        try:
            self.dlss_rr_var.set(1 if st.get("rr_enabled") else 0)
            self.dlss_rr_check.configure(state="normal" if st.get("rr_supported") else "disabled")
        except tk.TclError:
            pass

    def _read_dlss(self):
        self.app.runner.run(api.dlss_status, self._show_dlss, self.app.on_error("ERROR reading DLSS"))

    def _set_dlss(self, mode):
        def done(st):
            self._show_dlss(st)
            self.app.status("DLSS: " + self._dlss_text(st))
        self.app.runner.run(lambda: api.set_dlss_mode(mode), done, self.app.on_error("ERROR setting the DLSS mode"))

    def _toggle_dlss_rr(self):
        enable = bool(self.dlss_rr_var.get())
        self.app.runner.run(lambda: api.set_dlss_rr(enable), self._show_dlss, self.app.on_error("ERROR toggling ray reconstruction"))

    def _restore_dlss(self):
        def done(st):
            self._show_dlss(st)
            self.app.status("DLSS back to the game's mode: " + self._dlss_text(st))
        self.app.runner.run(api.restore_dlss, done, self.app.on_error("ERROR restoring DLSS"))

    def _collect_garbage(self):
        self.app.runner.run(api.collect_garbage, lambda r: self.app.status(str(r).strip()), self.app.on_error("ERROR collecting garbage"))

    # ------------------------------------------------------------------ priority / startup
    def _set_priority(self, level):
        try:
            self.app.status(api.set_game_priority(level))
        except Exception as exc:  # ctypes / tasklist problems: report, never raise into Tk
            self.app.status(f"ERROR setting the game's priority: {exc}", bad=True)

    def _toggle_own_priority(self):
        try:
            self.app.status(api.set_own_priority("below" if self.overlay_low_var.get() else "normal"))
        except Exception as exc:
            self.app.status(f"ERROR setting the overlay's priority: {exc}", bad=True)

    def _rhi_thread(self, enable):
        self.app.runner.run(lambda: api.set_rhi_thread(enable), lambda r: self.app.status(str(r).strip()),
                            self.app.on_error("ERROR issuing the RHI thread command"))

    def _refresh_startup_status(self):
        try:
            found = api.read_startup_settings()
        except Exception:
            found = {}
        wanted = dict(api.STARTUP_SETTINGS)
        if any(k in found for k in api.STARTUP_REMOVE_KEYS):
            text = "Engine.ini: contains r.RHIThread.Enable, which crashes the game at start-up: press Remove again"
        elif all(found.get(k) == v for k, v in wanted.items()):
            text = "Engine.ini: parallel RHI setting present (restart the game if not yet)"
        else:
            text = "Engine.ini: not set"
        self.startup_status_lbl.configure(text=text)

    def _write_startup(self, enable):
        try:
            result = api.write_startup_settings(enable)
        except Exception as exc:
            self.app.status(f"ERROR writing Engine.ini: {exc}", bad=True)
            return
        self._refresh_startup_status()
        self.app.status(result)
        messagebox.showinfo("Restart needed", result)
