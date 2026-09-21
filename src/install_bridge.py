"""Setup logic for getting ClaudeBridge running in Bodycam.

Deploys the bundled ue4ss_bundle/ (if UE4SS isn't already installed) and
mod/ClaudeBridge/ into the game's Binaries/Win64, registering the mod in
mods.txt. Design rationale for what gets bundled/deployed and why: see
docs/DOCUMENTATION.md section 5.4.

Callable standalone (`python src/install_bridge.py`) or imported by overlay_app.py
to run automatically on startup.
"""
import filecmp
import os
import re
import shutil
import sys
import webbrowser

try:
    import winreg
except ImportError:  # not running on Windows (e.g. this file imported for a syntax check)
    winreg = None

# PyInstaller onefile builds extract bundled data (see build.bat's --add-data)
# to a temp dir exposed as sys._MEIPASS; plain `python src/install_bridge.py` runs
# use this file's own directory instead. Same pattern as perf_api.py's _HERE.
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
BUNDLED_MOD = os.path.join(HERE, "mod", "ClaudeBridge")
UE4SS_BUNDLE = os.path.join(HERE, "ue4ss_bundle")
UE4SS_RELEASES_URL = "https://github.com/UE4SS-RE/RE-UE4SS/releases"

# Fallback only -- find_game_root() checks every registered Steam library
# first (see _steam_library_paths), so this short guess-list is just a safety
# net for a non-Steam copy or if that lookup fails for some reason.
CANDIDATE_ROOTS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Bodycam\Bodycam",
    r"C:\Program Files\Steam\steamapps\common\Bodycam\Bodycam",
    r"D:\SteamLibrary\steamapps\common\Bodycam\Bodycam",
    r"D:\Steam\steamapps\common\Bodycam\Bodycam",
]


def _steam_install_path():
    """Finds the Steam client's own install directory (registry first, since
    Steam itself can be installed anywhere -- falling back to the common
    default) so its library folders can be read from libraryfolders.vdf."""
    if winreg is not None:
        for hive, subkey, value_name in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, value_name)
                    path = os.path.normpath(path)
                    if os.path.isdir(path):
                        return path
            except OSError:
                continue
    for guess in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(guess):
            return guess
    return None


def _steam_library_paths():
    """Every Steam library folder (the base install plus any added under
    Steam's own Storage settings -- these can be on any drive/path), parsed
    from libraryfolders.vdf. This is what lets find_game_root() locate
    Bodycam wherever the user actually put it instead of only the handful of
    default paths in CANDIDATE_ROOTS."""
    steam = _steam_install_path()
    if steam is None:
        return []
    vdf_path = os.path.join(steam, "steamapps", "libraryfolders.vdf")
    try:
        text = open(vdf_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return [steam]
    # Only the "path" value of each library entry is needed here, so a regex
    # over quoted "path" lines is enough -- no need for a full VDF parser.
    paths = [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s*"([^"]+)"', text)]
    return list(dict.fromkeys(paths + [steam]))  # de-dup, keep order, base install always included


def find_game_root():
    for library in _steam_library_paths():
        win64 = os.path.join(library, "steamapps", "common", "Bodycam", "Bodycam", "Binaries", "Win64")
        if os.path.isdir(win64):
            return win64
    for root in CANDIDATE_ROOTS:
        win64 = os.path.join(root, "Binaries", "Win64")
        if os.path.isdir(win64):
            return win64
    return None


CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "BodycamOverlay")
# Folders the app expects to own inside CONFIG_DIR. This app saves nothing but
# its own ui state, so there are none.
CONFIG_SUBDIRS = ()


def has_config_dir():
    return os.path.isdir(CONFIG_DIR)


def ensure_config_dir():
    """Create %LOCALAPPDATA%\\BodycamOverlay (and its subfolders) when missing.

    Called on install and on every startup check. Repairing the tool means
    deleting the game's ue4ss folder, NOT this one -- this folder holds the
    user's saved custom maps -- but if it has been deleted anyway, the app
    puts it back instead of failing to save later. Returns True when it had to
    create something."""
    created = not os.path.isdir(CONFIG_DIR)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        for name in CONFIG_SUBDIRS:
            os.makedirs(os.path.join(CONFIG_DIR, name), exist_ok=True)
    except OSError:
        return False
    return created


def has_ue4ss(win64):
    """True only for a UE4SS install that will actually work -- also checks
    for the shared UEHelpers Lua library mods require(), not just the ue4ss/
    folder's existence. See docs/DOCUMENTATION.md section 5.4 for why that
    distinction matters."""
    ue4ss_dir = os.path.join(win64, "ue4ss")
    if not os.path.isdir(ue4ss_dir):
        return False
    return os.path.isfile(os.path.join(ue4ss_dir, "Mods", "shared", "UEHelpers", "UEHelpers.lua"))


def has_claude_bridge(win64):
    return os.path.isdir(os.path.join(win64, "ue4ss", "Mods", "ClaudeBridge"))


def deploy_ue4ss_bundle(win64):
    """Copies dwmapi.dll + the ue4ss/ folder (UE4SS.dll, settings, enabler mods)
    from ue4ss_bundle/ into the game's Binaries/Win64. These are the exact files
    already installed and running on this machine, just redeployed -- nothing
    fetched fresh. Returns True if deployed, False if the bundle isn't present
    (source-only checkout that skipped the bundling step)."""
    if not os.path.isdir(UE4SS_BUNDLE):
        return False
    shutil.copy2(os.path.join(UE4SS_BUNDLE, "dwmapi.dll"), os.path.join(win64, "dwmapi.dll"))
    dest_ue4ss = os.path.join(win64, "ue4ss")
    if os.path.isdir(dest_ue4ss):
        shutil.rmtree(dest_ue4ss)
    shutil.copytree(os.path.join(UE4SS_BUNDLE, "ue4ss"), dest_ue4ss)
    return True


