@echo off
REM ---------------------------------------------------------------------------
REM Packages src\overlay_app.py into a standalone Bodycam FPS Booster exe.
REM
REM A note on why this is plain PyInstaller and not a compiler:
REM
REM Compiling with Nuitka was tried and abandoned. It is not protection. The
REM data files come straight back out with binwalk, every string is still
REM greppable, and all it buys is turning five minutes of reverse engineering
REM into a few hours. In exchange you get an unsigned compiled binary next to
REM a DLL proxy, which is close to textbook antivirus bait.
REM
REM The rule this build follows instead:
REM
REM     COMPILED IN = PUBLISHED.
REM
REM Anything not cleared for release must not be in src\ when this runs. There
REM is no packaging trick that makes shipping uncleared code safe. Keep the
REM build honest and keep the source published alongside it.
REM
REM What ships here is the performance tool and nothing else: no map builder,
REM no hosting, no loadout or currency editing, no console, no joke code. The
REM ClaudeBridge Lua and the bundled UE4SS are readable by design; the Lua has
REM to be written into the game folder as text for UE4SS to load it at all.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto :fail
)

echo Installing/updating app dependencies from requirements.txt...
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Running PyInstaller...
python -m PyInstaller --noconfirm --onefile --windowed --name "BodycamFPSBooster" ^
    --add-data "src/app_icon.ico;." ^
    --add-data "src/mod;mod" ^
    --add-data "src/ue4ss_bundle;ue4ss_bundle" ^
    --exclude-module cv2 ^
    --exclude-module numpy ^
    --exclude-module PIL ^
    --exclude-module pystray ^
    --exclude-module keyboard ^
    --exclude-module moderngl ^
    --exclude-module matplotlib ^
    --exclude-module unittest ^
    --exclude-module pydoc ^
    --exclude-module doctest ^
    --exclude-module pdb ^
    --exclude-module email ^
    --icon "src/app_icon.ico" ^
    src/overlay_app.py
if errorlevel 1 goto :fail

echo.
echo Done. Exe is at dist\BodycamFPSBooster.exe
exit /b 0

:fail
echo.
echo Build failed -- see the error above.
exit /b 1
