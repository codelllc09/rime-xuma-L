-- menu.lua —— /menu 二級設定菜單（translator）
--
-- 交互：輸入 /menu → 一級（分類，數字選）→ 二級（取值，現值帶 ✓，數字選中即
--       生效並退出；Esc/⌫ 返回上一級）。選項組定義與共享狀態見 menu_state.lua。
-- 候選數控制在 7 以內（menu/page_size=7），防翻頁令選鍵索引錯位（同 /add 約束）。

local S = require('codelllc09/menu_state')

local function init(env)
  S.init_defaults(env.engine.context)
end

local function translator(input, seg, env)
  if input ~= '/menu' then return end
  local ctx = env.engine.context
  S.menu = {}
  local g = S.category and S.groups[S.category]
  if g then
    local cur = S.current(g, ctx)
    for i, v in ipairs(g.values) do
      S.menu[#S.menu + 1] = { group_idx = S.category, value = i }
      local c = Candidate('menu', seg.start, seg._end, v, i == cur and '✓ 現值' or '')
      c.preedit = input .. '\t' .. g.name .. '：數字選值｜Esc/⌫ 返回'
      yield(c)
    end
  else
    for gi, gr in ipairs(S.groups) do
      S.menu[#S.menu + 1] = { group_idx = gi }
      local c = Candidate('menu', seg.start, seg._end, gr.name,
                          '現值 ' .. gr.values[S.current(gr, ctx)] .. ' ›')
      c.preedit = input .. '\t' .. '設定：數字選分類'
      yield(c)
    end
  end
end

return { init = init, func = translator }