def bridge_outdated(win64):
    """True when any file of the installed ClaudeBridge mod differs from the
    bundled copy (or is missing), so a newer bundled mod gets installed."""
    dest = os.path.join(win64, "ue4ss", "Mods", "ClaudeBridge")
    for root, _dirs, files in os.walk(BUNDLED_MOD):
        for name in files:
            bundled = os.path.join(root, name)
            installed = os.path.join(dest, os.path.relpath(bundled, BUNDLED_MOD))
            if not os.path.isfile(installed) or not filecmp.cmp(bundled, installed, shallow=False):
                return True
    return False


def install_claude_bridge(win64):
    """Copies the bundled ClaudeBridge mod in and registers it in mods.txt.
    Assumes ue4ss/ already exists -- check has_ue4ss() first."""
    mods_dir = os.path.join(win64, "ue4ss", "Mods")
    dest = os.path.join(mods_dir, "ClaudeBridge")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(BUNDLED_MOD, dest)

    mods_txt = os.path.join(mods_dir, "mods.txt")
    line_needed = "ClaudeBridge : 1"
    content = open(mods_txt, encoding="utf-8", errors="replace").read() if os.path.exists(mods_txt) else ""
    if "ClaudeBridge" not in content:
        with open(mods_txt, "a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write(line_needed + "\n")
    return dest


def ensure_setup(prompt_for_path=None, on_status=None):
    """Full check used by overlay_app.py at startup. `prompt_for_path`, if given,
    is called (path = prompt_for_path()) when auto-detection fails, so a GUI can
    show its own dialog instead of blocking on input(). `on_status(msg)` is
    called with human-readable progress messages.

    Returns a dict: {ok: bool, win64: str|None, reason: str}
    reason is one of: 'ready', 'needs_ue4ss', 'not_found', 'installed_bridge',
    'installed_ue4ss_and_bridge', 'reinstalled_ue4ss'.

    'reinstalled_ue4ss' is the repair path: the documented way to fix the tool
    is to delete the game's ue4ss folder, so finding it gone on startup is
    expected, not an error -- the bundled copy goes straight back in.
    """
    def status(msg):
        if on_status:
            on_status(msg)

    # The app's own folder first: it is recreated if the user deleted it (which
    # the help now tells them NOT to do, since their maps live there).
    if ensure_config_dir():
        status("Recreated the app folder at " + CONFIG_DIR + ".")

    win64 = find_game_root()
    if win64 is None and prompt_for_path:
        candidate = prompt_for_path()
        if candidate and os.path.isdir(candidate):
            win64 = candidate

    if win64 is None:
        return {"ok": False, "win64": None, "reason": "not_found"}

    if not has_ue4ss(win64):
        # Was there a ue4ss folder at all? Gone entirely = the documented repair
        # step; present but broken = a half-installed or damaged copy. Either
        # way the bundle is redeployed, but the message should say which.
        was_deleted = not os.path.isdir(os.path.join(win64, "ue4ss"))
        status(("UE4SS folder missing at " if was_deleted else "UE4SS is incomplete at ")
               + os.path.join(win64, "ue4ss") + ".")
        if deploy_ue4ss_bundle(win64):
            status("Reinstalled the bundled UE4SS." if was_deleted
                   else "Deployed the bundled UE4SS (your own previously-installed copy).")
            install_claude_bridge(win64)
            return {"ok": True, "win64": win64,
                    "reason": "reinstalled_ue4ss" if was_deleted else "installed_ue4ss_and_bridge"}
        status("No bundled UE4SS available -- opening the official release page.")
        try:
            webbrowser.open(UE4SS_RELEASES_URL)
        except Exception:
            pass
        return {"ok": False, "win64": win64, "reason": "needs_ue4ss"}

    if not has_claude_bridge(win64):
        status("UE4SS found. Installing the ClaudeBridge mod...")
        install_claude_bridge(win64)
        return {"ok": True, "win64": win64, "reason": "installed_bridge"}

    # An installed mod that differs from the bundled one is replaced (2026-09-18:
    # the bundled main.lua gained a watchdog, and an existing install never
    # got mod updates before -- only a missing one was installed).
    if bridge_outdated(win64):
        status("ClaudeBridge is older than the bundled copy. Updating it...")
        install_claude_bridge(win64)
        return {"ok": True, "win64": win64, "reason": "updated_bridge"}

    status("ClaudeBridge already installed.")
    return {"ok": True, "win64": win64, "reason": "ready"}


def _prompt_for_path():
    print("Couldn't auto-find your Bodycam install. Checked every registered "
          "Steam library plus these common locations:")
    for r in CANDIDATE_ROOTS:
        print("  ", r)
    path = input("Paste the full path to Bodycam's Binaries\\Win64 folder: ").strip('"')
    return path if os.path.isdir(path) else None


def main():
    result = ensure_setup(prompt_for_path=_prompt_for_path, on_status=print)
    print()
    if result["reason"] == "not_found":
        print("Couldn't find or confirm your Bodycam install. Aborting.")
        sys.exit(1)
    elif result["reason"] == "needs_ue4ss":
        print("UE4SS still needs to be installed manually -- see the page that just opened.")
        print("Re-run this script once it's in place.")
        sys.exit(1)
    else:
        print("Done. Fully restart Bodycam (a new mod FOLDER needs a real restart,")
        print("Ctrl+R hot-reload only picks up changes to mods already loaded).")
        print(f"Game root used: {result['win64']}")


if __name__ == "__main__":
    main()
