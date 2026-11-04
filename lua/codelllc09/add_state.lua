-- add_state.lua —— /add 採集菜單（translator）與按鍵處理器（processor）共享狀態
-- librime-lua 單 Lua VM，require 即共享。menu 爲「本輪渲染的候選動作表」，
-- 與 translator 產出順序一一對應，processor 按選鍵索引執行。
-- 交互原則（2026-11-04 v3）：一切在候選欄展開時進行——無後台模式、無隱式跟蹤；
-- 打出缺詞後敲 /add，尾部連綴（最近 4 字）直接列出，選中即收錄進 spool。
return {
  menu = {},     -- {{kind='word'|'commit'|'clear'|'noop', text=...}}
  banner = nil,  -- 動作後一次性提示（首次渲染即消耗）
}
