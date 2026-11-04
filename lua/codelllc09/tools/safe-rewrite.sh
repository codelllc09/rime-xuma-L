#!/usr/bin/env bash
# safe-rewrite.sh — 歷史改寫守衛（2026-11-05 加固）
#
# 事故原型（2026-11-05 20:32，本倉庫真事）：
#     ALLP=$(git log --all --name-only --format= | sort -u | grep -E '...')   # ← 返回空
#     git filter-repo --invert-paths --paths-from-file <(echo "$ALLP" | sed 's/^/literal:/')
# 空清單 → `<(echo "" | sed …)` 產出一行 `literal:` → **空路徑匹配一切**，
# 配 `--invert-paths` ＝ 把全部歷史與工作樹一起刪光。
#
# 本腳本把「危險操作」包成有事前事後兩道檢查的形狀：
#   事前一：工作區必須乾淨（或顯式 --allow-dirty）
#   事前二：鏡像必須覆蓋當前 HEAD（否則先 mirror-sync）
#   事前二：另打一份帶時間戳的 bundle（最後一道本地保險）
#   事前四：**校驗一切路徑清單參數**——空檔、空行、全不存在的路徑，一律拒絕
#   事後一：提交數／跟蹤文件數不得歸零，否則判定爲災難並給出還原命令
#
# 用法：
#   ./safe-rewrite.sh -- <命令...>
#   ./safe-rewrite.sh --note "爲什麼要改寫" -- git filter-repo --invert-paths \
#                      --paths-from-file /tmp/paths.txt --force
#
# 若命令裏有 --paths-from-file/--paths 的檔案參數，本腳本會先驗證再放行。
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "不在 git 倉庫內"; exit 1; }
TOOLS="$ROOT/lua/codelllc09/tools"
BK="$TOOLS/backup.sh"
cd "$ROOT" || exit 1

