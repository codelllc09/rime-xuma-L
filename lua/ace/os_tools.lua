-- os_tools.lua · 系統環境查看（env/NAME → 值候選）
-- 配 recognizer.patterns.os_env: ^env/\w+
local function os_env_translator(input, seg, env)
  local name = input:match('^env/([%w_]+)$')
  if not name then return end
  local val = os.getenv(name)
  if not val then return end
  local c = Candidate('os_env', seg, input, name .. '=' .. val, '環境變量')
  c.quality = 9999
  yield(c)
end
return { os_env_translator = os_env_translator }
