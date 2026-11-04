-- unicode.lua · 码位反查：U6c5f → 江（2026-11-04）
-- 本机 librime（Fedora 1.16.1）无内建 unicode_translator（已核实组件表/符号表），
-- 以 lua_translator 实现，配 recognizer/patterns/unicode: "^U[0-9a-fA-F]{4,8}$"。
-- 候选不受字符集闸门限制（enable_charset_filter 仅作用于 table_translator）。
local function unicode(input, seg, env)
  if not seg:has_tag('unicode') then return end
  local hex = input:match('^U([0-9a-fA-F]+)$')
  if not hex or #hex < 4 or #hex > 8 then return end
  local cp = tonumber(hex, 16)
  if not cp or cp == 0 or cp > 0x10FFFF then return end
  if cp >= 0xD800 and cp <= 0xDFFF then return end -- 代理区无字符
  local ch = utf8.char(cp)
  local c = Candidate('unicode', seg.start, seg._end, ch, 'U+' .. hex:upper())
  c.preedit = input .. '\t碼位反查'
  yield(c)
end
return unicode
