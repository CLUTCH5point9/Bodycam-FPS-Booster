"""The only game-facing API this app has: rendering and performance.

Cut from the overlay's game_api.py to a whitelist (tools/... strip script) so
the binary cannot carry anything else. Deliberately absent: currency and
unlocks, loadout and save-file writing, hosting, travel, bots, player-state
and every other call that touches gameplay. If you need one of those, this is
the wrong program.
"""
import math
import json
import os
import re
import shutil
import sys
import bridge_client as bc
_HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", _HERE), "BodycamOverlay")
# The config folder has to exist before anything writes a log or ui state into
# it. The overlay's game_api did this at import; the extract dropped it and the
# app would not start on a machine that had never run the overlay.
os.makedirs(_CONFIG_DIR, exist_ok=True)


def is_connected(timeout=3.0):
    ok, _ = bc.ping(timeout=timeout)
    return ok
LOD_DEFAULTS = {"lod1_m": 50, "lod2_m": 100, "far_m": 200, "hide_m": 0, "tex1": 0.5, "tex2": 0.25, "tex3": 0.1, "lod_scale": 8.0,
                "foliage": 5, "mip_bias": 4, "view_scale": 0.2, "nanite_edge": 1.5, "nanite_bias": 3.0}
LOD_RANGES = {"lod1_m": (10, 5000), "lod2_m": (10, 5000), "far_m": (10, 5000), "hide_m": (0, 5000), "tex1": (0.05, 1.0),
              "tex2": (0.05, 1.0), "tex3": (0.05, 1.0), "lod_scale": (1.0, 8.0), "foliage": (5, 100), "mip_bias": (0, 4),
              "view_scale": (0.2, 1.0), "nanite_edge": (1.0, 8.0), "nanite_bias": (0.0, 8.0)}
LOD_HYSTERESIS = 0.9     # a level is left again only inside 90% of its distance
LOD_SLICE = 400          # components checked per keeper tick
LOD_SLICE_MS = 100       # ms between ticks
LOD_RESWEEP_SECONDS = 5  # how often the keeper looks for a level change / new scenery
LOD_CHANGES_PER_TICK = 60  # level transitions (each may re-register a component) per tick: the first pass after
LOD_TICK_HOOK = "/Game/AdvancedLocomotionV4/Blueprints/CharacterLogic/ALS_Base_CharacterBP.ALS_Base_CharacterBP_C:UpdateCharacterMovement"
LOD_SKIP_OWNERS = ("player", "pawn", "character", "controller", "wep_", "knife", "grenade", "projectile",
                   "flashlight", "drone", "bdt", "hardpointzone", "bombzone", "pointzonepreview")
LOD_MESH_CLASSES = ("StaticMeshComponent", "InstancedStaticMeshComponent",
                    "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent")
LOD_CVARS = {   # setting key -> (cvar, formatter)
    "lod_scale": ("r.StaticMeshLODDistanceScale", lambda v: f"{float(v):.3g}"),
    "foliage_lod": ("foliage.LODDistanceScale", lambda v: f"{float(v):.3g}"),
    "foliage": ("foliage.DensityScale", lambda v: f"{float(v) / 100.0:.3g}"),
    "grass": ("grass.DensityScale", lambda v: f"{float(v) / 100.0:.3g}"),
    "mip_bias": ("r.Streaming.MipBias", lambda v: str(int(v))),
    "view_scale": ("r.ViewDistanceScale", lambda v: f"{float(v):.3g}"),
    "nanite_edge": ("r.Nanite.MaxPixelsPerEdge", lambda v: f"{float(v):.3g}"),
    "nanite_bias": ("r.Nanite.ViewMeshLODBias.Offset", lambda v: f"{float(v):.3g}"),
}
def _lod_settings(settings):
    clean = dict(LOD_DEFAULTS)
    clean.update({k: v for k, v in (settings or {}).items() if k in LOD_DEFAULTS})
    for key, (low, high) in LOD_RANGES.items():
        value = float(clean[key])
        if not (low <= value <= high):
            raise ValueError(f"{key} must be between {low} and {high}")
        clean[key] = value
    if clean["lod2_m"] < clean["lod1_m"]:
        raise ValueError("lod2_m (level 2 distance) must not be closer than lod1_m (level 1)")
    if clean["far_m"] < clean["lod2_m"]:
        raise ValueError("far_m (far textures distance) must not be closer than lod2_m (level 2)")
    if clean["hide_m"] and clean["hide_m"] < clean["far_m"]:
        raise ValueError("hide_m (hide beyond) must be 0 (never) or not closer than far_m (far textures)")
    if clean["tex2"] > clean["tex1"] or clean["tex3"] > clean["tex2"]:
        raise ValueError("texture fractions must not rise with distance (tex1 >= tex2 >= tex3)")
    return clean
