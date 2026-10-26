-- bopomofo.lua
-- 汉语拼音（带调）→ 注音符号（ㄅㄆㄇㄈ）转换，用于注解显示，服务注音符号使用者。
-- 输入：伪词典拼音段，如 "bāng"、"zhōng_zhòng"（多音字以 _ 分隔）。
-- 输出：注音串，多音以 / 分隔，如 "ㄓㄨㄥ/ㄓㄨㄥˋ"；无法解析返回 nil。
-- 实测对 xuma_spelling_pseudo 词典 99.7% 词条可转换（余为外文/异常拼写）。

local bopomofo = {}

-- 声母：拼音 → 注音声母
local initial_map = {
  b = 'ㄅ', p = 'ㄆ', m = 'ㄇ', f = 'ㄈ',
  d = 'ㄉ', t = 'ㄊ', n = 'ㄋ', l = 'ㄌ',
  g = 'ㄍ', k = 'ㄎ', h = 'ㄏ',
  j = 'ㄐ', q = 'ㄑ', x = 'ㄒ',
  zh = 'ㄓ', ch = 'ㄔ', sh = 'ㄕ', r = 'ㄖ',
  z = 'ㄗ', c = 'ㄘ', s = 'ㄙ',
}

-- 韵母：去调拼音 → 注音韵母
local final_map = {
  ['iang'] = 'ㄧㄤ', ['uang'] = 'ㄨㄤ', ['iong'] = 'ㄩㄥ',
  ['ang'] = 'ㄤ', ['eng'] = 'ㄥ', ['ong'] = 'ㄨㄥ',
  ['ai'] = 'ㄞ', ['ei'] = 'ㄟ', ['ao'] = 'ㄠ', ['ou'] = 'ㄡ',
  ['an'] = 'ㄢ', ['en'] = 'ㄣ', ['er'] = 'ㄦ',
  ['ia'] = 'ㄧㄚ', ['ie'] = 'ㄧㄝ', ['iao'] = 'ㄧㄠ', ['iu'] = 'ㄧㄡ',
  ['ian'] = 'ㄧㄢ', ['in'] = 'ㄧㄣ', ['ing'] = 'ㄧㄥ',
  ['ua'] = 'ㄨㄚ', ['uo'] = 'ㄨㄛ', ['uai'] = 'ㄨㄞ', ['ui'] = 'ㄨㄟ',
  ['uan'] = 'ㄨㄢ', ['un'] = 'ㄨㄣ',
  ['üe'] = 'ㄩㄝ', ['üan'] = 'ㄩㄢ', ['ün'] = 'ㄩㄣ',
  ['a'] = 'ㄚ', ['o'] = 'ㄛ', ['e'] = 'ㄜ', ['i'] = 'ㄧ', ['u'] = 'ㄨ',
  ['ü'] = 'ㄩ', ['ê'] = 'ㄝ',
  -- 叹词等特殊音节
  ['n'] = 'ㄣ', ['ng'] = 'ㄥ', ['m'] = 'ㄇ', ['r'] = 'ㄦ',
  ['hm'] = 'ㄏㄇ', ['hng'] = 'ㄏㄥ',
  -- y/w 还原后产生的全写变体
  ['uei'] = 'ㄨㄟ', ['uen'] = 'ㄨㄣ', ['iou'] = 'ㄧㄡ', ['ueng'] = 'ㄨㄥ',
}

-- 带调字母 → { 去调字母, 声调号 }
local diacritic = {
  ['ā'] = {'a', 1}, ['á'] = {'a', 2}, ['ǎ'] = {'a', 3}, ['à'] = {'a', 4},
  ['ē'] = {'e', 1}, ['é'] = {'e', 2}, ['ě'] = {'e', 3}, ['è'] = {'e', 4},
  ['ê̄'] = {'ê', 1}, ['ế'] = {'ê', 2}, ['ê̌'] = {'ê', 3}, ['ề'] = {'ê', 4},
  ['ī'] = {'i', 1}, ['í'] = {'i', 2}, ['ǐ'] = {'i', 3}, ['ì'] = {'i', 4},
  ['ō'] = {'o', 1}, ['ó'] = {'o', 2}, ['ǒ'] = {'o', 3}, ['ò'] = {'o', 4},
  ['ū'] = {'u', 1}, ['ú'] = {'u', 2}, ['ǔ'] = {'u', 3}, ['ù'] = {'u', 4},
  ['ǖ'] = {'ü', 1}, ['ǘ'] = {'ü', 2}, ['ǚ'] = {'ü', 3}, ['ǜ'] = {'ü', 4},
  ['ń'] = {'n', 2}, ['ň'] = {'n', 3}, ['ǹ'] = {'n', 4},
  ['ḿ'] = {'m', 2},
}

