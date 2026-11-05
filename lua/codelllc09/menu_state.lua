-- menu_state.lua —— /menu 二級設定菜單的共享狀態與選項組定義
--
-- 與 schema `switches:` 的對應：options/values 順序即 switches 裏的 options/states。
-- 多態組（options 列表）與布爾組（bool 單選項）統一爲「取值索引」模型：
--   current() 返回現值索引；apply(i) 設定第 i 值。
-- 註解兩組已從 F4 switches 移除（選單瘦身，2026-11-05）——初始默認由本模組
-- init_defaults() 在會話啓動時補上（librime 對未聲明選項不會 reset，邏輯等價
-- 於原 reset: 2 / reset: 0，且換方案回來能記住上次選擇）。

local M = { category = nil, menu = {} }   -- category=nil 一級；數字=組索引（二級）

M.groups = {
  { key = 'word', name = '詞長',
    options = { 'word_all', 'word_124', 'word_12', 'single_char_only' },
    values  = { '全詞', '124', '12', '單字' }, default = 3 },
  { key = 'sp', name = '註解',
    options = { 'xuma_spelling.off', 'xuma_spelling.py', 'xuma_spelling.pysp',
                'xuma_spelling.bopomofo', 'xuma_spelling.lv1',
                'xuma_spelling.lv2', 'xuma_spelling.lv3' },
    values  = { '〇註解', '拼音', '生僻拼音', '註音符', '一重', '二重', '三重' },
    default = 3 },   -- 生僻拼音（原 schema reset: 2，0 基轉 1 基）
  { key = 'spr', name = '註解·反查',
    options = { 'xuma_spelling_rvlk.off', 'xuma_spelling_rvlk.bopomofo',
                'xuma_spelling_rvlk.lv1', 'xuma_spelling_rvlk.lv2',
                'xuma_spelling_rvlk.lv3' },
    values  = { '〇註解', '註音符', '一重', '二重', '三重' },
    default = 1 },   -- 反查不疊加註解（原 reset: 0）
  { key = 'simp', name = '簡繁', bool = 'simplification', values = { '漢字', '漢字→汉' } },
  { key = 'charset', name = '字符集', bool = 'extended_charset', values = { '通用', '全部' } },
  { key = 'nosimple', name = '非簡', bool = 'no_simple', values = { '通打', '非簡' } },
  { key = 'punct', name = '標點', bool = 'ascii_punct', values = { '。，', '．，' } },
}

function M.current(g, ctx)
  if g.bool then
    return ctx:get_option(g.bool) and 2 or 1
  end
  for i, o in ipairs(g.options) do
    if ctx:get_option(o) then return i end
  end
  return g.default or 1
end

function M.apply(g, idx, ctx)
  if g.bool then
    ctx:set_option(g.bool, idx == 2)
    return
  end
  for i, o in ipairs(g.options) do
    ctx:set_option(o, i == idx)
  end
end

-- 會話啓動補默認：僅對「全組皆滅」的多態組生效（布爾組滅＝合法的 False 檔）
function M.init_defaults(ctx)
  for _, g in ipairs(M.groups) do
    if not g.bool and g.default then
      local any = false
      for _, o in ipairs(g.options) do
        if ctx:get_option(o) then any = true break end
      end
      if not any then M.apply(g, g.default, ctx) end
    end
  end
end

return M