def _lod_lua_header():
    skip = ", ".join(f"'{s}'" for s in LOD_SKIP_OWNERS)
    classes = ", ".join(f"'{c}'" for c in LOD_MESH_CLASSES)
    return f"""
_G.__BDTLOD = _G.__BDTLOD or {{orig = {{}}, cvars = {{}}, gen = 0, enabled = false, cull = 0, dirty = false, sweeping = false}}
local S = _G.__BDTLOD
S.list = S.list or {{}}          -- recorded components in slice order
S.cursor = S.cursor or 1
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local SKIP = {{{skip}}}
local CLASSES = {{{classes}}}
local function lower(s) return string.lower(tostring(s or '')) end
local function skippedOwner(owner)
    if not valid(owner) then return true end
    local name = ''
    pcall(function() name = lower(owner:GetClass():GetFName():ToString()) end)
    for _, s in ipairs(SKIP) do if name:find(s, 1, true) then return true end end
    local held = false
    pcall(function() local o = owner:GetOwner(); held = valid(o) end)
    if held then return true end
    pcall(function() local p = owner:GetAttachParentActor(); if valid(p) then held = true end end)
    return held
end
local function cvarGet(name)
    local v = nil
    pcall(function() v = ksl:GetConsoleVariableFloatValue(name) end)
    return v
end
local function cvarSet(name, value)
    local ok = pcall(function() ksl:ExecuteConsoleCommand(UEHelpers.GetWorld(), name .. ' ' .. tostring(value), UEHelpers.GetPlayerController()) end)
    return ok
end
local function worldName()
    local n = ''
    pcall(function() n = UEHelpers.GetWorld():GetFullName():gsub('^World ', '') end)
    return n
end
-- one pass over every scenery mesh component OF THE CURRENT WORLD (FindAllOf
-- also returns components of preview / unloading worlds, which die under the
-- keeper): record its originals (cull distance, visibility, shadow casting,
-- Nanite switch, texture multiplier) the first time, and set the cull
-- distance when a hide distance is wanted (cull 0 = leave it)
local function sweep(cull)
    local touched, skipped = 0, 0
    local prefix = (S.world or worldName()) .. ':'
    for _, cls in ipairs(CLASSES) do
        for _, comp in ipairs(FindAllOf(cls) or {{}}) do
            pcall(function()
                if not valid(comp) then return end
                local key = comp:GetAddress()
                if S.orig[key] ~= nil then touched = touched + 1; return end   -- known: nothing to do (fast path for re-sweeps)
                if comp:GetFullName():find(prefix, 1, true) == nil then return end
                local owner = comp:GetOwner()
                if skippedOwner(owner) then skipped = skipped + 1; return end
                if S.orig[key] == nil then
                    local shown, shadow, nanite, mult, radius, proxy = true, true, false, 1.0, 0.0, true
                    pcall(function() shown = comp:IsVisible() end)
                    pcall(function() shadow = comp.CastShadow and true or false end)
                    pcall(function() nanite = comp.bForceDisableNanite and true or false end)
                    pcall(function() mult = tonumber(comp.StreamingDistanceMultiplier) or 1.0 end)
                    -- world-space bounds radius: the distance to a mesh is measured to
                    -- its EDGE, so a floor plate whose centre is far away stays full
                    -- detail while you stand on it (WornHouse's floor vanished before)
                    pcall(function()
                        local m = comp.StaticMesh
                        if m and m:IsValid() then
                            local b = m:GetBounds()
                            local sc = comp:K2_GetComponentScale()
                            radius = (tonumber(b.SphereRadius) or 0) * math.max(math.abs(sc.X), math.abs(sc.Y), math.abs(sc.Z), 0)
                            -- a mesh with no proxy triangles renders NOTHING on the proxy path: never swap it
                            local tris = m:GetNumTriangles(0)
                            if tris ~= nil and tris <= 0 then proxy = false end
                        end
                    end)
                    S.orig[key] = {{comp = comp, cull = comp.LDMaxDrawDistance or 0.0, shown = shown, shadow = shadow,
                                   nanite = nanite, mult = mult, tex = mult, radius = radius, proxy = proxy, level = 0, hidden = false}}
                    S.list[#S.list + 1] = S.orig[key]
                end
                if cull > 0 then comp:SetCullDistance(cull) end
                touched = touched + 1
            end)
        end
    end
    return touched, skipped
end
"""
def set_lod_system(settings=None, timeout=60):
    """Apply the LOD / draw-distance system to this game (self only). Returns the
    result line. ``settings`` keys: lod1_m (metres beyond which scenery renders
    its low-poly proxy mesh instead of Nanite), lod2_m (beyond which it also
    stops casting shadows), hide_m (beyond which it is hidden; 0 = never),
    tex1 / tex2 (fraction of texture resolution kept resident at level 1 / 2),
    lod_scale (LOD switch distance scale, >1 drops detail sooner), foliage
    (density %), mip_bias (texture mips to drop), view_scale (global
    view-distance scale), nanite_edge / nanite_bias (Nanite detail)."""
    s = _lod_settings(settings)
    cvar_values = {
        "lod_scale": s["lod_scale"], "foliage_lod": s["lod_scale"], "foliage": s["foliage"], "grass": s["foliage"],
        "mip_bias": s["mip_bias"], "view_scale": s["view_scale"], "nanite_edge": s["nanite_edge"], "nanite_bias": s["nanite_bias"],
    }
    sets = ", ".join(f"{{name = '{LOD_CVARS[k][0]}', value = '{LOD_CVARS[k][1](v)}'}}" for k, v in cvar_values.items())
    lua = _lod_lua_header() + f"""
S.gen = S.gen + 1
S.enabled = true
S.world = worldName()
S.lod1 = {s['lod1_m'] * 100.0:.9g}
S.lod2 = {s['lod2_m'] * 100.0:.9g}
S.far = {s['far_m'] * 100.0:.9g}
S.cull = {s['hide_m'] * 100.0:.9g}
S.tex1 = {s['tex1']:.4g}
S.tex2 = {s['tex2']:.4g}
S.tex3 = {s['tex3']:.4g}
local myGen = S.gen
-- console variables: originals once, then the wanted values
local applied = 0
for _, row in ipairs({{{sets}}}) do
    if S.cvars[row.name] == nil then S.cvars[row.name] = cvarGet(row.name) end
    if cvarSet(row.name, row.value) then applied = applied + 1 end
end
local touched, skipped = sweep(S.cull)
S.count = #(FindAllOf('StaticMeshComponent') or {{}})
-- the geometry levels: Nanite ignores cull distances and has no per-object
-- detail dial, so a keeper walks LOD_SLICE components per tick and, by the
-- distance from the pawn, switches each one to its proxy mesh (level 1), then
-- also stops its shadows (level 2), then hides it (level 3, only with a hide
-- distance), and keeps a fraction of its texture resolution (tex1 / tex2)
-- through StreamingDistanceMultiplier, re-registering a Static component
-- (mobility round trip) so the texture streamer reads the new value.
-- A level is left again only inside LOD_HYSTERESIS of its distance.
-- levels: 0 full, 1 proxy + tex1, 2 + no shadows + tex2, 3 far textures tex3, 4 hidden
local function levelAt(d2, f)
    if S.cull > 0 and d2 > (S.cull * f)^2 then return 4 end
    if d2 > (S.far * f)^2 then return 3 end
    if d2 > (S.lod2 * f)^2 then return 2 end
    if d2 > (S.lod1 * f)^2 then return 1 end
    return 0
end
local function applyLevel(row, level)
    local comp = row.comp
    if row.proxy ~= false and (level >= 1) ~= (row.level >= 1) then comp:SetForceDisableNanite(level >= 1 or row.nanite) end
    if (level >= 2) ~= (row.level >= 2) then comp:SetCastShadow(level < 2 and row.shadow) end
    if (level >= 4) ~= (row.level >= 4) then comp:SetVisibility(level < 4, true); row.hidden = level >= 4 end
    local tex = (level >= 3 and S.tex3) or (level >= 2 and S.tex2) or (level >= 1 and S.tex1) or row.mult
    if tex ~= row.tex then
        comp.StreamingDistanceMultiplier = tex
        row.tex = tex
        if comp.Mobility == 0 then comp:SetMobility(2); comp:SetMobility(0) end
    end
    row.level = level
end
-- one keeper tick, on the game thread from the hook below, at most every
-- LOD_SLICE_MS; every LOD_RESWEEP_SECONDS it also notices a level change
-- (forgets the dead components) or new scenery (a build deploys, a level
-- streams in: the StaticMeshComponent count changed) and sweeps again
local function slice()
    if not S.enabled or S.gen ~= myGen then return end
    local now = os.clock()
    if now - (S.last or 0) < {LOD_SLICE_MS / 1000.0:.3f} then return end
    S.last = now
    local t0 = now
    S.ticks = (S.ticks or 0) + 1
    if now - (S.lastSweep or now) >= {LOD_RESWEEP_SECONDS} or S.lastSweep == nil then
        S.lastSweep = now
        local w = worldName()
        if w ~= S.world then
            S.world = w; S.orig = {{}}; S.list = {{}}; S.cursor = 1; S.count = -1
        end
        local n = #(FindAllOf('StaticMeshComponent') or {{}})
        if n ~= S.count then S.count = n; pcall(sweep, S.cull); S.sweeps = (S.sweeps or 0) + 1 end
    end
    local pawn = nil
    pcall(function() pawn = UEHelpers.GetPlayerController().Pawn end)
    local pl = nil
    if valid(pawn) then pcall(function() pl = pawn:K2_GetActorLocation() end) end
    local tickChanges = 0
    if pl then
        local n = #S.list
        if n > 0 then
            for _ = 1, math.min({LOD_SLICE}, n) do
                if tickChanges >= {LOD_CHANGES_PER_TICK} then break end
                if S.cursor > n then S.cursor = 1 end
                local row = S.list[S.cursor]
                S.cursor = S.cursor + 1
                pcall(function()
                    local comp = row.comp
                    if not valid(comp) or not row.shown then return end   -- gone, or hidden by the game itself
                    local l = comp:K2_GetComponentLocation()
                    local d = math.sqrt((l.X - pl.X)^2 + (l.Y - pl.Y)^2 + (l.Z - pl.Z)^2) - (row.radius or 0)
                    if d < 0 then d = 0 end
                    local d2 = d * d
                    local up, down = levelAt(d2, 1.0), levelAt(d2, {LOD_HYSTERESIS})
                    if up > row.level then applyLevel(row, up); S.changes = (S.changes or 0) + 1; tickChanges = tickChanges + 1
                    elseif down < row.level then applyLevel(row, down); S.changes = (S.changes or 0) + 1; tickChanges = tickChanges + 1 end
                end)
            end
        end
    end
    local cost = (os.clock() - t0) * 1000
    S.workMs = (S.workMs or 0) + cost
    if cost > (S.worstMs or 0) then S.worstMs = cost end
end
_G.__BDTLODTick = slice
-- the hook is registered once per process and calls whatever the latest
-- apply installed; the character class is loaded if no match has run yet
local hookText = ''
if not S.hooked then
    local hook = {json.dumps(LOD_TICK_HOOK)}
    if not valid(StaticFindObject(hook)) then pcall(function() LoadAsset((hook:gsub(':.*$', ''))) end) end
    if valid(StaticFindObject(hook)) then
        RegisterHook(hook, function(Context)
            local f = _G.__BDTLODTick
            if f then pcall(f) end
        end)
        S.hooked = true
    else
        hookText = '; the keeper could not hook the character movement update yet, apply again once a match is loaded'
    end
end
local hideText = S.cull > 0 and string.format(', hidden beyond %d m', math.floor(S.cull / 100)) or ''
return string.format('LOD system ON: scenery beyond %d m renders its low-poly proxy with x%s textures (level 1), beyond %d m also without shadows and x%s textures (level 2), beyond %d m x%s textures (level 3)%s, as you move (%d mesh components watched, %d player/held skipped); %d console settings applied; texture mip bias %d, foliage %d%%, LOD distance x%s, view distance x%s, Nanite edge %s px, Nanite LOD bias %s (gen %d)%s',
    math.floor(S.lod1 / 100), tostring(S.tex1), math.floor(S.lod2 / 100), tostring(S.tex2), math.floor(S.far / 100), tostring(S.tex3), hideText, touched, skipped, applied, {int(s['mip_bias'])}, {int(s['foliage'])}, '{s['lod_scale']:.3g}', '{s['view_scale']:.3g}', '{s['nanite_edge']:.3g}', '{s['nanite_bias']:.3g}', S.gen, hookText)
"""
    return bc.run_lua(lua, timeout=timeout)
