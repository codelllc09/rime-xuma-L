-- add_word_proc.lua · /add 菜單選鍵攔截（數字/空格/分號/引號），選中即動作不上屏
-- 收錄→追加 spool；「入庫重跑」→ 後臺調 handpick_commit.sh（明細重定向 add_commit.log）
local SPOOL  = rime.api and rime.api.get_user_dir() .. '/lua/codelllc09/tools/dict_pipeline/spool_pending.txt'
  or (debug and '') or ''
local TOOLS = rime_api.get_user_dir() .. '/lua/codelllc09/tools'

local function in_add(env)
  return env.engine.context.input:match('^/add') ~= nil
end

local function append_spool(w)
  local f = io.open(SPOOL, 'a')
  if not f then return false end
  f:write(w .. '\n')
  f:close()
  return true
end

local function spool_count()
  local n, f = 0, io.open(SPOOL, 'r')
  if f then
    for _ in f:lines() do n = n + 1 end
    f:close()
  end
  return n
end

local function proc(key, env)
  if not in_add(env) then return kNoop end
  local ctx = env.engine.context
  local repr = key:repr()
  -- 數字選鍵：1=收錄尾詞；2=入庫重跑
  if key:release() then return kNoop end
  if repr == 'space' or repr == 'semicolon' or repr == 'apostrophe' then
    -- 視作選中首項（收錄）
    local word = ctx.input:match('^/add%s*(.*)$')
    if word ~= '' then
      if append_spool(word) then
        ctx:clear()
        return kAccepted
      end
    end
    return kNoop
  end
  if repr == '1' then
    local word = ctx.input:match('^/add%s*(.*)$')
    if word ~= '' and append_spool(word) then
      ctx:clear()
      return kAccepted
    end
    return kNoop
  elseif repr == '2' then
    if spool_count() > 0 then
      -- 後臺入庫：全量明細重定向 add_commit.log（兩級記錄：明細在 log，台賬由腳本自記）
      os.execute('nohup bash "' .. TOOLS .. '/handpick_commit.sh" '
                 .. '>> "' .. TOOLS .. '/dict_pipeline/log/add_commit.log" 2>&1 &')
      ctx:clear()
      return kAccepted
    end
    return kNoop
  end
  return kNoop
end
return proc
