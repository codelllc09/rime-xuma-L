-- xuma_spelling.lua

-- 在上游基础上新增：拼音注解(.py)、生僻注解(.pysp)、注音注解(.bopomofo)三档；
-- 注解选项拆为直接输入/反查两组（xuma_spelling.* / xuma_spelling_rvlk.*），
-- 热键按当前是否反查自动选组。
-- 依赖模块 lua/codelllc09/{bopomofo,common_chars}。

--[[
单字的字根拆分三重注解直接利用 simplifier 通过预制的 OpenCC 词库查到。
问题：这个方法，词组只能显示每个单字的注解，需要进行简化合并处理，仅显示词组编
码和对应字根。
计划：用 Lua 处理词组注解。

实现障碍：simplifier 返回的类型，无法修改其注释。
   https://github.com/hchunhui/librime-lua/issues/16
一个思路：show_in_commet: false
   然后读取 cand.text 修改后作为注释显示，问题是无法直接将 cand.text 改回。
   理论上只能用 Candidate() 生成简单类型候选。

现在的方案：完全弃用 simplifier + OpenCC，单字和词组都用 Lua 处理。

注解数据来源与 OpenCC 方法相同，编成伪方案的伪词典，通过写入主方案的
schema/dependencies 来让 rime 编译为反查库 *.reverse.bin，最后通过 Lua 的反查
函数查询。

Handle multibye string in Lua:
  https://stackoverflow.com/questions/9003747/splitting-a-multibyte-string-in-lua

lua_filter 如何判断 cand 是否来自反查或当前是否处于反查状态？
  https://github.com/hchunhui/librime-lua/issues/18
--]]

