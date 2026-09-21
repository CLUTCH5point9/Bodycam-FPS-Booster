--[[
  ClaudeBridge -- file-based RPC into a live UE4SS-modded game.
  Protocol, safety rationale, and the Python side: see docs/DOCUMENTATION.md
  section 5.1 in the repo root.

  Install:
    <game>\Binaries\Win64\ue4ss\Mods\ClaudeBridge\Scripts\main.lua
    add "ClaudeBridge : 1" to ue4ss\Mods\mods.txt
    restart the game (a NEW mod folder is not picked up by CTRL+R)
]]

local UEHelpers = require("UEHelpers")

local NAME = "bodycam"          -- change per game; just names the temp folder
local DIR  = os.getenv("LOCALAPPDATA") .. "\\Temp\\" .. NAME .. "_bridge"
local REQ  = DIR .. "\\req.txt"
local RESP = DIR .. "\\resp.txt"
local TMP  = DIR .. "\\resp.tmp"
local LOG  = DIR .. "\\bridge.log"

local lastId, busy = nil, false
-- Watchdog (2026-09-18): a request whose game-thread callback never ran
-- (UE4SS logged "[Lua::Registry::get_function_ref] Ref was not function"
-- once and the callback was dropped) left busy = true for the rest of the
-- session, so the pump never polled again and every later request waited
-- forever. After WATCHDOG_SECONDS the stuck request is answered with an
-- error and the pump carries on. A deploy of thousands of actors runs in
-- one request, so the limit is generous.
local WATCHDOG_SECONDS = 120
local busySince = 0

os.execute('mkdir "' .. DIR .. '" 2>nul')

local function note(s)
    print("[ClaudeBridge] " .. s .. "\n")
    local f = io.open(LOG, "a")
    if f then f:write(s .. "\n") f:close() end
end