def restore_lod_system(timeout=60):
    """Put every recorded cull distance, geometry level and console variable
    back (self only)."""
    lua = _lod_lua_header() + """
S.enabled = false
S.gen = S.gen + 1
_G.__BDTLODTick = nil
local restored, gone, shown = 0, 0, 0
-- the cheap part of the restore happens now; the texture re-registers (a
-- mobility round trip each, thousands at once froze the game) are drained a
-- few per tick through the hook, which keeps firing while a character exists
local drain = {}
for key, row in pairs(S.orig) do
    local ok = pcall(function()
        if valid(row.comp) then
            row.comp:SetCullDistance(row.cull)
            if (row.level or 0) >= 1 then row.comp:SetForceDisableNanite(row.nanite and true or false) end
            if (row.level or 0) >= 2 then row.comp:SetCastShadow(row.shadow ~= false) end
            if row.hidden then row.comp:SetVisibility(true, true) end
            if row.tex ~= nil and row.tex ~= row.mult then
                row.comp.StreamingDistanceMultiplier = row.mult
                if row.comp.Mobility == 0 then drain[#drain + 1] = row.comp end
            end
            if (row.level or 0) > 0 then shown = shown + 1 end
            restored = restored + 1
        else gone = gone + 1 end
    end)
    if not ok then gone = gone + 1 end
end
S.orig = {}
S.list = {}
S.cursor = 1
local cv = 0
for name, value in pairs(S.cvars) do
    if value ~= nil and cvarSet(name, value) then cv = cv + 1 end
end
S.cvars = {}
local pending = #drain
if pending > 0 then
    local i = 1
    local function drainTick()
        if S.enabled then _G.__BDTLODTick = nil; return end     -- a new apply took over
        local now = os.clock()
        if now - (S.drainLast or 0) < """ + f"{LOD_SLICE_MS / 1000.0:.3f}" + """ then return end
        S.drainLast = now
        for _ = 1, """ + f"{LOD_CHANGES_PER_TICK}" + """ do
            local comp = drain[i]
            if comp == nil then _G.__BDTLODTick = nil; return end
            pcall(function() if valid(comp) and comp.Mobility == 0 then comp:SetMobility(2); comp:SetMobility(0) end end)
            i = i + 1
        end
    end
    _G.__BDTLODTick = drainTick
end
local drainText = pending > 0 and string.format('; %d texture budgets re-register over the next %d s', pending, math.ceil(pending / """ + f"{LOD_CHANGES_PER_TICK}" + """ * """ + f"{LOD_SLICE_MS / 1000.0:.3f}" + """)) or ''
return string.format('LOD system OFF: %d mesh components restored (%d back to full detail, %d were already gone), %d console settings restored%s', restored, shown, gone, cv, drainText)
"""
    return bc.run_lua(lua, timeout=timeout)
def measure_fps(samples=6, spacing=0.25, timeout=15):
    """Average frames per second of this game right now, from the world's
    frame delta sampled a few times (self only, read-only)."""
    import time as _time
    deltas = []
    for _ in range(max(1, int(samples))):
        text = bc.run_lua("local gs = StaticFindObject('/Script/Engine.Default__GameplayStatics'); "
                          "return tostring(gs:GetWorldDeltaSeconds(UEHelpers.GetWorld()))", timeout=timeout).strip()
        try:
            dt = float(text)
        except ValueError:
            dt = 0.0
        if dt > 0:
            deltas.append(dt)
        _time.sleep(spacing)
    if not deltas:
        return 0.0
    return len(deltas) / sum(deltas)
