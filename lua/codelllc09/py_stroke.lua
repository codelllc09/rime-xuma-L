-- py_stroke.lua · 拼音＋聲調＋筆畫組合反查（`pinyin[，。；']/[筆畫]）
-- 純函數模塊形式；注：ReverseDb/Memory/Candidate/log 均爲 librime-lua 全局對象，不 require
-- 聲調鍵：, =1聲  . =2聲  ; =3聲  ' =4聲（右手一排四鍵）
-- 分隔鍵：/ 進入筆畫模式（可省聲調直接 /
-- 僅支持單音節（單字）查詢；多音節形式（如 xi.an=西安）不在範圍
-- 例：`li, → li一聲；`li/hs → li∩橫豎；`li,/hs → li一聲∩橫豎 → 李
local mem, stroke_rvdb, code_rvdb, inited
local tone_db
local TONE = { [","] = "1", ["."] = "2", [";"] = "3", ["'"] = "4" }

local function init(env)
  if inited then return end
  inited = true
  local ok
  ok, mem = pcall(Memory, env.engine, env.engine.schema, "luna_pinyin_lookup")
  if not ok or not mem then
    ok, mem = pcall(Memory, env.engine, env.engine.schema)
  end
  mem = (ok and mem) or nil
  stroke_rvdb = ReverseDb("build/stroke.reverse.bin")
  local ns = env.engine.schema.config:get_string("lua_reverse_db/code") or "xuma_L"
  code_rvdb = ReverseDb("build/" .. ns .. ".reverse.bin")
  log.info("py_stroke init: mem=" .. tostring(mem))
end

local function utf8len(s)
  local n, i = 0, 1
  while i <= #s do
    local b = s:byte(i)
    if b < 0x80 then i = i + 1
    elseif b < 0xE0 then i = i + 2
    elseif b < 0xF0 then i = i + 3
    else i = i + 4 end
    n = n + 1
  end
  return n
end

local function stroke_prefix_ok(text, prefix)
  local codes = stroke_rvdb:lookup(text)
  if not codes or codes == "" then return false end
  for code in codes:gmatch("%S+") do
    if code:sub(1, #prefix) == prefix then return true end
  end
  return false
end

local function xuma_comment(text)
  local codes = code_rvdb:lookup(text)
  if not codes or codes == "" then return "" end
  local best = nil
  for code in codes:gmatch("%S+") do
    if #code <= 4 and code:match("^[a-z]+$") then
      if not best or #code > #best then best = code end
    end
  end
  return best or ""
end

local function py_stroke(input, seg, env)
  log.info("py_stroke called: input=" .. tostring(input) .. " has_tag=" .. tostring(seg:has_tag("pystroke")))
  if not seg:has_tag("pystroke") then return end
  init(env)
  if not mem then return end
  local py, sep, sk = input:match("^%`?([a-z']+)([,.;']?/?)([hspnz]*)$")
  if not py or py == "" then return end
  -- lua pattern 不回溯：音節組會吞入尾部「聲調'+筆畫」結構，此處統一重解析
  -- （僅當無聲調符或僅 / 分隔時；核心：剝出最後一個 ' 及其後純筆畫尾段）
  if sep == "" or sep == "/" then
    local core, tail = py:match("^(.-)'([hspnz]*)$")
    if core and core ~= "" and py ~= core then
      py = core
      sep = "'" .. sep
      if tail ~= "" then sk = tail end
    end
  end
  if sep == "" and sk == "" then return end   -- 純拼音交回原通道
  local tone = TONE[sep:sub(1, 1)] or nil       -- 取聲調（/ 開頭則無調）
  log.info("py_stroke query: py=" .. py .. " tone=" .. tostring(tone) .. " sk=" .. sk)
  if tone and not tone_db then
    local ok, m = pcall(require, "codelllc09/pinyin_tone")
    if ok then tone_db = m end
  end
  if tone and not tone_db then return end
  local ok, iter = pcall(function() return mem:dictiter_lookup(py, false, 300) end)
  if not ok or not iter then
    log.info("py_stroke: dictiter_lookup failed: " .. tostring(iter))
    return
  end
  local n = 0
  local emit = 0
  local function pass(ch)
    if sk ~= "" and not stroke_prefix_ok(ch, sk) then return false end
    if tone then
      local readings = tone_db[ch]
      if not readings then return false end
      local target = py .. tone
      local hit = false
      for r in readings:gmatch("[^/]+") do
        if r == target then hit = true; break end
      end
      if not hit then return false end
    end
    return true
  end
  local okf, errf = pcall(function()
    for entry in iter:iter() do
      n = n + 1
      local t = entry and entry.text
      if t and utf8len(t) == 1 and pass(t) then
        local ok2, c = pcall(Candidate, "pystroke", seg.start, seg._end, t, xuma_comment(t))
        if ok2 and c then
          c.quality = 100.0 + ((entry.weight and entry.weight > 0) and entry.weight / 1e9 or 0)
          yield(c)
          emit = emit + 1
        end
      end
    end
  end)
  if not okf then log.info("py_stroke iter error: " .. tostring(errf)) end
  log.info("py_stroke: scanned " .. n .. " entries, emitted " .. emit)
end

return py_stroke