-- 组合变音符（词典中少量词条用「基字母+组合调符」而非预组合字母）
local combining = {
  [0x0304] = { a = 'ā', e = 'ē', i = 'ī', o = 'ō', u = 'ū' },  -- 1声
  [0x0301] = { a = 'á', e = 'é', i = 'í', o = 'ó', u = 'ú' },  -- 2声
  [0x030C] = { a = 'ǎ', e = 'ě', i = 'ǐ', o = 'ǒ', u = 'ǔ' },  -- 3声
  [0x0300] = { a = 'à', e = 'è', i = 'ì', o = 'ò', u = 'ù' },  -- 4声
}

local tone_symbol = { [1] = '', [2] = 'ˊ', [3] = 'ˇ', [4] = 'ˋ' }

-- 预处理：把「元音+组合声调符」折叠为预组合字母。
local function fold_combining(syl)
  local out = {}
  local codes = {}
  for _, code in utf8.codes(syl) do codes[#codes + 1] = code end
  local i = 1
  while i <= #codes do
    local code = codes[i]
    local ch = utf8.char(code)
    local nxt = codes[i + 1]
    if nxt and combining[nxt] and ch:match('[aeiou]') then
      local folded = combining[nxt][ch]
      out[#out + 1] = folded or ch
      i = i + 2
    else
      out[#out + 1] = ch
      i = i + 1
    end
  end
  return table.concat(out)
end

-- 将一个音节分解为 initial / final（去调）/ tone。
local function split_syllable(syl)
  syl = fold_combining(syl)
  local tone
  local plain = {}
  for _, code in utf8.codes(syl) do
    local ch = utf8.char(code)
    local d = diacritic[ch]
    if d then
      plain[#plain + 1] = d[1]
      tone = tone or d[2]
    else
      plain[#plain + 1] = ch
    end
  end
  local s = table.concat(plain)
  -- y/w 是隔音字母，还原为 i/u/ü：yu→ü, yi→i, wu→u 整体；其余 y→i, w→u
  if s:find('^yu') then s = 'ü' .. s:sub(3)
  elseif s:find('^yi') then s = 'i' .. s:sub(3)
  elseif s:find('^wu') then s = 'u' .. s:sub(3)
  elseif s:find('^y') then s = 'i' .. s:sub(2)
  elseif s:find('^w') then s = 'u' .. s:sub(2) end
  -- j/q/x 不与 u 相拼，其后的 u 实为 ü
  if s:sub(1, 1):find('[jqx]') and s:sub(2, 2) == 'u' then
    s = s:sub(1, 1) .. 'ü' .. s:sub(3)
  end
  -- 声母判定：zh/ch/sh 优先，其次单字母，否则零声母
  local ini, fin
  local two = s:sub(1, 2)
  if initial_map[two] then
    ini, fin = two, s:sub(3)
  else
    local one = s:sub(1, 1)
    if initial_map[one] then ini, fin = one, s:sub(2)
    else ini, fin = '', s end
  end
  return ini, fin, tone
end

local function pinyin_to_bopomofo(syl)
  local ini, fin, tone = split_syllable(syl)
  local ini_b = initial_map[ini] or ''
  local fin_b = final_map[fin]
  if not fin_b then return nil end
  return ini_b .. fin_b .. (tone and tone_symbol[tone] or '')
end

-- 转换整段拼音（多音字以 "_" 分隔）；任一音节解析失败则返回 nil。
function bopomofo.convert(pinyin_str)
  if not pinyin_str or pinyin_str == '' then return nil end
  local outs = {}
  for syl in pinyin_str:gmatch('[^_]+') do
    local b = pinyin_to_bopomofo(syl)
    if not b then return nil end
    outs[#outs + 1] = b
  end
  return table.concat(outs, '/')
end

return bopomofo