PERF_SETTINGS = [
    ("screen_pct", "r.ScreenPercentage", "Render scale %", "gpu", "num", 100, 50, 25, 100, 5,
     "Internal render resolution. The biggest GPU lever there is: 75 -> 40 measured 52 -> 71 fps."),
    ("lumen_hwrt", "r.Lumen.HardwareRayTracing", "Lumen hardware ray tracing", "gpu", "bool", 1, 0, 0, 1, 1,
     "Off uses Lumen's software tracing: much cheaper on GPU and no per-frame ray-tracing scene rebuild on the CPU."),
    ("rt_enable", "r.RayTracing.Enable", "Ray tracing effects", "gpu", "bool", 1, 0, 0, 1, 1,
     "Off skips every ray-traced pass."),
    ("gi_method", "r.DynamicGlobalIlluminationMethod", "Global illumination (0 off, 1 Lumen, 2 screen space)", "gpu", "num", 1, 2, 0, 2, 1,
     "Lumen GI is the most expensive lighting path; screen-space GI is far cheaper, 0 is cheapest and flattest."),
    ("refl_method", "r.ReflectionMethod", "Reflections (0 off, 1 Lumen, 2 screen space)", "gpu", "num", 1, 2, 0, 2, 1,
     "Lumen reflections trace the scene every frame; screen-space reflections are cheap."),
    ("lumen_refl", "r.Lumen.Reflections.Allow", "Lumen reflections", "gpu", "bool", 1, 0, 0, 1, 1,
     "Off drops Lumen's reflection pass even when Lumen is the reflection method."),
    ("lumen_probe", "r.Lumen.ScreenProbeGather.DownsampleFactor", "Lumen probe spacing (8-64)", "gpu", "num", 16, 32, 8, 64, 8,
     "Higher = fewer Lumen probes per frame (coarser, cheaper GI)."),
    # r.Shadow.Virtual.Enable is deliberately NOT listed: turning virtual shadow
    # maps off at runtime measured 56 -> 19 fps on WornHouse (2026-09-19),
    # because Nanite geometry then renders its shadows through the cascaded
    # path, which is far more expensive than VSM. It was the "Smooth preset
    # drops to 20 fps with 190 ms latency" report.
    ("csm_cascades", "r.Shadow.CSM.MaxCascades", "Shadow cascades (1-4)", "gpu", "num", 3, 1, 1, 4, 1,
     "Fewer cascades = fewer shadow passes for the lights that still use cascaded shadows."),
    ("vol_fog", "r.VolumetricFog", "Volumetric fog", "gpu", "bool", 1, 0, 0, 1, 1, "Off skips the fog volume passes."),
    ("ao_levels", "r.AmbientOcclusionLevels", "Ambient occlusion levels (-1 auto, 0 off)", "gpu", "num", -1, 0, -1, 4, 1,
     "0 disables screen-space ambient occlusion."),
    ("ssr", "r.SSR.Quality", "Screen-space reflection quality (0-4)", "gpu", "num", 3, 0, 0, 4, 1, "0 disables SSR."),
    ("bloom", "r.BloomQuality", "Bloom quality (0-5)", "gpu", "num", 5, 0, 0, 5, 1, "0 disables bloom."),
    ("dof", "r.DepthOfFieldQuality", "Depth of field quality (0-4)", "gpu", "num", 2, 0, 0, 4, 1, "0 disables depth of field."),
    ("lens", "r.LensFlareQuality", "Lens flare quality (0-3)", "gpu", "num", 1, 0, 0, 3, 1, "0 disables lens flares."),
    ("anim_rate", "a.URO.ForceAnimRate", "Animate skeletons every N frames (0 = every frame)", "cpu", "num", 0, 2, 0, 10, 1,
     "Skeletal animation is game-thread CPU work per character; 2 halves it. Pair with interpolation."),
    ("anim_interp", "a.URO.ForceInterpolation", "Interpolate skipped animation frames", "cpu", "bool", 0, 1, 0, 1, 1,
     "Smooths the frames skipped above."),
    ("skel_lod", "r.SkeletalMeshLODBias", "Skeletal mesh LOD bias (0-3)", "cpu", "num", 0, 1, 0, 3, 1,
     "Characters use a lower LOD sooner: fewer bones and triangles to skin."),
    ("niagara", "fx.Niagara.QualityLevel", "Particle quality (0 low - 4 cinematic)", "cpu", "num", 3, 0, 0, 4, 1,
     "Niagara particle systems are simulated on the CPU; lower quality spawns fewer particles."),
    ("particle_lod", "r.ParticleLODBias", "Particle LOD bias (0-3)", "cpu", "num", 0, 2, 0, 3, 1, "Cheaper particle LODs sooner."),
    ("foliage_lod", "foliage.MinLOD", "Foliage minimum LOD (-1 off)", "cpu", "num", -1, 1, -1, 3, 1,
     "Instanced foliage never uses LOD 0 when set to 1 or more."),
    ("load_ms", "s.AsyncLoadingTimeLimit", "Loading work per frame (ms)", "cpu", "num", 3, 2, 1, 10, 1,
     "How long each frame may spend finishing asset loads; lower = smoother, slower streaming."),
    ("tex_per_frame", "r.Streaming.MaxNumTexturesToStreamPerFrame", "Textures streamed per frame (0 = unlimited)", "cpu", "num", 0, 8, 0, 64, 1,
     "Caps texture streaming work per frame."),
    ("max_fps", "t.MaxFPS", "Frame cap (0 = none)", "cpu", "num", 0, 0, 0, 500, 5,
     "A cap stops the CPU spinning past what the monitor shows."),
    ("tex_pool", "r.Streaming.PoolSize", "Texture streaming pool (MB)", "mem", "num", 2000, 1500, 500, 8000, 100,
     "The texture budget in video memory; smaller = less memory, more mip swapping."),
    ("vsm_pages", "r.Shadow.Virtual.MaxPhysicalPages", "Virtual shadow map pages", "mem", "num", 4096, 2048, 512, 8192, 512,
     "Video memory held by virtual shadow maps."),
    ("gc_interval", "gc.TimeBetweenPurgingPendingKillObjects", "Garbage collection interval (s)", "mem", "num", 61, 61, 10, 600, 10,
     "Seconds between garbage-collection passes: shorter frees memory sooner, longer means fewer GC hitches."),
    # Textures & VRAM (user request 2026-09-19: take load off the GPU and stop
    # video memory spilling into system RAM). All probed live the same day.
    ("stream_boost", "r.Streaming.Boost", "Texture resolution scale (1 = full)", "tex", "num", 1.0, 0.5, 0.25, 1.0, 0.05,
     "Scales the resolution the streamer keeps for EVERY texture by its distance: 0.5 = one mip lower everywhere, half the VRAM."),
    ("eff_screen", "r.Streaming.MaxEffectiveScreenSize", "Streaming assumes screen height (0 = real)", "tex", "num", 0, 1080, 0, 4320, 180,
     "Mips are chosen for this screen height instead of yours: 1080 on a 1440p screen loads smaller mips at every distance."),
    ("hidden_scale", "r.Streaming.HiddenPrimitiveScale", "Resolution kept for hidden scenery", "tex", "num", 0.5, 0.1, 0.05, 1.0, 0.05,
     "What fraction of a texture stays loaded for scenery that is not visible (including the LOD system's hidden level)."),
    ("mip_sample", "r.MipMapLODBias", "Sampling mip bias (0 = sharp)", "tex", "num", 0, 1, 0, 4, 0.5,
     "Makes the GPU sample one mip blurrier across the board: less texture bandwidth per pixel, softer look."),
    ("aniso", "r.MaxAnisotropy", "Anisotropic filtering (1-16)", "tex", "num", 2, 1, 1, 16, 1,
     "Texture filtering at grazing angles: 1 is cheapest for distant floors and walls."),
    ("vt_pool", "r.VT.PoolSizeScale", "Virtual texture pool scale", "tex", "num", 1.0, 0.5, 0.25, 1.0, 0.25,
     "Bodycam uses virtual texturing; this scales the VRAM its page pools take."),
    ("vt_uploads", "r.VT.MaxUploadsPerFrame", "Virtual texture uploads per frame", "tex", "num", 32, 8, 4, 64, 4,
     "Caps virtual texture page uploads per frame: smoother frame times, slower texture fill-in."),
    ("nanite_pool", "r.Nanite.Streaming.StreamingPoolSize", "Nanite geometry pool (MB)", "tex", "num", 512, 256, 128, 1024, 64,
     "VRAM held for streamed Nanite geometry; smaller = less VRAM, more geometry streaming."),
    ("temp_mem", "r.Streaming.MaxTempMemoryAllowed", "Texture staging memory (MB)", "tex", "num", 50, 20, 10, 100, 10,
     "System RAM the streamer may use for in-flight texture uploads."),
]
PERF_GROUPS = (("gpu", "Rendering load (GPU, with a CPU side)"), ("cpu", "CPU"), ("mem", "Memory"),
               ("tex", "Textures & VRAM"))
