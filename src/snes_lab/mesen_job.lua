-- snes-lab's Mesen runner. Mesen runs it with --testrunner; the job is a Lua file named by
-- $SNES_LAB_JOB (written by mesen.py). Results go to job.out as tab-separated lines:
--   hit <watch> <frame> <address> <value> <k> <pc> <ps> <a> <x> <y> <sp> <d> <dbr> <emu>
--   count <watch> <hits>   read <index> <hex>   progress <frame>   done <frame> <frameCount>
-- Savestates can only be made or loaded inside an exec callback on the main CPU.

local job = dofile(os.getenv("SNES_LAB_JOB"))
local out = assert(io.open(job.out, "w"))
local function emit(...)
  local parts = { ... }
  for i = 1, #parts do parts[i] = tostring(parts[i]) end
  out:write(table.concat(parts, "\t"), "\n")
end

local frame = 0
local main_cpu = emu.cpuType.snes
local whole = { 0, 0xFFFFFF, main_cpu, emu.memType.snesMemory }

-- Inputs: job.inputs = { {frames, {right=true, b=true}}, ... } played from frame 0.
local schedule, last = {}, 0
for _, step in ipairs(job.inputs) do
  last = last + step[1]
  schedule[#schedule + 1] = { last, step[2] }
end
local function buttons_now()
  for _, s in ipairs(schedule) do
    if frame < s[1] then return s[2] end
  end
  return {}
end
emu.addEventCallback(function() emu.setInput(buttons_now(), 0) end, emu.eventType.inputPolled)

-- The main CPU's state is flat (pc, k, ...); the SA-1's is the whole chip with "cpu." in front.
local function reg(s, name)
  local v = s[name]
  if v == nil then v = s["cpu." .. name] end
  if type(v) == "boolean" then return v and 1 or 0 end
  return v or 0
end

local counts = {}
for i, w in ipairs(job.watches) do
  counts[i] = 0
  local cpu = emu.cpuType[w.cpu]
  emu.addMemoryCallback(function(address, value)
    if w.value ~= nil and value ~= w.value then return end
    counts[i] = counts[i] + 1
    if counts[i] <= job.limit then
      local s = emu.getCpuState(cpu)
      emit("hit", i, frame, address, value or -1, reg(s, "k"), reg(s, "pc"), reg(s, "ps"), reg(s, "a"),
        reg(s, "x"), reg(s, "y"), reg(s, "sp"), reg(s, "d"), reg(s, "dbr"), reg(s, "emulationMode"))
    end
  end, emu.callbackType[w.kind], w.start, w.stop, cpu, emu.memType[w.memory])
end

local function once_in_exec(fn)
  local ref
  ref = emu.addMemoryCallback(function()
    if ref == nil then return end
    local r = ref
    ref = nil
    emu.removeMemoryCallback(r, emu.callbackType.exec, whole[1], whole[2], whole[3], whole[4])
    fn()
  end, emu.callbackType.exec, whole[1], whole[2], whole[3], whole[4])
end

if job.state_in then
  local f = assert(io.open(job.state_in, "rb"))
  local blob = f:read("a")
  f:close()
  once_in_exec(function() emu.loadSavestate(blob) end)
end

local function write_cdl()
  local cdl = emu.getCdlData(emu.memType.snesPrgRom)
  local f = assert(io.open(job.cdl, "wb"))
  local n = emu.getMemorySize(emu.memType.snesPrgRom)
  local chunk = {}
  for i = 0, n - 1 do
    chunk[#chunk + 1] = string.char(cdl[i] or 0)
    if #chunk == 4096 then f:write(table.concat(chunk)) chunk = {} end
  end
  f:write(table.concat(chunk))
  f:close()
end

local function finish()
  for i = 1, #job.watches do emit("count", i, counts[i]) end
  for i, r in ipairs(job.reads) do
    local bytes = {}
    for a = r[1], r[1] + r[2] - 1 do bytes[#bytes + 1] = string.format("%02x", emu.read(a, emu.memType[r[3]])) end
    emit("read", i, table.concat(bytes))
  end
  if job.cdl then write_cdl() end
  if job.shot then
    local f = assert(io.open(job.shot, "wb"))
    f:write(emu.takeScreenshot())
    f:close()
  end
  emit("done", frame, emu.getState().frameCount)
  out:close()
  emu.stop(0)
end

local finishing = false
emu.addEventCallback(function()
  if finishing then return end
  frame = frame + 1
  if frame % 60 == 0 then emit("progress", frame) out:flush() end
  if frame >= job.frames then
    finishing = true
    if job.state_out then
      once_in_exec(function()
        local f = assert(io.open(job.state_out, "wb"))
        f:write(emu.createSavestate())
        f:close()
        finish()
      end)
    else
      finish()
    end
  end
end, emu.eventType.endFrame)
