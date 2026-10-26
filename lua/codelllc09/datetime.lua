-- datetime.lua · /time 与 /date 一键时间日期（lua_filter：symbols 占位 → ShadowCandidate 动态替换）
-- 通道实证（2026-09-08 debug 会战）：本机 librime-lua 中 Candidate 新造候选进不了菜单
--（translator/filter 两环境均空），ShadowCandidate（依附已显示候选）可进；
-- 其签名为 ShadowCandidate(cand, type, text, comment[, inherit])——第二参是 type，勿漏。
--   /time → 16:45:36（24h 首位）、4:45:36 PM（12h）
--   /date → 2026年9月8日、2026-09-08、農曆丙午年（马）七月廿七、二〇二六年九月八日、
--           2026/09/08、09/08/2026、星期二
local L = require('codelllc09/lunar_data')

local GAN = { '甲', '乙', '丙', '丁', '戊', '己', '庚', '辛', '壬', '癸' }
local ZHI = { '子', '丑', '寅', '卯', '辰', '巳', '午', '未', '申', '酉', '戌', '亥' }
local XIAO = { '鼠', '牛', '虎', '兔', '龍', '蛇', '馬', '羊', '猴', '雞', '狗', '豬' }
local NUM = { '〇', '一', '二', '三', '四', '五', '六', '七', '八', '九' }
local WEEK = { '日', '一', '二', '三', '四', '五', '六' }
local PLACEHOLDER = { ['ttimexx'] = '/time', ['ddatexx'] = '/date' }

local function cn_num(n)
  if n == 0 then return '〇' end
  local s, t = '', n
  if t >= 10 then s = (t >= 20) and NUM[math.floor(t / 10) + 1] .. '十' or '十'; t = t % 10 end
  if t > 0 then s = s .. NUM[t + 1] end
  return s
end

local function cn_year(y)
  local s = ''
  for ch in tostring(y):gmatch('%d') do s = s .. NUM[tonumber(ch) + 1] end
  return s
end

local function solar2lunar(sy, sm, sd)
  local days = os.time({ year = sy, month = sm, day = sd, hour = 12 })
      - os.time({ year = 1900, month = 1, day = 31, hour = 12 })
  days = math.floor(days / 86400 + 0.5)
  local y = 1900
  while L[y] do
    if days < L[y][1] then
      for i, md in ipairs(L[y][3]) do
        if days < md then
          local mn, leap = i, false
          if L[y][2] > 0 then
            if i == L[y][2] + 1 then leap = true; mn = i - 1
            elseif i > L[y][2] + 1 then mn = i - 1 end
          end
          return { y = y, m = mn, is_leap = leap, d = days + 1 }
        end
        days = days - md
      end
      return nil
    end
    days = days - L[y][1]; y = y + 1
  end
  return nil
end

local function lunar_text(sy, sm, sd)
  local lu = solar2lunar(sy, sm, sd)
  if not lu then return nil end
  local g = GAN[(lu.y - 4) % 10 + 1] .. ZHI[(lu.y - 4) % 12 + 1]
  local sx = XIAO[(lu.y - 4) % 12 + 1]
  local mname = (lu.is_leap and '閏' or '') .. cn_num(lu.m) .. '月'
  local d
  if lu.d <= 10 then d = '初' .. (lu.d == 10 and '十' or NUM[lu.d + 1])
  elseif lu.d < 20 then d = '十' .. NUM[lu.d - 10 + 1]
  elseif lu.d == 20 then d = '二十'
  elseif lu.d < 30 then d = '廿' .. NUM[lu.d - 20 + 1]
  else d = '三十' end
  return string.format('農曆%s年（%s）%s%s', g, sx, mname, d)
end

local function dt_filter(input, env)
  for cand in input:iter() do
    local kind = PLACEHOLDER[cand.text]
    if not kind then
      yield(cand)
    else
      local t = os.date('*t')
      if kind == '/time' then
        yield(ShadowCandidate(cand, 'datetime', string.format('%02d:%02d:%02d', t.hour, t.min, t.sec)))
        local h12 = t.hour % 12; if h12 == 0 then h12 = 12 end
        yield(ShadowCandidate(cand, 'datetime', string.format('%d:%02d:%02d %s', h12, t.min, t.sec, t.hour < 12 and 'AM' or 'PM')))
      else
        local y, m, d = tonumber(os.date('%Y')), tonumber(os.date('%m')), tonumber(os.date('%d'))
        yield(ShadowCandidate(cand, 'datetime', string.format('%d年%d月%d日', y, m, d)))
        yield(ShadowCandidate(cand, 'datetime', os.date('%Y-%m-%d')))
        local lt = lunar_text(y, m, d)
        if lt then yield(ShadowCandidate(cand, 'datetime', lt)) end
        yield(ShadowCandidate(cand, 'datetime', string.format('%s年%s月%s日', cn_year(y), cn_num(m), cn_num(d))))
        yield(ShadowCandidate(cand, 'datetime', os.date('%Y/%m/%d')))
        yield(ShadowCandidate(cand, 'datetime', os.date('%m/%d/%Y')))
        yield(ShadowCandidate(cand, 'datetime', '星期' .. WEEK[t.wday]))
      end
    end
  end
end

return dt_filter
