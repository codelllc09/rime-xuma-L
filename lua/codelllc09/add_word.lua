-- add_word.lua —— /add 加詞採集菜單（translator）
--
-- 交互（全程候選欄可見，無隱式狀態）：
--   逐字打出缺詞上屏（最近 4 字內）→ 敲 /add → 尾部連綴候選（4/3/2 字降序，人眼即審）
--   → 選鍵收錄進 spool → 攢批後選「入庫重跑」固化（handpicked ##日收 → 詞頻管道 → 部署）。
--   詞形＝實際上屏的繁體形（WYSIWYG）；含簡形候選自帶 ⚠ 警示；
--   詞頻由管道 t2s 後從十源語料查取，本模組不管頻次。

local state = require('codelllc09/add_state')
local ns_data = require('codelllc09/no_simple_data')

local function user_dir()
  local ok, d = pcall(function() return rime_api.get_user_dir() end)
  if ok and d and d ~= '' then return d end
  return (os.getenv('XDG_CONFIG_HOME') or (os.getenv('HOME') .. '/.config')) .. '/ibus/rime'
end

local SPOOL = user_dir() .. '/lua/codelllc09/tools/dict_pipeline/spool_pending.txt'

local function is_hanzi(w)
  if w == '' then return false end
  for _, c in utf8.codes(w) do
    if not ((c >= 0x3400 and c <= 0x4DBF) or (c >= 0x4E00 and c <= 0x9FFF) or
            (c >= 0xF900 and c <= 0xFAFF) or (c >= 0x20000 and c <= 0x2A6DF)) then
      return false
    end
  end
  return true
end

local function char_len(s)
  local n = 0
  for _ in utf8.codes(s) do n = n + 1 end
  return n
end

local function suffix(s, n)
  local len = char_len(s)
  if n >= len then return s end
  local skip, i = len - n, 0
  for p, _ in utf8.codes(s) do
    i = i + 1
    if i == skip + 1 then return s:sub(p) end
  end
  return s
end

local function spool_set()
  local set = {}
  local f = io.open(SPOOL, 'r')
  if f then
    for l in f:lines() do
      l = l:match('^%s*(%S+)')
      if l then set[l] = true end
    end
    f:close()
  end
  return set
end

-- 簡形檢測（同 no_simple 的二分查找，word_ranges 全量簡形集）
local function has_simp(w)
  local r = ns_data.word_ranges
  local i, n = 1, #w
  while i <= n do
    local b = w:byte(i)
    local cp, len
    if b < 0x80 then cp, len = b, 1
    elseif b < 0xC0 then return false
    elseif b < 0xE0 then cp, len = (b - 0xC0) * 64 + w:byte(i + 1) - 128, 2
    elseif b < 0xF0 then cp, len = (b - 0xE0) * 4096 + (w:byte(i + 1) - 128) * 64 + w:byte(i + 2) - 128, 3
    else cp, len = ((b - 0xF0) * 64 + w:byte(i + 1) - 128) * 4096 + (w:byte(i + 2) - 128) * 64 + w:byte(i + 3) - 128, 4 end
    local lo, hi = 1, math.floor(#r / 2)
    while lo <= hi do
      local mid = math.floor((lo + hi) / 2)
      local s, e = r[mid * 2 - 1], r[mid * 2]
      if cp < s then hi = mid - 1
      elseif cp > e then lo = mid + 1
      else return true end
    end
    i = i + len
  end
  return false
end

-- librime-lua 官方用法：to_table()（舊→新）；記錄字段 .text/.type
local function history_texts(ctx)
  local out = {}
  local ok, tbl = pcall(function() return ctx.commit_history:to_table() end)
  if ok and type(tbl) == 'table' then
    for _, r in ipairs(tbl) do
      local t = ''
      pcall(function() t = tostring(r.text or '') end)
      out[#out + 1] = t
    end
    return out
  end
  pcall(function()
    local tmp = {}
    for _, cr in ctx.commit_history:iter() do
      local t = ''
      pcall(function() t = tostring(cr.text or '') end)
      tmp[#tmp + 1] = t
    end
    for i = #tmp, 1, -1 do out[#out + 1] = tmp[i] end
  end)
  return out
end

-- 尾部連續純漢字上屏串（單條 ≤2 字，回看至多 4 字）
local function tail_run(ctx)
  local recs = history_texts(ctx)
  local acc = ''
  for i = #recs, 1, -1 do
    local t = recs[i]
    if t == '' or not is_hanzi(t) or #t > 6 then break end
    acc = t .. acc
    if char_len(acc) >= 4 then break end
  end
  return acc
end

local function translator(input, seg, env)
  if input ~= '/add' then return end
  local ctx = env.engine.context
  state.menu = {}
  local seen = spool_set()
  local budget = 7   -- menu/page_size，防翻頁導致選鍵索引錯位

  -- 動作後一次性橫幅
  if state.banner then
    state.menu[#state.menu + 1] = { kind = 'noop', text = state.banner }
    local c = Candidate('add', seg.start, seg._end, state.banner, '')
    c.preedit = input .. '\t加詞'
    yield(c)
    state.banner = nil
  end

  -- 尾部連綴詞候選（4/3/2 字降序）
  local acc = tail_run(ctx)
  local any_word = false
  for _, L in ipairs({ 4, 3, 2 }) do
    if #state.menu >= budget - 3 then break end
    if L <= char_len(acc) then
      local w = suffix(acc, L)
      if not seen[w] then
        any_word = true
        state.menu[#state.menu + 1] = { kind = 'word', text = w }
        local c = Candidate('add', seg.start, seg._end, w,
          ' ' .. L .. '字·收錄' .. (has_simp(w) and ' ⚠含簡形' or ''))
        c.preedit = input .. '\t加詞'
        yield(c)
      end
    end
  end

  -- 無詞可收時首位給指引（可選中關閉）
  if not any_word and #state.menu == 0 then
    state.menu[#state.menu + 1] = { kind = 'noop', text = '打出缺詞·再敲 /add' }
    local c = Candidate('add', seg.start, seg._end, '打出缺詞·再敲 /add', ' 最近 4 字連綴會列在這裏')
    c.preedit = input .. '\t加詞'
    yield(c)
  end

  -- 動作項
  local n = 0
  for _ in pairs(seen) do n = n + 1 end
  state.menu[#state.menu + 1] = { kind = 'commit', text = '入庫重跑' }
  local c1 = Candidate('add', seg.start, seg._end, '入庫重跑', ' 按 s 直達（合併暫存·重跑詞頻·部署）')
  c1.preedit = input .. '\t加詞'
  yield(c1)
  state.menu[#state.menu + 1] = { kind = 'clear', text = '清空暫存' }
  local c2 = Candidate('add', seg.start, seg._end, '清空暫存', ' 按 x 直達（暫存 ' .. n .. ' 詞）')
  c2.preedit = input .. '\t加詞'
  yield(c2)
end

return translator
