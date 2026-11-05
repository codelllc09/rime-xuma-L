-- menu_proc.lua —— /menu 菜單按鍵處理器（processor）
--
-- 職責：input=='/menu' 時攔截選鍵（數字 1-9、空格/回車首選、分號次選、引號三選
--       ——與 key_binder 的 has_menu 映射對齊），按 menu_state.menu 執行：
--         一級 → 進入二級（清稿後 push_input 重拉菜單）
--         二級 → set_option 即時生效，清稿退出
--       Esc/⌫ 在二級＝返回一級，在一級放行（下游取消菜單）。其餘按鍵一律放行。

local S = require('codelllc09/menu_state')

-- 本機 librime 1.16：kRejected=0, kAccepted=1, kNoop=2（同 add_word_proc）
local K_NOOP, K_ACCEPTED = 2, 1

local function processor(key, env)
  local ctx = env.engine.context
  if ctx.input ~= '/menu' then return K_NOOP end

  local r = key:repr()
  if r:find('Release', 1, true) then return K_NOOP end
  if r:find('Control', 1, true) or r:find('Alt', 1, true) then return K_NOOP end
  local parts = {}
  for t in r:gmatch('[^+]+') do parts[#parts + 1] = t end
  local keyname = parts[#parts]
  if keyname:match('_[LR]$') or keyname == 'Caps_Lock' then return K_NOOP end

  if keyname == 'Escape' or keyname == 'BackSpace' then
    if S.category then
      S.category = nil
      ctx:clear()
      ctx:push_input('/menu')   -- 回到一級
      return K_ACCEPTED
    end
    return K_NOOP   -- 一級：交下游取消菜單
  end

  local idx
  if keyname == 'space' or keyname == 'Return' or keyname == 'KP_Enter' then idx = 1
  elseif keyname:match('^%d$') then idx = tonumber(keyname)
  elseif keyname == 'semicolon' then idx = 2
  elseif keyname == 'apostrophe' then idx = 3
  else return K_NOOP end
  if not idx or idx == 0 or idx > #S.menu then return K_NOOP end

  local item = S.menu[idx]
  if item.value then
    S.apply(S.groups[item.group_idx], item.value, ctx)
    S.category = nil
    ctx:clear()   -- 選中即生效即退出（打字優先）
    return K_ACCEPTED
  end
  S.category = item.group_idx
  ctx:clear()
  ctx:push_input('/menu')   -- 重拉菜單進二級
  return K_ACCEPTED
end

return processor