def _perf_values(**overrides):
    values = {row[0]: row[5] for row in PERF_SETTINGS}       # the engine defaults, then the preset's changes
    values.update(overrides)
    return values
PERF_PRESETS = [
    # Quality presets (user request 2026-09-19): High = the game's own Epic
    # values with the LOD bands pushed far out; Ultra = above the game
    # (finer Lumen probes and Nanite edges, 16x anisotropy, bigger pools).
    ("Ultra quality", _perf_values(lumen_probe=8, csm_cascades=4, ssr=4, dof=4, lens=3, niagara=4, load_ms=5, tex_pool=4000,
                                   vsm_pages=8192, aniso=16, vt_uploads=64, nanite_pool=1024, temp_mem=100),
     {"lod1_m": 1000, "lod2_m": 2000, "far_m": 4000, "hide_m": 0, "tex1": 1.0, "tex2": 1.0, "tex3": 1.0, "lod_scale": 1.0,
      "foliage": 100, "mip_bias": 0, "view_scale": 1.0, "nanite_edge": 1.0, "nanite_bias": 0.0},
     "Above the game's Epic settings: finer Nanite and Lumen detail, 16x anisotropy, bigger texture and geometry pools, "
     "full textures out to a kilometre."),
    # High quality = halfway between Ultra and Smooth on every axis (user
    # request 2026-09-19): Lumen with hardware ray tracing kept, effects at the
    # engine's middle values, DLSS Quality, textures and pools between the two,
    # LOD bands at the geometric mean of the two presets' distances.
    ("High quality", _perf_values(lumen_probe=16, ssr=3, bloom=3, dof=2, lens=1, niagara=3, load_ms=4, tex_pool=3000, vsm_pages=6144,
                                  stream_boost=0.9, hidden_scale=0.35, aniso=8, vt_uploads=24, nanite_pool=768, temp_mem=70),
     {"lod1_m": 250, "lod2_m": 500, "far_m": 1000, "hide_m": 0, "tex1": 0.75, "tex2": 0.5, "tex3": 0.35, "lod_scale": 2.0,
      "foliage": 60, "mip_bias": 0, "view_scale": 0.75, "nanite_edge": 1.25, "nanite_bias": 0.5},
     "Halfway between Ultra and Smooth: Lumen with hardware ray tracing and virtual shadow maps kept, DLSS Quality, "
     "textures and pools in between, scenery simplifies from 250 m."),
    # Smooth measured per setting on WornHouse 2026-09-19 (baseline 56 fps):
    # every value here is neutral or a gain on its own; vsm=0 (0.33x),
    # screen_pct=75 (0.73x, it fights DLSS's own scale), vt_pool=0.75 (0.77x)
    # and nanite_pool=384 (0.96x) were dropped after that measurement.
    ("Smooth", _perf_values(lumen_hwrt=0, refl_method=2, lumen_refl=0, vol_fog=0, ssr=2, bloom=2, dof=0, lens=0, niagara=2,
                            particle_lod=1, stream_boost=0.8, eff_screen=1440, hidden_scale=0.25, vt_uploads=16, temp_mem=40),
     # LOD "sooner and heavier" (user request 2026-09-19): distances are now
     # measured to a mesh's edge, so short bands no longer eat the floor
     # nanite_bias stays 0 and mip_bias 1: WornHouse's terrain is a NANITE
     # LANDSCAPE (16 LandscapeComponents, bEnableNanite true) that the keeper
     # never touches, so the global Nanite bias and the texture mip bias are the
     # only Smooth settings that can make it look "not loaded" (user report
     # 2026-09-19); the per-object levels do the heavy lifting on scenery
     # 2026-09-19 (later): "increase the geometry and texture reduction at a
     # higher distance": bands out to 70/140/250 m, fractions down to
     # 0.35/0.15/0.05, and a mild global Nanite bias (1) back; mip bias 1.
     {"lod1_m": 70, "lod2_m": 140, "far_m": 250, "hide_m": 0, "tex1": 0.35, "tex2": 0.15, "tex3": 0.05, "lod_scale": 4.0, "foliage": 25,
      "mip_bias": 1, "view_scale": 0.5, "nanite_edge": 1.5, "nanite_bias": 1.0},
     "Keeps Lumen GI, virtual shadow maps and per-frame animation; drops hardware ray tracing, Lumen reflections and fog; "
     "DLSS Performance; scenery swaps to its proxy from 70 m with a third of its textures, a sixth from 140 m and a "
     "twentieth from 250 m."),
    # The floor (user request 2026-09-20). Every setting at its cheapest EXCEPT
    # the ones that starve textures: the first cut of this preset stacked
    # r.Streaming.MipBias 4 on r.Streaming.Boost 0.5, a 1080 effective screen,
    # a 1500 MB pool and per-object budgets of 0.25/0.1/0.05 from 25 m out, and
    # every surface collapsed to its smallest mip -- which for these materials
    # averages to flat grey, so the whole map looked washed out and untextured
    # (user screenshot 2026-09-20, "potato mode makes everything kind of a
    # grayish color, like it's wrecking the UV maps"). Geometry, lighting,
    # effects and draw distance stay at the floor; textures get enough to still
    # look like themselves.
    ("Potato", dict({row[0]: row[6] for row in PERF_SETTINGS},
                    mip_sample=0, stream_boost=0.9, eff_screen=0, tex_pool=2000, aniso=2),
     {"lod1_m": 40, "lod2_m": 80, "far_m": 150, "hide_m": 0, "tex1": 0.6, "tex2": 0.35, "tex3": 0.2, "lod_scale": 8.0,
      "foliage": 5, "mip_bias": 1, "view_scale": 0.3, "nanite_edge": 2.0, "nanite_bias": 3.0},
     "The floor: geometry, lighting and effects at their cheapest, scenery on its proxy from 40 m, DLSS or FSR Ultra "
     "performance. Textures are left readable on purpose, because starving them turns every surface flat grey."),
]
STARTUP_SETTINGS = (("r.RHICmdUseParallelAlgorithms", "1"),)
STARTUP_REMOVE_KEYS = ("r.RHIThread.Enable",)
STARTUP_SECTION = "[SystemSettings]"
def _perf_by_key():
    return {row[0]: row for row in PERF_SETTINGS}
