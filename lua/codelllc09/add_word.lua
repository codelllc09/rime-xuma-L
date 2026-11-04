-- add_word.lua · /add 加詞採集菜單（尾部連綴人審收詞）
-- 流程：打缺詞（候選被頂出的生詞留於連綴尾）→ 敲 /add 出菜單
--   → 選鍵收錄進 spool → 攢批後選「入庫重跑」固化（handpicked ##日收 → 詞頻管道 → 部署）。
-- 選鍵攔截（數字/空格/分號/引號）由 add_word_proc.lua 處理，選中即動作不上屏。
local SPOOL = rime_api.get_user_dir() .. '/lua/codelllc09/tools/dict_pipeline/spool_pending.txt'

local function spool_set()
  local s = {}
  local f = io.open(SPOOL, 'r')
  if f then
    for l in f:lines() do if l ~= '' then s[l] = true end end
    f:close()
  end
  return s
end

local function add(input, seg, env)
  local ctx = env.engine.context
  local tail = ctx.input:match('^/add(.*)$')
  if not tail then return end
  local word = tail:gsub('^%s+', ''):gsub('%s+$', '')
  local seen = spool_set()
  if word ~= '' and word:match('^[^\t]+$') then
    local label = seen[word] and '（已在採集隊列）' or ''
    local c = Candidate('add', seg, '/add' .. tail, '收錄：' .. word .. label, '加詞')
    c.quality = 9999
    yield(c)
  end
  local n = 0
  for _ in pairs(seen) do n = n + 1 end
  if n > 0 then
    local c2 = Candidate('add', seg, '/add' .. tail, '入庫重跑（採集 ' .. n .. ' 詞）', '加詞')
    c2.quality = 9998
    yield(c2)
  end
end
return add