local basic = require 'ace/lib/basic'
local utf8chars = basic.utf8chars
local rime = require 'ace/lib/rime'
local bopomofo = require 'codelllc09/bopomofo'
local common_chars = require 'codelllc09/common_chars'
local config = {}
config.encode_rules = {
  { length_equal = 2, formula = 'AaAbBaBb' },
  { length_equal = 3, formula = 'AaBaCaCb' },
  { length_in_range = {4, 10}, formula = 'AaBaCaZa' }
}
-- 注意借用编码规则有局限性：取码索引不一定对应取根索引，尤其是从末尾倒数时。
local spelling_rules = rime.encoder.load_settings(config.encode_rules)
-- options 要与方案保持一致。两个过滤器实例按命名空间各选一组：
-- xmsp（直接输入）用 xuma_spelling.*；xmsp_for_rvlk（反查）用
-- xuma_spelling_rvlk.*。两组可分别设置，互不影响。
-- with_py 为真时，直接输入组在 off 后插入「仅拼音」档（.py）。
-- 修正 2026-09-06：.bopomofo 移出 with_py 分支——两套方案的 switches 都为反查组
-- 定义了该档。此前反查组仅 4 项而 default=5，options_rvlk[5]=nil 传入 set_option，
-- 令 xmsp/xmsp_for_rvlk 两个过滤器初始化即失败（三重注解自部署起从未生效）。
local function make_options(prefix, with_py)
  local t = { prefix .. '.off' }
  if with_py then
    t[#t + 1] = prefix .. '.py'
    t[#t + 1] = prefix .. '.pysp'
  end
  t[#t + 1] = prefix .. '.bopomofo'
  t[#t + 1] = prefix .. '.lv1'
  t[#t + 1] = prefix .. '.lv2'
  t[#t + 1] = prefix .. '.lv3'
  return t
end
local options = make_options('xuma_spelling', true)
local options_rvlk = make_options('xuma_spelling_rvlk', false)
options.default = 7  -- [off, py, pysp, bopomofo, lv1, lv2, lv3] 的 lv3
options_rvlk.default = 5


local processor = rime.make_option_cycler(
    { options = options, options_rvlk = options_rvlk },
    'xuma_spelling/lua/cycle_key',
    'xuma_spelling/lua/switch_key')


local function xform(s)
  -- input format: "[spelling,code_code...,pinyin_pinyin...]"
  -- output format: "〔 spelling · code code ... · pinyin pinyin ... 〕"
  return s == '' and s or s:gsub('%[', '〔 ')
                           :gsub('%]', ' 〕')
                           :gsub('{', '<')
                           :gsub('}', '>')
                           :gsub('_', ' ')
                           :gsub(',', ' · ')
end


local function parse_spll(str)
  -- Handle spellings like "{于下}{四点}丶"(for 求) where some radicals are
  -- represented by characters in braces.
  local radicals = {}
  for seg in str:gsub('%b{}', ' %0 '):gmatch('%S+') do
    if seg:find('^{.+}$') then
      table.insert(radicals, seg)
    else
      for pos, code in utf8.codes(seg) do
        table.insert(radicals, utf8.char(code))
      end
    end
  end
  return radicals
end


local function parse_raw_tricomment(str)
  return str:gsub(',.*', ''):gsub('^%[', '')
end


-- 从伪词典的原始条目 [拆分,编码,拼音] 提取拼音段；无拼音时返回 nil。
local function parse_raw_pinyin(str)
  local py = str:match('^%[[^,]*,[^,]*,([^,%]]*)%]?$')
  if py == nil or py == '' then return end
  return py:gsub('_', '/')
end

-- 拼音段转注音；失败返回 nil。
local function raw_pinyin_to_bopomofo(str)
  local py = str:match('^%[[^,]*,[^,]*,([^,%]]*)%]?$')
  if py == nil or py == '' then return end
  return bopomofo.convert(py)
end


-- 生僻字注音：单字为生僻字时返回注音（拼音或注音），否则 nil。
local function rare_pinyin(raw_spelling, bpmf)
  if bpmf then return raw_pinyin_to_bopomofo(raw_spelling)
  else return parse_raw_pinyin(raw_spelling) end
end

-- 生僻字注音（词组）：返回按字拼接的注音串，常用字以 '…' 占位；
-- 若无任何生僻字则返回 nil。多音字注音以 '/' 分隔。
local function rare_phrase_pinyin(text, spll_rvdb, bpmf)
  local parts, has_rare = {}, false
  for _, ch in ipairs(utf8chars(text)) do
    if common_chars.is_common_char(ch) then
      parts[#parts + 1] = '…'
    else
      local raw = spll_rvdb:lookup(ch)
      if raw == '' then return end
      local py = rare_pinyin(raw, bpmf)
      if not py then return end
      parts[#parts + 1] = py
      has_rare = true
    end
  end
  if not has_rare then return end
  return table.concat(parts, ' ')
end


local function spell_phrase(s, spll_rvdb)
  local chars = utf8chars(s)
  local rule = spelling_rules[#chars]
  if not rule then return end
  local radicals = {}
  for i, coord in ipairs(rule) do
    local char_idx = coord[1] > 0 and coord[1] or #chars + 1 + coord[1]
    local raw = spll_rvdb:lookup(chars[char_idx])
    -- 若任一取码单字没有注解数据，则不对词组作注。
    if raw == '' then return end
    local char_radicals = parse_spll(parse_raw_tricomment(raw))
    local code_idx = coord[2] > 0 and coord[2] or #char_radicals + 1 + coord[2]
    radicals[i] = char_radicals[code_idx] or '◇'
  end
  return table.concat(radicals)
end


local function get_tricomment(cand, env)
  local prefix = env.name_space == 'xmsp_for_rvlk'
    and 'xuma_spelling_rvlk'
    or 'xuma_spelling'
  local function on(suffix)
    return env.engine.context:get_option(prefix .. '.' .. suffix)
  end
  local text = cand.text
  if utf8.len(text) == 1 then
    local raw_spelling = env.spll_rvdb:lookup(text)
    if raw_spelling == '' then return end
    if on('py') then
      local py = parse_raw_pinyin(raw_spelling)
      if py then return ('〔 %s 〕'):format(py) end
      return
    end
    if on('pysp') then
      if common_chars.is_common_char(text) then return end
      local py = rare_pinyin(raw_spelling, false)
      if py then return ('〔 %s 〕'):format(py) end
      return
    end
    if on('bopomofo') then
      local bpmf = raw_pinyin_to_bopomofo(raw_spelling)
      if bpmf then return ('〔 %s 〕'):format(bpmf) end
      return
    end
    return on('lv1')
      and xform(raw_spelling:gsub('%[(.-),.*%]', '[%1]'))
      or on('lv2')
      and xform(raw_spelling:gsub('%[(.-,.-),.*%]', '[%1]'))
      or xform(raw_spelling)  -- lv3 is on
  elseif utf8.len(text) > 1 then
    if on('py') then
      local pys = {}
      for _, ch in ipairs(utf8chars(text)) do
        local py = parse_raw_pinyin(env.spll_rvdb:lookup(ch))
        if not py then return end
        pys[#pys + 1] = py
      end
      return ('〔 %s 〕'):format(table.concat(pys, ' '))
    end
    if on('pysp') then
      local py = rare_phrase_pinyin(text, env.spll_rvdb, false)
      if py then return ('〔 %s 〕'):format(py) end
      return
    end
    if on('bopomofo') then
      local bpmfs = {}
      for _, ch in ipairs(utf8chars(text)) do
        local bpmf = raw_pinyin_to_bopomofo(env.spll_rvdb:lookup(ch))
        if not bpmf then return end
        bpmfs[#bpmfs + 1] = bpmf
      end
      return ('〔 %s 〕'):format(table.concat(bpmfs, ' '))
    end
    local spelling = spell_phrase(text, env.spll_rvdb)
    if not spelling then return end
    spelling = spelling:gsub('{(.-)}', '<%1>')
    if on('lv1') then
      return ('〔 %s 〕'):format(spelling)
    end
    local code = env.code_rvdb:lookup(text)
    if code ~= '' then  -- 按长度排列多个编码。
      local codes = {}
      for m in code:gmatch('%S+') do codes[#codes + 1] = m end
      table.sort(codes, function (i, j) return i:len() < j:len() end)
      return ('〔 %s · %s 〕'):format(spelling, table.concat(codes, ' '))
    else  -- 以括号类型区分非本词典之固有词
      return ('〈 %s 〉'):format(spelling)
      -- Todo: 如果要为此类词组添加编码注释，其中的单字存在一字多码的情况，需先
      -- 通过比较来确定全码，再提取词组编码。注意特殊单字：八个八卦名，要排除其
      -- 特殊符号编码 'dl?g'.
    end
  end
end


local function filter(input, env)
  local prefix = env.name_space == 'xmsp_for_rvlk'
    and 'xuma_spelling_rvlk'
    or 'xuma_spelling'
  if env.engine.context:get_option(prefix .. '.off') then
    for cand in input:iter() do yield(cand) end
    return
  end
  for cand in input:iter() do
    --[[
    用户有时需要通过拼音反查简化字并显示三重注解，但 luna_pinyin 的简化字排序不
    合理且靠后。用户可开启 simplification 来解决，但是 simplifier 会强制覆盖注
    释，为了避免三重注解被覆盖，只能生成一个简单类型候选来代替原候选。
    Todo: 测试在 <simplifier>/tips: none 的条件下，用 cand.text 和
    cand:get_genuine().text 分别读到什么值。若分别读到转换前后的候选，则可以仅
    修改 comment 而不用生成简单类型候选来代替原始候选。这样做的问题是关闭
    xuma_spelling 时就不显示 tips 了。
    --]]
    if cand.type == 'simplified' and env.name_space == 'xmsp_for_rvlk' then
      local comment = (get_tricomment(cand, env) or '') .. cand.comment
      cand = Candidate("simp_rvlk", cand.start, cand._end, cand.text, comment)
    else
      local add_comment = cand.type == 'punct'
        and env.code_rvdb:lookup(cand.text)
        or cand.type ~= 'sentence'
        and get_tricomment(cand, env)
      if add_comment and add_comment ~= '' then
        -- 混输和反查中的非 completion 类型，原注释为空或主词典的编码。
        -- 为免重复冗长，直接以新增注释替换之。前提是后者非空。
        cand.comment = cand.type ~= 'completion'
          and ((env.name_space == 'xmsp' and env.is_mixtyping)
            or (env.name_space == 'xmsp_for_rvlk'))
          and add_comment
          or add_comment .. cand.comment
      end
    end
    yield(cand)
  end
end


local function init(env)
  local config = env.engine.schema.config
  local spll_rvdb = config:get_string('lua_reverse_db/spelling')
  local code_rvdb = config:get_string('lua_reverse_db/code')
  local abc_extags_size = config:get_list_size('abc_segmentor/extra_tags')
  env.spll_rvdb = ReverseDb('build/' .. spll_rvdb .. '.reverse.bin')
  env.code_rvdb = ReverseDb('build/' .. code_rvdb .. '.reverse.bin')
  env.is_mixtyping = abc_extags_size > 0
  rime.init_options(options, env.engine.context)
  rime.init_options(options_rvlk, env.engine.context)
end


return { filter = { init = init, func = filter }, processor = processor }