ALLOW_DIRTY=0
NOTE="（未註明）"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --note) NOTE="$2"; shift 2 ;;
    --) shift; ARGS=("$@"); break ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[ ${#ARGS[@]} -gt 0 ] || { sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

say() { printf '\033[36m%s\033[0m\n' "$*"; }
bad() { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; }
ok()  { printf '\033[32m✓ %s\033[0m\n' "$*"; }

say "══ 事前一：工作區狀態 ══"
DIRTY="$(git status --porcelain | wc -l)"
if [ "$DIRTY" -gt 0 ] && [ "$ALLOW_DIRTY" = 0 ]; then
  bad "工作區有 $DIRTY 處未提交改動。改寫後這些改動無法區分「原本就有」還是「改寫造成」。"
  echo "  先提交，或加 --allow-dirty 明確承擔風險。"
  exit 1
fi
[ "$DIRTY" -gt 0 ] && echo "⚠ 工作區有 $DIRTY 處未提交改動（--allow-dirty 已放行）" || ok "工作區潔淨"

say "══ 事前二：鏡像覆蓋 ══"
if ! "$BK" mirror-status >/tmp/rime-mirror-status.log 2>&1; then
  bad "鏡像未覆蓋當前 HEAD——先同步，否則出事後無外部參照"
  sed 's/^/  /' /tmp/rime-mirror-status.log
  "$BK" mirror-sync
  ok "已補同步"
else
  ok "鏡像已覆蓋 HEAD"
fi

say "══ 事前三：時間戳 bundle ══"
STAMP="$(date '+%Y%m%d-%H%M%S')"
BUNDLE_DIR="${HOME}/Backups/rime/pre-rewrite"
mkdir -p "$BUNDLE_DIR"
BUNDLE="$BUNDLE_DIR/pre-rewrite-$STAMP.bundle"
git bundle create "$BUNDLE" --all --quiet || { bad "bundle 失敗，中止"; exit 1; }
ok "$(basename "$BUNDLE")（$(du -h "$BUNDLE" | cut -f1)）"

BEFORE_COMMITS="$(git rev-list --all --count)"
BEFORE_FILES="$(git ls-files | wc -l)"
echo "  改寫前：$BEFORE_COMMITS 個提交｜$BEFORE_FILES 個跟蹤文件"

say "══ 事前四：路徑清單校驗 ══"
# 提取命令裏 --paths-from-file / --paths 的檔案參數並逐一驗證
python3 - "$ROOT" "${ARGS[@]}" <<'PY'
import os, subprocess, sys

root, argv = sys.argv[1], sys.argv[2:]
files = []
i = 0
while i < len(argv):
    a = argv[i]
    if a in ('--paths-from-file', '--paths', '--replace-text', '--replace-message'):
        if i + 1 < len(argv):
            files.append((a, argv[i + 1]))
        i += 2
        continue
    for fl in ('--paths-from-file', '--paths', '--replace-text', '--replace-message'):
        if a.startswith(fl + '='):
            files.append((fl, a.split('=', 1)[1])); break
    i += 1

if not files:
    print('  （本命令無路徑清單參數，跳過）')
    sys.exit(0)

fatal = False
for flag, path in files:
    if flag == '--paths':
        print(f'  {flag} {path}（字面路徑，非清單檔，跳過內容校驗）')
        continue
    if not os.path.exists(path):
        print(f'  ✗ {flag} 指向的檔案不存在：{path}'); fatal = True; continue
    raw = open(path, encoding='utf-8', errors='replace').read().splitlines()
    entries = [l.strip() for l in raw if l.strip() and not l.strip().startswith('#')]
    if not entries:
        print(f'  ✗ {path}：**空清單**——這正是 2026-11-05 刪庫事故的形狀，拒絕執行')
        fatal = True; continue
    # --replace-text／--replace-message：每行須是 `舊==>新`。缺 `==>` 的行會被當作
    # 「整行字面量替換爲空」——靜默改壞內容，且不會有任何報錯。
    if flag in ('--replace-text', '--replace-message'):
        bad = [e for e in entries if '==>' not in e]
        if bad:
            print(f'  ✗ {path}：{len(bad)} 行缺 `==>` 分隔（會被當作字面量替換爲空）：{bad[:3]}')
            fatal = True; continue
        print(f'  ✓ {path}：{len(entries)} 條替換規則')
        continue
    # `literal:` / `glob:` / `regex:` 後爲空 → 空模式匹配一切
    hollow = [e for e in entries if e in ('literal:', 'glob:', 'regex:')
              or (e.split(':', 1)[0] in ('literal', 'glob', 'regex') and not e.split(':', 1)[1].strip())]
    if hollow:
        print(f'  ✗ {path}：含空模式 {hollow[:3]}——空路徑匹配一切，拒絕執行')
        fatal = True; continue
    # 全部 literal: 條目在歷史中都不存在 → 反選後等於全刪
    lits = [e.split(':', 1)[1].strip() for e in entries
            if e.startswith('literal:') and ':' in e]
    if lits and len(lits) == len(entries):
        exist = set(subprocess.run(['git', '-C', root, 'log', '--all', '--name-only',
                                    '--format='], capture_output=True, text=True
                                   ).stdout.splitlines())
        miss = [p for p in lits if p not in exist]
        if len(miss) == len(lits):
            print(f'  ✗ {path}：{len(lits)} 條路徑在歷史中**全部不存在**'
                  f'（如 {lits[:2]}）——配 --invert-paths 等於刪光，拒絕執行')
            fatal = True; continue
        if miss:
            print(f'  ⚠ {path}：{len(miss)}/{len(lits)} 條在歷史中不存在（不會致命，但多半是筆誤）：{miss[:3]}')
    print(f'  ✓ {path}：{len(entries)} 條有效項')
sys.exit(1 if fatal else 0)
PY
if [ $? -ne 0 ]; then
  bad "路徑清單校驗未過，已中止（未執行任何改寫）"
  exit 1
fi

say "══ 執行 ══"
echo "  說明：$NOTE"
echo "  命令：${ARGS[*]}"
echo
"${ARGS[@]}"
RC=$?
echo
say "══ 事後：完整性判定 ══"
AFTER_COMMITS="$(git rev-list --all --count 2>/dev/null || echo 0)"
AFTER_FILES="$(git ls-files 2>/dev/null | wc -l)"
echo "  改寫後：$AFTER_COMMITS 個提交｜$AFTER_FILES 個跟蹤文件"
if [ "$AFTER_COMMITS" -eq 0 ] || [ "$AFTER_FILES" -eq 0 ]; then
  bad "倉庫已空——判定爲災難（命令 exit=$RC）"
  echo
  echo "  還原（任選其一）："
  echo "    A. 自改寫前 bundle："
  echo "         mkdir -p $ROOT.recover && cd $ROOT.recover"
  echo "         git clone $BUNDLE . && git remote set-url origin https://github.com/codelllc09/rime-xuma-L.git"
  echo "    B. 自獨立鏡像："
  echo "         $BK mirror-restore $ROOT.recover"
  echo
  echo "  本次改寫的事前 bundle：$BUNDLE"
  exit 1
fi
ok "倉庫非空（提交 $AFTER_COMMITS、文件 $AFTER_FILES），命令 exit=$RC"
python3 "$TOOLS/_ledger.py" "$ROOT" 歷史改寫 "$NOTE｜前 $BEFORE_COMMITS 提交/$BEFORE_FILES 件 → 後 $AFTER_COMMITS/$AFTER_FILES；bundle $(basename "$BUNDLE")" >/dev/null
exit "$RC"
