-- word_mode.lua -- 詞組長度過濾器，四檔切換
-- options: [word_all, word_124, word_12, single_char_only]
--   word_all          = 全詞組（不過濾）
--   word_124          = 僅單字/二字詞/四字詞
--   word_12           = 僅單字/二字詞（2026-11-05 新增檔，默認）
--   single_char_only  = 僅單字
-- 默認（無選項激活）= 全詞組

local function char_count(text)
  -- UTF-8 字符計數（兼容 Lua 5.1/5.4）
  local n = 0
  local i = 1
  while i <= #text do
    local b = text:byte(i)
    if b < 0x80 then i = i + 1
    elseif b < 0xE0 then i = i + 2
    elseif b < 0xF0 then i = i + 3
    else i = i + 4
    end
    n = n + 1
  end
  return n
end

local function filter(input, env)
  local ctx = env.engine.context
  -- punct/helper/os_env/add 段直通：符号组（/jt △○□…）、帮助菜单（help/）、环境变量（env/）、
  -- 加词菜单（/add 橫幅/動作項為 5-20 字符的功能文案，非詞組，長度過濾必誤殺）
  local comp = ctx.composition
  local seg = comp and comp:back()
  if seg and (seg:has_tag('punct') or seg:has_tag('helper') or seg:has_tag('os_env') or seg:has_tag('add') or seg:has_tag('menu')) then
    for cand in input:iter() do yield(cand) end
    return
  end
  if ctx:get_option('word_all') then
    for cand in input:iter() do yield(cand) end
    return
  end
  if ctx:get_option('word_124') then
    for cand in input:iter() do
      local n = char_count(cand.text)
      if n == 1 or n == 2 or n == 4 then yield(cand) end
    end
    return
  end
  if ctx:get_option('word_12') then
    for cand in input:iter() do
      local n = char_count(cand.text)
      if n == 1 or n == 2 then yield(cand) end
    end
    return
  end
  if ctx:get_option('single_char_only') then
    for cand in input:iter() do
      if char_count(cand.text) == 1 then yield(cand) end
    end
    return
  end
  -- 默認：全詞組
  for cand in input:iter() do yield(cand) end
end

return filter