local function readAll(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local d = f:read("*a")
    f:close()
    return d
end

local function writeResp(id, ok, body)
    local f = io.open(TMP, "wb")
    if not f then note("cannot open resp.tmp") return end
    f:write(tostring(id) .. "\n" .. (ok and "OK" or "ERR") .. "\n" .. body)
    f:close()
    os.remove(RESP)
    os.rename(TMP, RESP)
end

---------------------------------------------------------------------------
-- helpers injected into every payload
---------------------------------------------------------------------------

local function isValid(o)
    if o == nil then return false end
    local ok, v = pcall(function() return o:IsValid() end)
    return ok and v == true
end

local function render(v, depth, seen)
    depth, seen = depth or 0, seen or {}
    local t = type(v)
    if t == "userdata" then
        local s
        if pcall(function() s = v:GetFullName() end) and s then return s end
        return "<userdata>"
    elseif t == "table" then
        if depth > 3 then return "{...}" end
        if seen[v] then return "<cycle>" end
        seen[v] = true
        local parts = {}
        for k, val in pairs(v) do
            parts[#parts + 1] = tostring(k) .. "=" .. render(val, depth + 1, seen)
            if #parts > 60 then parts[#parts + 1] = "..." break end
        end
        return "{" .. table.concat(parts, ", ") .. "}"
    end
    return tostring(v)
end

local function getPawn()
    local p = UEHelpers.GetPlayerController()
    if isValid(p) then
        local ok, v = pcall(function() return p.Pawn end)
        if ok and isValid(v) then return v end
        ok, v = pcall(function() return p.AcknowledgedPawn end)
        if ok and isValid(v) then return v end
    end
    local ok, v = pcall(function() return UEHelpers.GetPlayer() end)
    return (ok and isValid(v)) and v or nil
end

--- Walk the class chain. ForEachProperty/ForEachFunction only report
--- OWN-class members, so without this every inherited field looks missing.
local function chainOf(o)
    local chain, c = {}, o:GetClass()
    while isValid(c) do
        chain[#chain + 1] = c
        local nxt; local ok = pcall(function() nxt = c:GetSuperStruct() end)
        if not ok or not isValid(nxt) then break end
        c = nxt
        if #chain > 10 then break end
    end
    return chain
end

-- Reading Struct/Array/Map/Set values marshals a pointer and can hard-crash.
local SAFE = {
    IntProperty = true, Int64Property = true, UInt32Property = true,
    FloatProperty = true, DoubleProperty = true, BoolProperty = true,
    ByteProperty = true, EnumProperty = true, NameProperty = true,
    ObjectProperty = true, ClassProperty = true,
}

local function props(obj, stopAt)
    obj = obj or getPawn()
    if not isValid(obj) then return "props: invalid object" end
    local out, seen = {}, {}
    pcall(function() out[#out + 1] = "== " .. obj:GetClass():GetFullName() end)
    for _, k in ipairs(chainOf(obj)) do
        local kn = ""; pcall(function() kn = k:GetFName():ToString() end)
        if kn == "Object" or (stopAt and kn == stopAt) then break end
        pcall(function()
            k:ForEachProperty(function(p)
                local n = p:GetFName():ToString()
                if seen[n] then return end
                seen[n] = true
                local ty = "?"
                pcall(function() ty = p:GetClass():GetFName():ToString() end)
                if ty:find("Delegate") then return end
                local shown = "(" .. ty .. ")"
                if SAFE[ty] then
                    local v; local got = pcall(function() v = obj[n] end)
                    if got then
                        if ty == "ObjectProperty" or ty == "ClassProperty" then
                            local s = "nil"
                            if isValid(v) then pcall(function() s = v:GetFName():ToString() end) end
                            shown = s
                        else shown = tostring(v) end
                    end
                end
                out[#out + 1] = string.format("  %-34s %-16s %s", n, ty, shown)
            end)
        end)
    end
    return table.concat(out, "\n")
end

local function funcs(obj, stopAt)
    obj = obj or getPawn()
    if not isValid(obj) then return "funcs: invalid object" end
    local out, seen = {}, {}
    pcall(function() out[#out + 1] = "== " .. obj:GetClass():GetFullName() end)
    for _, k in ipairs(chainOf(obj)) do
        local kn = ""; pcall(function() kn = k:GetFName():ToString() end)
        if stopAt and kn == stopAt then break end
        local got = pcall(function()
            k:ForEachFunction(function(f)
                local fn = f:GetFName():ToString()
                if seen[fn] then return end
                seen[fn] = true
                out[#out + 1] = "  " .. fn
            end)
        end)
        if not got then out[#out + 1] = "  (ForEachFunction unavailable on " .. kn .. ")" end
    end
    return table.concat(out, "\n")
end

local function count(clsName, limit)
    limit = limit or 20
    local out, n = {}, 0
    local ok, all = pcall(function() return FindAllOf(clsName) end)
    if not ok or not all then return clsName .. ": none / not a known class" end
    for _, o in ipairs(all) do
        n = n + 1
        if n <= limit then
            local s; pcall(function() s = o:GetFullName() end)
            out[#out + 1] = "  " .. (s or "?")
        end
    end
    return string.format("%s: %d instance(s)\n%s", clsName, n, table.concat(out, "\n"))
end

--- Does this object really have that UFunction?
--- type() cannot tell: a real UFunction and UE4SS's TrivialObject placeholder
--- both index as "userdata". Only the stringification differs.
---   real    -> "UFunction: 00000125E33E3558"
---   missing -> "TrivialObject: 00000125E4899578"
local function has(obj, fnName)
    if not isValid(obj) then return false end
    local s = ""
    local ok = pcall(function() s = tostring(obj[fnName]) end)
    return ok and s:find("^UFunction:") ~= nil
end

---------------------------------------------------------------------------
-- request pump
---------------------------------------------------------------------------

local function handle(id, src)
    local buf, realPrint = {}, print

    local function cap(...)
        local parts = {}
        for i = 1, select("#", ...) do
            parts[#parts + 1] = render((select(i, ...)), 0)
        end
        buf[#buf + 1] = table.concat(parts, "\t")
    end

    local chunk, cerr = load(src, "bridge", "t")
    if not chunk then
        writeResp(id, false, "compile error: " .. tostring(cerr))
        busy = false
        return
    end

    ExecuteInGameThread(function()
        _G.print, _G.props, _G.funcs, _G.count = cap, props, funcs, count
        _G.render, _G.valid, _G.pawn, _G.chainOf, _G.has =
            render, isValid, getPawn, chainOf, has
        _G.UEHelpers = UEHelpers

        local ok, res = pcall(chunk)
        _G.print = realPrint

        local body = table.concat(buf, "\n")
        if ok then
            if res ~= nil then body = body .. "\n-- return: " .. render(res, 0) end
            writeResp(id, true, body)
            realPrint(string.format("[ClaudeBridge] req %s OK (%d line(s))\n", id, #buf))
        else
            writeResp(id, false, body .. "\n-- error: " .. tostring(res))
            realPrint(string.format("[ClaudeBridge] req %s ERROR: %s\n", id, tostring(res)))
        end
        busy = false
    end)
end

LoopAsync(200, function()
    if busy then
        if busySince > 0 and os.time() - busySince > WATCHDOG_SECONDS then
            note("watchdog: request " .. tostring(lastId) .. " never finished; pump released")
            pcall(writeResp, lastId, false, "-- error: the bridge watchdog gave up on this request after " .. WATCHDOG_SECONDS .. " s (the game-thread callback never ran)")
            busy = false
        end
        return false
    end
    local data = readAll(REQ)
    if not data then return false end
    local nl = data:find("\n")
    if not nl then return false end
    local id  = data:sub(1, nl - 1):gsub("%s+$", "")
    local src = data:sub(nl + 1)
    if id == lastId then return false end
    lastId, busy, busySince = id, true, os.time()
    os.remove(REQ)                  -- consume BEFORE running
    note("executing request " .. id)
    handle(id, src)
    return false
end)

note("ready -- polling " .. REQ)
