-- rvlk_code.lua · 反查候選注釋統一：只顯示該字在徐碼典中的最長碼（全碼）
-- 全碼不定位數（樹 ijxj 4位、貝 nbg 3位）：取字的所有碼中字符數最多者。
-- 作用於 pystroke / luna_pinyin_lookup / stroke_lookup / reverse_lookup 各反查 tag。
local code_rvdb

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

local function longest_code(text)
  local codes = code_rvdb:lookup(text)
  if not codes or codes == "" then return nil end
  local best = nil
  for code in codes:gmatch("%S+") do
    if #code <= 4 and code:match("^[a-z]+$") then
      if not best or #code > #best then best = code end
    end
  end
  return best
end

local function filter(input, env)
  local ctx = env.engine.context
  local comp = ctx.composition
  local seg = comp and comp:back()
  local is_rvlk = seg and (seg:has_tag("pystroke")
    or seg:has_tag("luna_pinyin_lookup")
    or seg:has_tag("stroke_lookup")
    or seg:has_tag("reverse_lookup"))
  if not is_rvlk then
    for cand in input:iter() do yield(cand) end
    return
  end
  if not code_rvdb then
    local ns = env.engine.schema.config:get_string("lua_reverse_db/code") or "xuma_L"
    code_rvdb = ReverseDb("build/" .. ns .. ".reverse.bin")
  end
  for cand in input:iter() do
    local t = cand.text
    if t and utf8len(t) == 1 then
      local best = longest_code(t)
      if best then
        local g = cand:get_genuine()
        g.comment = best
      end
    end
    yield(cand)
  end
end

return filter
