#!/usr/bin/env bash
# setup.sh — 新克隆／災後恢復的一次性設置（2026-11-05 加固）
#
# 為什麼需要它：鉤子、reflog 保留期、鏡像路徑都存在 **.git/config**（不入庫），
# 克隆後全部消失。把這幾步寫成腳本，恢復時少一次人為遺漏。
#
# 用法： cd ~/.config/ibus/rime && ./lua/codelllc09/tools/setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TOOLS="$ROOT/lua/codelllc09/tools"
cd "$ROOT"

echo "① 鉤子（pre-commit / post-commit / pre-push）"
git config core.hooksPath lua/codelllc09/tools/hooks
chmod +x lua/codelllc09/tools/hooks/* 2>/dev/null || true
echo "   core.hooksPath = $(git config core.hooksPath)"

echo "② reflog 永不過期（誤操作後仍有找回窗口）"
git config gc.reflogExpire never
git config gc.reflogExpireUnreachable never

echo "③ 本地裸鏡像"
"$TOOLS/backup.sh" mirror-init
"$TOOLS/backup.sh" mirror-sync
"$TOOLS/backup.sh" mirror-status | sed 's/^/   /'

echo "④ 門禁自檢"
cd "$TOOLS"
python3 check_consistency.py | tail -3

echo
echo "完成。異地備份：$TOOLS/backup.sh create 後把檔案拷到另一臺設備。"
