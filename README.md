# Bodycam FPS Booster

A small desktop app that tunes **Bodycam**'s rendering while the game is
running, so you can trade picture for frame rate without restarting anything.

---

## Read this before you install it

**This tool loads code into the running game, and anti-cheat will treat it
that way.**

To change anything at runtime it installs UE4SS into your Bodycam folder. That
works by putting a `dwmapi.dll` proxy next to the game executable, which the
game loads at start-up, and UE4SS then runs a scripting runtime inside the
game process. That is the same shape as a cheat loader, because mechanically
it is the same thing. It does not matter that this app only changes rendering.

What that means for you, plainly:

- Bodycam does not ship kernel anti-cheat today. **The day it does, this will
  be flagged**, and the account that used it is the one that wears it.
- Some antivirus products already flag DLL-proxy loaders on sight.
- Using it in a match with other people is your call and your risk. Nobody
  else in the match is affected by anything this changes, but that is not the
  same as being safe from a ban.

If any of that is not a risk you want, do not use this.

---

## What it does

Everything here applies to **your machine only**. Nothing is sent to other
players, and nothing about the match changes for anyone else.

**Presets**, left to right, are the quick answer:

| Preset | What it is for |
|---|---|
| Ultra quality | Above the game's own Epic settings |
| High quality | Halfway between Ultra and Smooth |
| Smooth | The everyday one: keeps the lighting, drops the expensive extras |
| Potato | The floor, for when nothing else holds up |
| Game defaults | Puts everything back |

**Upscaling** renders the frame smaller and rebuilds it at your screen's size.
NVIDIA cards use DLSS. Everything else uses the AMD / Intel row, which tries
FSR and falls back to XeSS; the status line tells you which one engaged. In
this build of the game FSR never starts, so on an AMD card XeSS is what does
the work.

**LOD / draw distance** swaps distant scenery to its low-detail version and
trims the texture memory it holds, in bands you set yourself.

**Live settings** are the raw engine settings behind the presets. They are
collapsed behind a warning on purpose. Change one at a time and use
**Measure FPS** to see what it actually bought you.

There is also a process priority switch, a button to free memory now, and one
start-up setting that needs a game restart.

## What is deliberately not in here

This app is a performance tool and nothing else. It has no map builder, no
match hosting, no loadout or currency editing, no bot or player controls, and
no console. The API it ships with covers rendering and performance only; the
rest of the overlay's code is not part of this program.

## Getting started

1. Run the app.
2. The first run installs the bridge into your Bodycam folder. If it says so,
   fully restart Bodycam.
3. Start the game, then press a preset.

Close the window and the app closes. There is no tray icon and no hotkey.

The app itself is Python's standard library and tkinter, nothing else.

## Credits and licence

MIT licensed. Built from the Bodycam Overlay tooling created by
**clutch5.9**. The copyright notice is kept in `LICENSE`, as the licence
requires.

The bundled UE4SS copy under `src/ue4ss_bundle/ue4ss/` is RE-UE4SS, MIT
licensed separately under its own copyright; see the LICENSE file inside that
folder.
