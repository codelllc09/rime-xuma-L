-- no_simple.lua · 非簡模式（開關 no_simple，默認開）
-- 兩級攔截：
--   ranges      = 主攔截集（任何場合攔，含單字）→ 蕭/龍/車 等
--   word_ranges = 全量簡體形（組詞時攔）→ 東/國/愛/溫/跡 等
--
-- 單字：查 ranges → 主攔截集裡的攔，白名單放行的可打
-- 組詞：查 word_ranges → 任何簡體形參與組詞即攔
local data = require 'codelllc09/no_simple_data'
local ranges = data.ranges
local word_ranges = data.word_ranges

local word_block = { ['叶子'] = true }

local function in_ranges(cp, r)
  local lo, hi = 1, math.floor(#r / 2)
  while lo <= hi do
    local mid = math.floor((lo + hi) / 2)
    local s, e = r[mid * 2 - 1], r[mid * 2]
    if cp < s then hi = mid - 1
    elseif cp > e then lo = mid + 1
    else return true end
  end
  return false
end

local function each_cp(text, f)
  local i, n = 1, #text
  while i <= n do
    local b = text:byte(i)
    local cp, len
    if b < 0x80 then cp, len = b, 1
    elseif b < 0xC0 then return
    elseif b < 0xE0 then
      local b2 = text:byte(i + 1)
      if not b2 then return end  -- 截断序列：中止扫描（fail-open，候选放行），勿对 nil 做算术
      cp, len = (b - 0xC0) * 64 + b2 - 128, 2
    elseif b < 0xF0 then
      local b2, b3 = text:byte(i + 1, i + 2)
      if not b3 then return end
      cp, len = (b - 0xE0) * 4096 + (b2 - 128) * 64 + b3 - 128, 3
    else
      local b2, b3, b4 = text:byte(i + 1, i + 3)
      if not b4 then return end
      cp, len = ((b - 0xF0) * 64 + b2 - 128) * 4096
        + (b3 - 128) * 64 + b4 - 128, 4
    end
    f(cp)
    i = i + len
  end
end

local function filter(input, env)
  if not env.engine.context:get_option('no_simple') then
    for cand in input:iter() do yield(cand) end
    return
  end
  for cand in input:iter() do
    local text = cand.text
    if not word_block[text] then
      local is_single = #text <= 3 -- UTF-8 BMP 單字 ≤3 字節
      local hit = false
      each_cp(text, function(cp)
        if is_single then
          if in_ranges(cp, ranges) then hit = true end
        else
          if in_ranges(cp, word_ranges) then hit = true end
        end
      end)
      if not hit then yield(cand) end
    end
  end
end

return { func = filter }