def _perf_lua_header():
    return """
_G.__BDTPERF = _G.__BDTPERF or {orig = {}}
local P = _G.__BDTPERF
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local function cvarGet(name)
    local v = nil
    pcall(function() v = ksl:GetConsoleVariableFloatValue(name) end)
    return v
end
local function cvarSet(name, value)
    return pcall(function() ksl:ExecuteConsoleCommand(UEHelpers.GetWorld(), name .. ' ' .. tostring(value), UEHelpers.GetPlayerController()) end)
end
"""
def perf_settings_clean(values):
    """Validate {key: value} against PERF_SETTINGS; returns {key: float}."""
    rows = _perf_by_key()
    clean = {}
    for key, value in (values or {}).items():
        if key not in rows:
            continue
        _, cvar, label, group, kind, game, perf, lo, hi, step, _help = rows[key]
        v = float(value)
        if not (lo <= v <= hi):
            raise ValueError(f"{label} must be between {lo} and {hi}")
        clean[key] = float(int(round(v))) if kind == "bool" else v
    return clean
def read_perf_settings(timeout=20):
    """Current live value of every PERF_SETTINGS console variable: {key: float}."""
    names = ", ".join(f"'{row[1]}'" for row in PERF_SETTINGS)
    lua = _perf_lua_header() + f"""
local out = {{}}
for _, name in ipairs({{{names}}}) do out[#out + 1] = name .. '=' .. tostring(cvarGet(name)) end
return table.concat(out, ';')
"""
    text = bc.run_lua(lua, timeout=timeout).strip()
    by_cvar = {row[1]: row[0] for row in PERF_SETTINGS}
    values = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        name, raw = part.split("=", 1)
        try:
            values[by_cvar[name.strip()]] = float(raw)
        except (KeyError, ValueError):
            pass
    return values
def set_perf_settings(values, timeout=30):
    """Set the given console variables live (self only), remembering each one's
    original the first time so restore_perf_settings can put it back. Returns
    the result line, naming any variable that did not read back as set."""
    clean = perf_settings_clean(values)
    rows = _perf_by_key()
    if not clean:
        raise ValueError("No settings to apply")
    sets = ", ".join(f"{{name = '{rows[k][1]}', value = '{v:.6g}'}}" for k, v in clean.items())
    lua = _perf_lua_header() + f"""
local applied, bad = 0, {{}}
for _, row in ipairs({{{sets}}}) do
    if P.orig[row.name] == nil then P.orig[row.name] = cvarGet(row.name) end
    cvarSet(row.name, row.value)
    local back = cvarGet(row.name)
    if back ~= nil and math.abs(back - tonumber(row.value)) < 1e-4 then applied = applied + 1 else bad[#bad + 1] = row.name end
end
local n = 0
for _ in pairs(P.orig) do n = n + 1 end
local badText = #bad > 0 and (' (did not take: ' .. table.concat(bad, ', ') .. ')') or ''
return string.format('Performance settings: %d applied%s; %d originals remembered for restore', applied, badText, n)
"""
    return bc.run_lua(lua, timeout=timeout)
def restore_perf_settings(timeout=30):
    """Put every remembered console variable back to its recorded original."""
    lua = _perf_lua_header() + """
local n = 0
for name, value in pairs(P.orig) do
    if value ~= nil and cvarSet(name, string.format('%.6g', value)) then n = n + 1 end
end
P.orig = {}
return string.format('Performance settings: %d console variables restored to their original values', n)
"""
    return bc.run_lua(lua, timeout=timeout)
DLSS_LIBRARY = "/Script/DLSSBlueprint.Default__DLSSLibrary"
DLSS_MODES = [   # (mode number in EDLSSMode, label, render scale it implies)
    (2, "DLAA (native)", 100), (3, "Ultra quality", 77), (4, "Quality", 67), (5, "Balanced", 58),
    (6, "Performance (quarter of the pixels)", 50), (7, "Ultra performance", 33), (0, "Off (TSR at the render scale)", None),
]
PRESET_DLSS = {"Ultra quality": 2, "High quality": 4, "Smooth": 6, "Potato": 7}
def _dlss_lua_header():
    return f"""
_G.__BDTDLSS = _G.__BDTDLSS or {{}}
local D = _G.__BDTDLSS
local lib = StaticFindObject('{DLSS_LIBRARY}')
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local function have() return lib ~= nil and lib:IsValid() end
local function status()
    local supported, enabled, mode, rrs, rre, sp = false, false, -1, false, false, -1
    if have() then
        pcall(function() supported = lib:IsDLSSSupported() end)
        pcall(function() enabled = lib:IsDLSSEnabled() end)
        pcall(function() mode = lib:GetDLSSMode() end)
        pcall(function() rrs = lib:IsDLSSRRSupported() end)
        pcall(function() rre = lib:IsDLSSRREnabled() end)
    end
    pcall(function() sp = ksl:GetConsoleVariableFloatValue('r.ScreenPercentage') end)
    return string.format('supported=%s;enabled=%s;mode=%s;screen=%s;rr_supported=%s;rr_enabled=%s;orig=%s',
        tostring(supported), tostring(enabled), tostring(mode), tostring(sp), tostring(rrs), tostring(rre), tostring(D.orig))
end
"""
def _parse_dlss(text):
    out = {}
    for part in text.strip().split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            v = v.strip()
            out[k.strip()] = True if v == "true" else False if v == "false" else (float(v) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else None)
    return out
