-- dx_amount.lua · /dx[数字] 金额大写（lua_translator，通道测试中）
--   /dx123 → 壹佰貳拾叄 / /dx123.45 → 壹佰貳拾叄元肆角伍分 / /dx → 壹貳參單字組
--   繁體大寫為主，簡化由 simplifier 開關處理（與詞庫一致）
local D = { [0]='零', [1]='壹', [2]='貳', [3]='參', [4]='肆', [5]='伍',
            [6]='陸', [7]='柒', [8]='捌', [9]='玖' }
local U = { [2]='拾', [3]='佰', [4]='仟' }
local G = { [1]='萬', [2]='億', [3]='兆' }

local function four(n)  -- 0-9999 → 大写（无前导零处理由调用者）
  if n == 0 then return '零' end
  local s = ''
  local digits = {}
  local x = n
  while x > 0 do digits[#digits+1] = x % 10; x = math.floor(x / 10) end
  local zero_pending = false
  for i = #digits, 1, -1 do
    local d = digits[i]
    if d == 0 then
      zero_pending = true
    else
      if zero_pending and s ~= '' then s = s .. '零' end
      zero_pending = false
      s = s .. D[d] .. (U[i] or '')
    end
  end
  return s
end

local function int_part(str)
  -- 去前导零
  str = str:gsub('^0+', '')
  if str == '' then return '零' end
  local groups = {}
  while #str > 0 do
    local seg = str:sub(-4)
    str = str:sub(1, -5)
    groups[#groups+1] = tonumber(seg == '' and '0' or seg)
  end
  local out = ''
  for i = #groups, 1, -1 do
    local g = four(groups[i])
    if groups[i] > 0 then
      -- 跨组零：本组千位缺（<1000）且前面已有高位组 → 亿/万交界补零（壹億零伍萬）
      if out ~= '' and groups[i] < 1000 and not out:find('零$') then out = out .. '零' end
      out = out .. g .. (i > 1 and (G[i-1] or '') or '')
    elseif out ~= '' and i > 1 and not out:find('零$') then
      out = out .. '零'
    end
  end
  out = out:gsub('零+$', '')
  return out == '' and '零' or out
end

local function dec_part(frac)
  -- frac 两位数字字符串
  local j = tonumber(frac:sub(1,1)) or 0
  local f = tonumber(frac:sub(2,2)) or 0
  local s = ''
  if j > 0 then s = D[j] .. '角' elseif f > 0 then s = '零' end
  if f > 0 then s = s .. D[f] .. '分' end
  return s
end

local function convert(numstr)
  local ip, dp = numstr:match('^(%d*)%.?(%d*)$')
  local text
  if dp ~= '' then
    local ipx = int_part(ip ~= '' and ip or '0')
    local dpx = dec_part(dp)
    if dpx == '' then
      text = ipx .. '元整'
    else
      text = ipx .. '元' .. dpx
    end
  else
    text = int_part(ip)
  end
  return text
end

local function translator(input, seg, env)
  if not seg:has_tag('punct') then return end
  local num = input:match('^/dx(%d*)%.?%d*$')
  if not num and input ~= '/dx' then return end
  if input == '/dx' then return end   -- 纯 /dx 无候选：防数字键被当选字键（候选存在时 1-9 是选字）；数字由 recognizer 收进输入串
  local digits = input:match('^/dx([%d.]+)$')
  if digits then
    local ok, r = pcall(convert, digits)
    if ok and r then
      yield(Candidate('dx', seg.start, seg._end, r, ''))
    end
  end
end

return translator
