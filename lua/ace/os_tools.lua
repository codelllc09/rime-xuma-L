-- os_tools.lua

-- 懒钟 lazy_clock_filter 已删（2026-11-04）：候选栏常驻实时时钟（Ctrl+T 开关），
-- 与 /time 动态时间候选重复，按需保留后者；Ctrl+T 绑定与 helper 条目一并移除。
-- preedit_lazy_clock_filter 亦已于 2026-09-08 删除（无开关无接线）。

-- 将 `env/VAR` 翻译为系统环境变量。
-- 长度限制为 199 字节。太长时 UI 卡住，且实际上屏的是前 199 字节。
local function os_env_translator(input, seg)
  local prefix = '^env/'
  if input:find(prefix .. '%w+') then
    local val, cand = os.getenv(input:gsub(prefix, ''))
    if val then
      cand = Candidate("os_env", seg.start, seg._end, val:sub(1,199), "")
    else
      cand = Candidate("os_env", seg.start, seg._end, "", "空值")
    end
    cand.preedit = input .. '\t環境變量'
    yield(cand)
  end
end

return { os_env_translator = os_env_translator }
