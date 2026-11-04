#!/usr/bin/env bash
# privacy-gate.sh — push 前的隐私门禁（2026-11-05 加固）
#
# 为什么需要：机械电池再全，也查不出「提交里带了家目录路径 / 身份串 / token」。
# 此前这类检查全靠人记得跑 privacy-scrub skill ——而实测「人记得」等于「很少跑」
# （本仓库曾连续 45 个提交未经隐私扫描）。
#
# 设计：调用 privacy-scrub 的扫描器，用**仓内白名单**过滤已人工复核的命中。
# 白名单里不写明文 token，只写 `<kind>\t<路径子串>\t<match 的 sha256 前 12 位>\t<理由>`
# —— 加一条 = 一次显式的、能在 git diff 里看到的决定。
#
# 用法：./privacy-gate.sh [--list] [--scan-only]
# 退出码：0 通过；1 有未放行的命中
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ALLOW="$ROOT/lua/codelllc09/tools/privacy-allow.txt"
SCAN="${PRIVACY_SCAN:-$HOME/.pi/agent/skills/privacy-scrub/scripts/scan_history.py}"

[ -f "$SCAN" ] || { echo "− 未装 privacy-scrub 扫描器（$SCAN），跳过隐私门禁"; exit 0; }

if [ "${1:-}" = "--list" ]; then
  [ -f "$ALLOW" ] && cat "$ALLOW" || echo "（无白名单）"
  exit 0
fi

cd "$ROOT" || exit 1
JSON="$(mktemp)"
trap 'rm -f "$JSON"' EXIT
python3 "$SCAN" . --json >"$JSON" 2>/dev/null
RC=$?
if [ "$RC" = 2 ]; then echo "✗ 扫描器运行失败"; exit 1; fi

python3 - "$JSON" "$ALLOW" <<'PY'
import hashlib, json, sys

findings = json.load(open(sys.argv[1], encoding='utf-8')).get('findings', [])
allow_path = sys.argv[2]

allow = []
try:
    for ln in open(allow_path, encoding='utf-8'):
        ln = ln.strip()
        if not ln or ln.startswith('#'):
            continue
        parts = ln.split('\t')
        if len(parts) >= 3:
            allow.append((parts[0], parts[1], parts[2]))
except FileNotFoundError:
    pass

def h(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:12]

def allowed(f):
    for kind, pathsub, dig in allow:
        if f['kind'] == kind and pathsub in f['where'] and h(f['match']) == dig:
            return True
    return False

bad = [f for f in findings if not allowed(f)]
if not bad:
    print(f'✓ 隐私门禁：{len(findings)} 条命中全部在白名单内（{len(allow)} 条规则）')
    sys.exit(0)

print(f'✗ 隐私门禁：{len(bad)} 条未放行命中（共 {len(findings)} 条）')
seen = set()
for f in bad:
    key = (f['kind'], f['match'], f['where'])
    if key in seen:
        continue
    seen.add(key)
    print(f"  [{f['sev'].upper()}] {f['kind']:<10} {f['match']:<24} @ {f['where']}")
print()
print('  处置：① 改代码消除命中（首选）；② 若确属误报，写入白名单：')
print(f'     printf "%s\\t%s\\t%s\\t%s\\n" <kind> <路径子串> <match 的 sha256 前12位> <理由> >> {allow_path}')
for f in bad[:5]:
    print(f"     {f['kind']}\t{f['where'].split(':',1)[-1]}\t{h(f['match'])}\t<理由>")
sys.exit(1)
PY
