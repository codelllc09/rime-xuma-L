-- basic.lua · 通用小工具库
-- 2026-09-08 精简：index / last_index / list2dict / map / matchstr /
-- random_in_ranges / time_accurate / utf8sub 均为零调用死代码，已删；
-- 仅存 utf8chars（xuma_spelling 等在用）。


local basic = {}
package.loaded[...] = basic


function basic.utf8chars(str)
  local chars = {}
  for pos, code in utf8.codes(str) do
    chars[#chars + 1] = utf8.char(code)
  end
  return chars
end


return basic
