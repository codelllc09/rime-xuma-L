-- mode_hint.lua · 输入状态徽标 v3
-- 写入会话运行目录中的 rime-mode 供 WezTerm 读取：
--   a = 西文(小写)  A = 西文(CapsLock大写)  漢 = 繁中  汉 = 简中
-- A/a 实时追踪 CapsLock 灯状态（按键事件携带 Lock 修饰符 = 大写中），
-- 无论会话从什么状态开始、通过什么路径进入西文，都反映真实大小写。

local state = { caps_cause = false, last_written = nil }
local runtime_dir = os.getenv('XDG_RUNTIME_DIR') or '/tmp'
local mode_file = runtime_dir .. '/rime-mode'
local mode_file_tmp = mode_file .. '.tmp'

local function notify_wezterm(s)
  -- 通知仅用于立即触发重绘。setsid 令命令脱离 Rime 主线程，timeout
  -- 防止 WezTerm 未启动或残留 socket 时后台进程长期挂住。
  local cmd = string.format(
    '/usr/bin/setsid -f /usr/bin/timeout 0.5s /usr/bin/wezterm cli set-tab-title %q </dev/null >/dev/null 2>&1',
    s
  )
  os.execute(cmd)
end

local function write_state(ctx)
  local s
  if ctx:get_option('ascii_mode') then
    s = state.caps_cause and 'A' or 'a'
  elseif ctx:get_option('simplification') then
    s = '汉'
  else
    s = '漢'
  end
  if s == state.last_written then return end -- 防重复写
  -- 先写临时文件再替换，避免 WezTerm 读到截断中的空文件。
  local f = io.open(mode_file_tmp, 'w')
  if f then
    f:write(s)
    f:close()
    -- 仅在落盘成功后更新缓存；失败则保留旧值，下次状态变化前会重试
    -- （WezTerm 的周期刷新另作兑底）。
    if os.rename(mode_file_tmp, mode_file) then state.last_written = s end
  end
  -- 正常时立即推送；失败时由 WezTerm 的周期刷新兜底。
  notify_wezterm(s)
end

local function caps_led_on()
  -- 读内核 LED 设备的硬件真相（兼容 input 编号变化，用通配符）
  local p = io.popen('cat /sys/class/leds/*capslock/brightness 2>/dev/null')
  if not p then return nil end
  local s = p:read('*a') or ''  -- 读异常时按无数据处理，勿对 nil 调 match
  p:close()
  return s:match('^%s*1') ~= nil
end

local m = {}

function m.init(env)
  env.engine.context.option_update_notifier:connect(function(ctx, name)
    if name == 'ascii_mode' or name == 'simplification' then
      write_state(ctx)
    end
  end)
  write_state(env.engine.context) -- 会话启动时写入初始状态
end

local function is_modifier_key(name)
  return name:match('_[LR]$') ~= nil or name == 'Caps_Lock'
end

function m.func(key, env)
  local r = key:repr()

  -- 拆解 repr: 形如 "Shift+Lock+Release+space"，末位是键名
  local parts = {}
  for t in r:gmatch('[^+]+') do parts[#parts + 1] = t end
  local keyname = parts[#parts]
  local has_lock = false
  for _, t in ipairs(parts) do
    if t == 'Lock' then has_lock = true break end
  end

  if keyname == 'Caps_Lock' then
    if has_lock or r:find('Release', 1, true) then
      -- 松开（或带修饰符）时读硬件 LED 真相，灯必已稳定
      local led = caps_led_on()
      state.caps_cause = (led == nil) or led
    else
      state.caps_cause = true -- 按下瞬间先乐观显示 A
    end
    write_state(env.engine.context)
  elseif keyname == 'Shift_L' or keyname == 'Shift_R' then
    state.caps_cause = false
  elseif has_lock ~= state.caps_cause then
    -- 普通按键的 Lock 修饰符与当前认知不符 → 实时纠正 a⇄A
    if env.engine.context:get_option('ascii_mode') then
      state.caps_cause = has_lock
      write_state(env.engine.context)
    end
  end

  return 2 -- kNoop：只观察，不拦截
end

return m
