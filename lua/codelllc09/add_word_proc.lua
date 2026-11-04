-- add_word_proc.lua —— /add 菜單按鍵處理器（processor）
--
-- 職責：input=='/add' 時攔截選鍵（數字 1-9、空格首選、分號次選、引號三選——
--       與 key_binder 的 has_menu 映射對齊），按 add_state.menu 執行動作後清空組稿，
--       不讓選中內容作爲文本上屏。其餘按鍵一律放行（Esc 交給下游取消菜單）。

local state = require('codelllc09/add_state')

-- 本機 librime 1.16：kRejected=0, kAccepted=1, kNoop=2（librime-lua 未暴露 k* 全局量，
-- 與 mode_hint.lua 的 `return 2 -- kNoop` 同源）
local K_NOOP, K_ACCEPTED = 2, 1

local function user_dir()
  local ok, d = pcall(function() return rime_api.get_user_dir() end)
  if ok and d and d ~= '' then return d end
  return (os.getenv('XDG_CONFIG_HOME') or (os.getenv('HOME') .. '/.config')) .. '/ibus/rime'
end

local SPOOL  = user_dir() .. '/lua/codelllc09/tools/dict_pipeline/spool_pending.txt'
local COMMIT = user_dir() .. '/lua/codelllc09/tools/handpick_commit.sh'

local function append_spool(w)
  local seen = {}
  local f = io.open(SPOOL, 'r')
  if f then
    for l in f:lines() do
      l = l:match('^%s*(%S+)')
      if l then seen[l] = true end
    end
    f:close()
  end
  if seen[w] then return false end
  local g = io.open(SPOOL, 'a')
  if not g then return false end
  g:write(w .. '\n')
  g:close()
  return true
end

local function processor(key, env)
  local ctx = env.engine.context
  if ctx.input ~= '/add' then return K_NOOP end

  -- 本機 librime-lua 的 KeyEvent 惯用法（同 mode_hint）：解析 repr 字符串
  local r = key:repr()                      -- 形如 "Shift+Release+space"
  if r:find('Release', 1, true) then return K_NOOP end
  if r:find('Control', 1, true) or r:find('Alt', 1, true) then return K_NOOP end
  local parts = {}
  for t in r:gmatch('[^+]+') do parts[#parts + 1] = t end
  local keyname = parts[#parts]
  if keyname:match('_[LR]$') or keyname == 'Caps_Lock' then return K_NOOP end

  local idx
  if keyname == 'space' then idx = 1                    -- 空格＝首選
  elseif keyname:match('^%d$') then idx = tonumber(keyname)  -- 數字
  elseif keyname == 'semicolon' then idx = 2            -- 分號＝次選（對齊 key_binder）
  elseif keyname == 'apostrophe' then idx = 3           -- 引號＝三選（對齊 key_binder）
  else return K_NOOP
  end
  if not idx or idx == 0 then return K_NOOP end

  local item = state.menu[idx]
  if not item then return K_NOOP end

  if item.kind == 'word' then
    if append_spool(item.text) then
      state.banner = '✓ ' .. item.text .. ' 已入暫存'
    else
      state.banner = '✗ ' .. item.text .. ' 暫存寫入失敗（或已在暫存）'
    end
  elseif item.kind == 'clear' then
    io.open(SPOOL, 'w'):close()
    state.banner = '暫存已清空'
  elseif item.kind == 'commit' then
    -- 非阻塞後臺執行：合併暫存 → 重跑詞頻管道 → 部署（約 1 分鐘，引擎會重啓）
    os.execute('nohup "' .. COMMIT .. '" >>"' .. user_dir() ..
               '/lua/codelllc09/tools/dict_pipeline/log/add_commit.log" 2>&1 &')
    state.banner = '入庫重跑已啓動（約 1 分鐘，輸入法將自動重啓）'
  else -- noop：橫幅/指引項，選中即關閉
    ctx:clear()
    return K_ACCEPTED
  end

  ctx:clear()
  ctx:push_input('/add')   -- 重新拉起菜單展示橫幅與更新後列表
  return K_ACCEPTED
end

return processor
