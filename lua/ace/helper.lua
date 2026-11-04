-- helper.lua

-- List features and usage of the schema.
local function translator(input, seg)
  local items  -- 原名 table，遮蔽标准库 table（若日后深加 table.concat 等调用会意外报 nil）
  if input == 'help/' then
    -- 2026-11-04 按本方案现状重写：移除已不存在的绑定（簡入繁出/屏蔽词组/预显顶选/上屏注释），
    -- 补齐新功能（组合反查、码位反查、金额大写、动态日期、F4 可切项）。
    items = {
      { '繁體簡化', 'Ctrl+Shift+4 / Super+F' },
      { '三重註解', 'Ctrl + C' },
      { '註解輪換', 'Ctrl + Shift + C' },
      { '詞組長短', 'F4 菜單切換' },
      { '非簡模式', 'F4 菜單切換' },
      { '字符集閘門', 'Ctrl + Shift + 5' },
      { '雙重反查', '`' },
      { '全拼反查', '`P' },
      { '筆畫反查', '`B' },
      { '組合反查', '`拼音,筆畫' },
      { '碼位反查', 'U + 十六进制码位' },
      { '金額大寫', '/dx + 数字' },
      { '時間日期', '/time  /date' },
      { '加詞入庫', '/add（逐字打出缺詞後趁熱敲）' },
      { '環境變量', 'env/' },
      { '顯示幫助', 'help/' },
      { '徐碼官網', 'https://www.xumax.top' },
      { '方案下載', 'https://github.com/Ace-Who/rime-xuma' }
    }
  else return end
  for k, v in ipairs(items) do
    local cand = Candidate('help', seg.start, seg._end, v[2], ' ' .. v[1])
    cand.preedit = input .. '\t簡要說明'
    yield(cand)
  end
end

return translator