def dlss_status(timeout=15):
    """{supported, enabled, mode, screen, rr_supported, rr_enabled, orig} from the DLSS plugin (mode -1 = plugin missing)."""
    return _parse_dlss(bc.run_lua(_dlss_lua_header() + "return status()", timeout=timeout))
def set_dlss_mode(mode, timeout=15):
    """Switch DLSS to an EDLSSMode number (see DLSS_MODES; 0 = off, TSR takes over at
    the render scale). The mode before the first change is remembered for restore_dlss."""
    mode = int(mode)
    if mode not in {m for m, _l, _s in DLSS_MODES}:
        raise ValueError(f"Unknown DLSS mode {mode}")
    lua = _dlss_lua_header() + f"""
if not have() then return 'unsupported;' .. status() end
local ok = false
pcall(function() ok = lib:IsDLSSSupported() end)
if not ok then return 'unsupported;' .. status() end
if D.orig == nil then pcall(function() D.orig = lib:GetDLSSMode() end) end
lib:SetDLSSMode(UEHelpers.GetWorld(), {mode})
return status()
"""
    return _parse_dlss(bc.run_lua(lua, timeout=timeout))
def set_dlss_rr(enable, timeout=15):
    """DLSS ray reconstruction on/off (only takes where IsDLSSRRSupported)."""
    lua = _dlss_lua_header() + f"""
if have() then pcall(function() lib:EnableDLSSRR({'true' if enable else 'false'}) end) end
return status()
"""
    return _parse_dlss(bc.run_lua(lua, timeout=timeout))
def restore_dlss(timeout=15):
    """Put the DLSS mode back to what it was before the first set_dlss_mode."""
    lua = _dlss_lua_header() + """
if have() and D.orig ~= nil then pcall(function() lib:SetDLSSMode(UEHelpers.GetWorld(), D.orig) end); D.orig = nil end
return status()
"""
    return _parse_dlss(bc.run_lua(lua, timeout=timeout))
FSR_QUALITY_CVAR = "r.FidelityFX.FSR3.QualityMode"
FSR_ENABLE_CVAR = "r.FidelityFX.FSR3.Enabled"
FSR_SHARPNESS_CVAR = "r.FidelityFX.FSR3.Sharpness"
FSR_MODES = [   # (EFFXFSR3QualityMode, label, the render scale AMD ties to it)
    (1, "Native AA (100%)", 100), (2, "Quality (67%)", 67), (3, "Balanced (59%)", 59),
    (4, "Performance (quarter of the pixels)", 50), (5, "Ultra performance (33%)", 33),
    (0, "Off (TSR at the render scale)", None),
]
PRESET_FSR = {"Ultra quality": 1, "High quality": 2, "Smooth": 4, "Potato": 5}
def _fsr_lua_header():
    return f"""
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local function cvarGet(name)
    local v = nil
    pcall(function() v = ksl:GetConsoleVariableFloatValue(name) end)
    return v
end
local function cvarSet(name, value)
    pcall(function() ksl:ExecuteConsoleCommand(UEHelpers.GetWorld(), name .. ' ' .. tostring(value), UEHelpers.GetPlayerController()) end)
end
local function status()
    return string.format('enabled=%s;mode=%s;sharpness=%s',
        tostring(cvarGet({json.dumps(FSR_ENABLE_CVAR)})), tostring(cvarGet({json.dumps(FSR_QUALITY_CVAR)})),
        tostring(cvarGet({json.dumps(FSR_SHARPNESS_CVAR)})))
end
"""
def _parse_fsr(text):
    out = {}
    for part in (text or "").strip().split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                out[k.strip()] = None
    return out
def fsr_status(timeout=15):
    """{enabled, mode, sharpness} as the game currently reports them."""
    return _parse_fsr(bc.run_lua(_fsr_lua_header() + "return status()", timeout=timeout))
def set_fsr_mode(mode, timeout=15):
    """Switch AMD FSR to one of FSR_MODES (0 = off, TSR takes over at the render
    scale). Returns {enabled, mode, sharpness, took} read back from the game;
    `took` is False when the build refuses the write, which is the normal
    answer on an NVIDIA card here."""
    mode = int(mode)
    if mode not in {m for m, _l, _s in FSR_MODES}:
        raise ValueError(f"Unknown FSR mode {mode}")
    enable = 1 if mode > 0 else 0
    lua = _fsr_lua_header() + f"""
cvarSet({json.dumps(FSR_ENABLE_CVAR)}, {enable})
if {enable} == 1 then cvarSet({json.dumps(FSR_QUALITY_CVAR)}, {mode}) end
return status()
"""
    state = _parse_fsr(bc.run_lua(lua, timeout=timeout))
    state["took"] = bool(state.get("enabled") == enable and (enable == 0 or state.get("mode") == mode))
    return state
XESS_LIBRARY = "/Script/XeSSBlueprint.Default__XeSSBlueprintLibrary"
AMD_MODES = [
    ("native", "Native (77%)", 77, 1, 6),
    ("quality", "Quality (67%)", 67, 2, 5),
    ("balanced", "Balanced (59%)", 59, 3, 4),
    ("performance", "Performance (quarter of the pixels)", 50, 4, 3),
    ("ultra", "Ultra performance (33%)", 33, 5, 1),
    ("off", "Off (TSR at the render scale)", None, 0, 0),
]
PRESET_AMD = {"Ultra quality": "native", "High quality": "quality", "Smooth": "performance", "Potato": "ultra"}
def _xess_lua_header():
    return f"""
local x = StaticFindObject({json.dumps(XESS_LIBRARY)})
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local function have() return x ~= nil and x:IsValid() end
local function status()
    local supported, mode, screen = false, -1, -1
    if have() then
        pcall(function() supported = x:IsXeSSSupported() end)
        pcall(function() mode = x:GetXeSSQualityMode() end)
    end
    pcall(function() screen = ksl:GetConsoleVariableFloatValue('r.ScreenPercentage') end)
    return string.format('supported=%s;mode=%s;screen=%s', tostring(supported), tostring(mode), tostring(screen))
end
"""
def _parse_xess(text):
    out = {}
    for part in (text or "").strip().split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            v = v.strip()
            out[k.strip()] = True if v == "true" else False if v == "false" else (
                float(v) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else None)
    return out
def xess_status(timeout=15):
    """{supported, mode, screen} from Intel's XeSS plugin (runs on AMD too)."""
    return _parse_xess(bc.run_lua(_xess_lua_header() + "return status()", timeout=timeout))
def set_xess_mode(mode, timeout=15):
    """Switch XeSS to an EXeSSQualityMode (0 = off). Returns the status read back."""
    mode = int(mode)
    lua = _xess_lua_header() + f"""
if not have() then return 'supported=false;mode=-1;screen=-1' end
pcall(function() x:SetXeSSQualityMode({mode}) end)
return status()
"""
    return _parse_xess(bc.run_lua(lua, timeout=timeout))
