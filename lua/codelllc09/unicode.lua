-- unicode.lua · U+ 十六進制碼位反查（U6c5f → 江）
-- 2026-11-04：本機 librime 無內建 unicode_translator，lua 實現；僅單字查詢
-- 配 recognizer.patterns.unicode: ^U[0-9a-fA-F]{4,8}$
local function unicode_translator(input, seg, env)
  if not input:match('^U[0-9a-fA-F]{4,8}$') then return end
  local cp = tonumber(input:sub(2), 16)
  if not cp or cp > 0x10FFFF then return end
  local ok, ch = pcall(utf8.char, cp)
  if not ok or ch == '' then return end
  local cand = Candidate('unicode', seg, input, ch, string.format('U+%X', cp))
  cand.quality = 9999
  yield(cand)
end
return unicode_translator