def set_amd_mode(key, timeout=15):
    """Put the non-NVIDIA upscaler on one rung of AMD_MODES. Tries FSR, then
    XeSS. Returns {which, label, screen, ...}; which is 'fsr', 'xess' or None."""
    row = next((r for r in AMD_MODES if r[0] == key), None)
    if row is None:
        raise ValueError(f"Unknown upscaler rung {key!r}")
    _key, label, _scale, fsr_mode, xess_mode = row
    out = {"rung": key, "label": label, "which": None}
    if key == "off":
        # Off has to turn BOTH off: stopping at the first success left XeSS
        # still upscaling while the button claimed it was done (2026-09-20).
        try:
            set_fsr_mode(0, timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
        out.update(set_xess_mode(0, timeout=timeout))
        out["which"] = "off"
        return out
    try:
        fsr = set_fsr_mode(fsr_mode, timeout=timeout)
        if fsr.get("took") and fsr.get("enabled"):
            out.update(fsr)
            out["which"] = "fsr"
            return out
    except Exception:  # noqa: BLE001 -- FSR failing is the normal case here
        pass
    xess = set_xess_mode(xess_mode, timeout=timeout)
    out.update(xess)
    if xess.get("supported") and xess.get("mode") == xess_mode:
        out["which"] = "xess"
    return out
def set_rhi_thread(enable, timeout=15):
    """Run the engine's r.RHIThread.Enable console COMMAND live (1 = on, 0 = off).
    There is no variable to read back, so the result only says the command was
    issued; Measure FPS is the check. The same switch at start-up is the
    -rhithread / -norhithread launch option, never an Engine.ini line."""
    value = 1 if enable else 0
    lua = f"""
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local ok, err = pcall(function() ksl:ExecuteConsoleCommand(UEHelpers.GetWorld(), 'r.RHIThread.Enable {value}', UEHelpers.GetPlayerController()) end)
return ok and 'r.RHIThread.Enable {value} issued (a command, nothing to read back: Measure FPS to compare)' or ('Could not issue the command: ' .. tostring(err))
"""
    return bc.run_lua(lua, timeout=timeout)
def collect_garbage(timeout=30):
    """Run the engine's garbage collector now (frees dead objects; a short hitch)."""
    lua = """
local ksl = StaticFindObject('/Script/Engine.Default__KismetSystemLibrary')
local ok, err = pcall(function() ksl:CollectGarbage() end)
return ok and 'Garbage collection requested' or ('Could not run CollectGarbage: ' .. tostring(err))
"""
    return bc.run_lua(lua, timeout=timeout)
GAME_PROCESS = "Bodycam-Win64-Shipping.exe"
_PRIORITY_CLASSES = {"high": 0x00000080, "above": 0x00008000, "normal": 0x00000020, "below": 0x00004000}
def _kernel32():
    """kernel32 with 64-bit-safe handle signatures: without restype the pseudo
    handle from GetCurrentProcess comes back truncated and SetPriorityClass fails."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32
def _find_pids(image_name):
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image_name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids
def set_game_priority(level="high"):
    """Set the game process's Windows priority class ("high", "above", "normal").
    Returns a result line. Needs no admin for a process of the same user."""
    import ctypes
    cls = _PRIORITY_CLASSES[level]
    pids = _find_pids(GAME_PROCESS)
    if not pids:
        return f"{GAME_PROCESS} is not running"
    k32 = _kernel32()
    done = 0
    for pid in pids:
        h = k32.OpenProcess(0x0200 | 0x0400, False, pid)   # PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION
        if h:
            if k32.SetPriorityClass(h, cls):
                done += 1
            k32.CloseHandle(h)
    return f"Game process priority set to {level} on {done} process(es)" if done else "Could not change the game's priority (access denied?)"
def set_own_priority(level="below"):
    """Priority class of this overlay itself; "below" keeps it out of the game's way."""
    k32 = _kernel32()
    ok = k32.SetPriorityClass(k32.GetCurrentProcess(), _PRIORITY_CLASSES[level])
    return f"Overlay priority set to {level}" if ok else "Could not change the overlay's priority"
def startup_ini_path():
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Bodycam", "Saved", "Config", "Windows", "Engine.ini")
def read_startup_settings(path=None):
    """{cvar: value} for STARTUP_SETTINGS keys currently under [SystemSettings]."""
    path = path or startup_ini_path()
    found = {}
    if not os.path.exists(path):
        return found
    section = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("["):
                section = stripped
                continue
            if section == STARTUP_SECTION and "=" in stripped and not stripped.startswith(";"):
                key, value = stripped.split("=", 1)
                if key.strip() in dict(STARTUP_SETTINGS) or key.strip() in STARTUP_REMOVE_KEYS:
                    found[key.strip()] = value.split(";")[0].strip()
    return found
def write_startup_settings(enable, path=None):
    """Add (enable=True) or remove (False) the STARTUP_SETTINGS lines under
    [SystemSettings] in the game's Engine.ini, after a timestamped backup next
    to it. Returns the result line; the game must be restarted to pick it up."""
    import time as _time
    path = path or startup_ini_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Engine.ini not found at {path}")
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    backup = f"{path}.bdt-backup-{_time.strftime('%Y%m%d-%H%M%S')}-{_time.time_ns() % 1_000_000:06d}"
    shutil.copy2(path, backup)
    keys = dict(STARTUP_SETTINGS)
    keys.update({k: None for k in STARTUP_REMOVE_KEYS})     # always dropped, never re-added
    out, section, inserted, last_kv = [], None, False, None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if section == STARTUP_SECTION and enable and not inserted:
                # right after the section's last key=value line, before any
                # banner comments that belong to the next section
                at = (last_kv + 1) if last_kv is not None else len(out)
                out[at:at] = [f"{k}={v}" for k, v in STARTUP_SETTINGS]
                inserted = True
            section = stripped
            out.append(line)
            continue
        if section == STARTUP_SECTION and "=" in stripped and stripped.split("=", 1)[0].strip() in keys:
            continue            # drop the old line; re-added below when enabling
        out.append(line)
        if section == STARTUP_SECTION and "=" in stripped and not stripped.startswith(";"):
            last_kv = len(out) - 1
    if enable and not inserted:
        if section != STARTUP_SECTION:
            if out and out[-1].strip():
                out.append("")
            out.append(STARTUP_SECTION)
        out.extend(f"{k}={v}" for k, v in STARTUP_SETTINGS)
    # Optimization packs tell people to mark Engine.ini read-only so the game
    # cannot rewrite it (the user's is); lift the attribute for the write and
    # put it back, so the file stays protected the way they left it.
    import stat as _stat
    mode = os.stat(path).st_mode
    read_only = not (mode & _stat.S_IWRITE)
    if read_only:
        os.chmod(path, mode | _stat.S_IWRITE)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(out))
    finally:
        if read_only:
            os.chmod(path, mode)
    what = "written to" if enable else "removed from"
    kept = " The file's read-only flag was kept." if read_only else ""
    return f"Startup settings {what} Engine.ini (backup: {os.path.basename(backup)}).{kept} Restart Bodycam for it to take effect."
