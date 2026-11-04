#!/usr/bin/env bash
# backup.sh — 倉庫防災底盤（2026-11-05 加固）
#
# 背景：2026-11-05 filter-repo 事故能恢復，靠「本地日誌與提交記錄互相印證」。
# 但當時 72M 字表原件、台賬、審計報告都只有這一塊盤一份。本腳本把
# 「git 歷史 + 不可再生數據 + 清單」打成一個可整體拷走的檔案，供異地留存。
#
# 子命令：
#   create [--full] [--out DIR]   生成快照（默認 ~/Backups/rime；--full 另含語料與緩存）
#   list                          列出已有快照
#   verify [檔案]                 校驗快照完整性（默認最新一件）
#   restore <檔案> <目標目錄>     從快照還原（目標目錄須不存在或爲空）
#   drill                         災難演練：造快照→還原到臨時目錄→克隆→校驗→跑門禁
#
#   mirror-init [--dest P]        建立本地裸鏡像（默認 ~/Backups/rime/mirror.git）
#   mirror-sync                   把當前 HEAD 推成鏡像裏一個**新**的快照 ref（post-commit 自動調用）
#   mirror-status                 鏡像覆蓋情況（HEAD 是否有對應快照 ref）
#   mirror-restore <目標目錄>     從鏡像克隆（鏡像是歷史改寫的最後防線）
#
# 快照內容（--full 額外）：
#   repo.bundle          git 全史（--all，含所有分支標籤）
#   ignored/             不可再生區：std_tables/download、log/（含 add_debug）、tmp/、spool
#   ignored/corpus/      清洗語料（默認不含：BUILD.md 已釘版，可聯網重建 29/29）
#   ignored/cache/       派生緩存（默認不含：純派生，熱跑 10s 可重生）
#   MANIFEST.json        逐件 sha256 ＋ git HEAD ＋ 門禁結果
#   RESTORE.md           還原步驟（給未來的你，含另一臺設備的情形）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TOOLS="$ROOT/lua/codelllc09/tools"
OUT_DEFAULT="$HOME/Backups/rime"
MIRROR_DEFAULT="$HOME/Backups/rime/mirror.git"
KEEP=14

_CLEAN=""
_rm_clean() { [ -n "${_CLEAN:-}" ] && rm -rf $_CLEAN; _CLEAN=""; }

c_g() { printf '\033[32m%s\033[0m\n' "$*"; }
c_y() { printf '\033[33m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# ── create ────────────────────────────────────────────────────────
cmd_create() {
  local full=0 out="$OUT_DEFAULT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --full) full=1 ;;
      --out) out="$2"; shift ;;
      *) die "未知參數：$1" ;;
    esac
    shift
  done
  mkdir -p "$out"
  local stamp name tmp
  stamp="$(date '+%Y%m%d-%H%M%S')"
  name="rime-snapshot-$stamp.tar.gz"
  tmp="$(mktemp -d)"
  _CLEAN="$tmp"; trap '_rm_clean' RETURN

  c_g "① git bundle（全史 --all）"
  # 快照只含「已提交」狀態：bundle 打包的是 refs，未提交的工作區改動不會進快照
  local dirty
  dirty="$(git -C "$ROOT" status --porcelain | wc -l)"
  if [ "$dirty" -gt 0 ]; then
    c_y "   ⚠ 工作區有 $dirty 處未提交改動——不在本快照內（先 git commit 再備份）"
  fi
  git -C "$ROOT" bundle create "$tmp/repo.bundle" --all --quiet
  ls -lh "$tmp/repo.bundle" | awk '{print "   ",$5}'

  c_g "② 不可再生區"
  mkdir -p "$tmp/ignored"
  for d in \
    "lua/codelllc09/tools/std_tables/download" \
    "lua/codelllc09/tools/dict_pipeline/log" \
    "tmp"; do
    [ -e "$ROOT/$d" ] || continue
    mkdir -p "$tmp/ignored/$(dirname "$d")"
    cp -a "$ROOT/$d" "$tmp/ignored/$d"
    printf '    %-48s %s\n' "$d" "$(du -sh "$ROOT/$d" | cut -f1)"
  done
  for f in "lua/codelllc09/tools/dict_pipeline/spool_pending.txt"; do
    [ -e "$ROOT/$f" ] || continue
    mkdir -p "$tmp/ignored/$(dirname "$f")"
    cp -a "$ROOT/$f" "$tmp/ignored/$f"
  done
  if [ "$full" = 1 ]; then
    c_y "   --full：另含語料與緩存（可重建，故默認不含）"
    for d in "lua/codelllc09/tools/dict_pipeline/corpus/sources" \
             "lua/codelllc09/tools/dict_pipeline/cache"; do
      [ -e "$ROOT/$d" ] || continue
      mkdir -p "$tmp/ignored/$(dirname "$d")"
      cp -a "$ROOT/$d" "$tmp/ignored/$d"
      printf '    %-48s %s\n' "$d" "$(du -sh "$ROOT/$d" | cut -f1)"
    done
  fi

  c_g "③ 門禁結果（快照即時體檢）"
  local battery="SKIP"
  if ( cd "$TOOLS" && python3 check_consistency.py >"$tmp/battery.log" 2>&1 ); then
    battery="$(tail -3 "$tmp/battery.log" | grep 小計 || echo PASS)"
    c_g "   $battery"
  else
    battery="FAIL（詳見 MANIFEST 內 battery_tail）"
    c_y "   ⚠ 門禁未全綠，仍照常打包（快照不因門禁擋路），詳見 battery.log"
  fi

  c_g "④ MANIFEST"
  python3 - "$ROOT" "$tmp" "$battery" <<'PY'
import hashlib, json, os, subprocess, sys
root, tmp, battery = sys.argv[1:4]
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()
files = {}
for base, _, names in os.walk(os.path.join(tmp, 'ignored')):
    for n in sorted(names):
        fp = os.path.join(base, n)
        files[os.path.relpath(fp, tmp)] = {'sha256': sha(fp), 'size': os.path.getsize(fp)}
head = subprocess.run(['git', '-C', root, 'rev-parse', 'HEAD'],
                      capture_output=True, text=True).stdout.strip()
branch = subprocess.run(['git', '-C', root, 'rev-parse', '--abbrev-ref', 'HEAD'],
                        capture_output=True, text=True).stdout.strip()
m = {'schema': 1,
     'created_at': subprocess.run(['date', '-Iseconds'], capture_output=True, text=True).stdout.strip(),
     'git_head': head, 'git_branch': branch,
     'tracked_files': len(subprocess.run(['git', '-C', root, 'ls-files'],
                                         capture_output=True, text=True).stdout.split()),
     'battery': battery,
     'battery_tail': open(os.path.join(tmp, 'battery.log'), encoding='utf-8').read()[-2000:]
                     if os.path.exists(os.path.join(tmp, 'battery.log')) else '',
     'ignored_files': files}
json.dump(m, open(os.path.join(tmp, 'MANIFEST.json'), 'w', encoding='utf-8'),
          ensure_ascii=False, indent=1, sort_keys=True)
print(f'    {len(files)} 件被忽略區文件已入清單；HEAD={head[:9]}')
PY

  cat > "$tmp/RESTORE.md" <<'MD'
# 還原步驟（給未來的你／另一臺設備）

本快照＝「git 全史」＋「不可再生區」。清洗語料與緩存默認不在內（可重建）。

## A. 本機重來（硬盤還在，倉庫沒了）

```sh
mkdir -p ~/.config/ibus/rime && cd ~/.config/ibus/rime
git clone /path/to/解包/repo.bundle .
git remote set-url origin https://github.com/codelllc09/rime-xuma-L.git
rsync -a /path/to/解包/ignored/ ./          # 覆蓋不可再生區（download/log/tmp）
cd lua/codelllc09/tools
python3 check_consistency.py                # 10 項電池應全綠
```

## B. 另一臺設備接續（那邊已有自己的 rime 用戶目錄）

先備份對方目錄，再擇一：

- **只取歷史與原件**：把 `ignored/` 按需挑揀覆蓋（如只要 `std_tables/download`、`tmp` 審計報告）
- **整機對齊**：按 A 步驟在對方機器重建，再把對方 `userdb/`（個人學習記錄）另行處置

## C. 不可再生區說明

| 目錄 | 是否可重建 |
|---|---|
| `lua/codelllc09/tools/std_tables/download/` | **否**（一次性考據原件，README 有出處無釘版 URL） |
| `lua/codelllc09/tools/dict_pipeline/log/` | **否**（台賬與管道明細，二重歷史） |
| `tmp/` | **否**（對賬報告與審查答辯） |
| `dict_pipeline/corpus/sources/` | 是（`build_sources.py` ＋ `SHA256SUMS`，淨室實測 29/29） |
| `dict_pipeline/cache/` | 是（管道重算，熱跑約 10s／冷跑約 50s） |
MD

  c_g "⑤ 打包"
  tar -czf "$out/$name" -C "$tmp" repo.bundle ignored MANIFEST.json RESTORE.md battery.log
  chmod 600 "$out/$name"
  printf '    %s\n' "$out/$name"
  ls -lh "$out/$name" | awk '{print "    大小",$5}'

  # 滾動保留
  local n
  n=$(ls -1 "$out"/rime-snapshot-*.tar.gz 2>/dev/null | wc -l)
  if [ "$n" -gt "$KEEP" ]; then
    ls -1t "$out"/rime-snapshot-*.tar.gz | tail -n +$((KEEP + 1)) | while read -r old; do
      rm -f "$old"; echo "    滾動清理：$(basename "$old")"
    done
  fi
  # 註：快照本身不寫台賬——MANIFEST.json（含 created_at／HEAD／216 件雜湊）就在快照裏，
  # 它自己即記錄本。若每份快照都往台賬追加一行，會讓 events.jsonl 長期處於「未提交」狀態，
  # 稀釋「git status 潔淨」的信號價值。drill／restore 屬低頻且有診斷價值，仍記台賬。
  c_g "完成。異地留存：把該檔拷到另一臺設備（建議放 $HOME/Backups/rime 之外的另一塊盤）"
}

# ── list / verify ─────────────────────────────────────────────────
cmd_list() {
  local out="${1:-$OUT_DEFAULT}"
  ls -1lht "$out"/rime-snapshot-*.tar.gz 2>/dev/null | awk '{print $5,$6,$7,$8,$9}' \
    || echo "（無快照：$out）"
}

pick_latest() {
  ls -1t "$OUT_DEFAULT"/rime-snapshot-*.tar.gz 2>/dev/null | head -1
}

cmd_verify() {
  local f="${1:-$(pick_latest)}"
  [ -n "$f" ] && [ -f "$f" ] || die "找不到快照"
  local tmp; tmp="$(mktemp -d)"; _CLEAN="$tmp"; trap '_rm_clean' RETURN
  tar -xzf "$f" -C "$tmp"
  [ -f "$tmp/repo.bundle" ] || die "缺 repo.bundle"
  git bundle verify "$tmp/repo.bundle" >/dev/null 2>&1 || die "bundle 校驗失敗"
  python3 - "$tmp" <<'PY'
import hashlib, json, os, sys
tmp = sys.argv[1]
m = json.load(open(os.path.join(tmp, 'MANIFEST.json'), encoding='utf-8'))
bad = []
for rel, rec in m['ignored_files'].items():
    p = os.path.join(tmp, rel)
    if not os.path.exists(p):
        bad.append(f'缺件 {rel}'); continue
    h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
    if h != rec['sha256']:
        bad.append(f'雜湊不合 {rel}')
print(f"清單 {len(m['ignored_files'])} 件｜HEAD {m['git_head'][:9]}｜門禁 {m['battery']}")
if bad:
    print('✗ ' + '；'.join(bad[:5])); sys.exit(1)
print('✓ 快照完整：bundle 可驗、逐件雜湊相符')
PY
}

# ── restore ───────────────────────────────────────────────────────
cmd_restore() {
  local f="${1:-}" dest="${2:-}"
  [ -n "$f" ] && [ -n "$dest" ] || die "用法：backup.sh restore <快照> <目標目錄>"
  [ -f "$f" ] || die "找不到快照：$f"
  [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ] && die "目標目錄非空：$dest"
  mkdir -p "$dest"
  local tmp; tmp="$(mktemp -d)"; _CLEAN="$tmp"; trap '_rm_clean' RETURN
  tar -xzf "$f" -C "$tmp"
  git clone --quiet "$tmp/repo.bundle" "$dest"
  cp -a "$tmp/ignored/." "$dest/" 2>/dev/null || true
  echo "已還原到 $dest"
  echo "後續：cd $dest/lua/codelllc09/tools && python3 check_consistency.py"
}

# ── drill ─────────────────────────────────────────────────────────
cmd_drill() {
  c_g "災難演練：造快照 → 還原到臨時目錄 → 門禁復跑"
  local out; out="$(mktemp -d)"
  local work; work="$(mktemp -d)"
  _CLEAN="$out $work"; trap '_rm_clean' RETURN
  cmd_create --out "$out" >/dev/null
  local snap; snap="$(ls -1 "$out"/rime-snapshot-*.tar.gz | head -1)"
  echo "① 快照：$(basename "$snap") $(du -h "$snap" | cut -f1)"
  cmd_verify "$snap" | sed 's/^/② /'
  cmd_restore "$snap" "$work/rime" >/dev/null
  echo "③ 還原：$(find "$work/rime" -type f | wc -l) 件"
  echo "④ 還原體門禁："
  local rc=0
  ( cd "$work/rime/lua/codelllc09/tools" && python3 check_consistency.py 2>&1 | tail -4 ) || rc=$?
  if [ "$rc" = 0 ]; then
    c_g "⑤ 演練通過：快照可還原，還原體門禁全綠"
  else
    c_y "⑤ 演練未通過（rc=$rc）——快照不足以重建可工作倉庫，須修復後重演"
    return 1
  fi
  python3 "$TOOLS/_ledger.py" drill 演練 "快照 $(basename "$snap") 還原並復跑門禁：通過" >/dev/null
}

# ── mirror：獨立裸鏡像 ────────────────────────────────────────────
# 爲什麼要它：工作區倉庫裏的一切改寫（filter-repo／reset --hard／gc --prune=now）
# 都碰不到另一個目錄裏的裸庫。這是**唯一不依賴自律**的防線——2026-11-05 那次
# 空路徑清單事故若當時有鏡像，恢復只需一條 clone。
#
# 關鍵設計：不 push --mirror（那會把鏡像的 ref 一起覆蓋，改寫後照樣清零）。
# 改為每個提交推一個**新** ref：refs/snapshots/<時間>-<sha8>。只增不刪，
# 任何時刻的 HEAD 都留着一份，且互相不可覆蓋。
mirror_path() { echo "${MIRROR:-$MIRROR_DEFAULT}"; }

cmd_mirror_init() {
  local dest; dest="$(mirror_path)"     # 尊重 MIRROR 環境變量（沙箱演練與多倉共存需要）
  while [ $# -gt 0 ]; do case "$1" in --dest) dest="$2"; shift ;; esac; shift; done
  [ -d "$dest" ] && { echo "鏡像已存在：$dest"; return 0; }
  mkdir -p "$(dirname "$dest")"
  git init --quiet --bare "$dest"
  echo "已建裸鏡像：$dest"
  cmd_mirror_sync
}

cmd_mirror_sync() {
  local m; m="$(mirror_path)"
  [ -d "$m" ] || return 0        # 鏡像未建：靜默跳過（鉤子調用時不得阻塞提交）
  local head ref
  head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)" || return 0
  ref="refs/snapshots/$(date '+%Y%m%d-%H%M%S')-${head:0:8}"
  # 同秒同 sha 重複推同一 ref 是無害的（no-op），故不加去重判斷
  git -C "$ROOT" push --quiet "$m" "HEAD:$ref" 2>/dev/null || {
    echo "⚠ 鏡像同步失敗（$m）" >&2; return 0; }   # 失敗也不阻塞提交
}

cmd_mirror_status() {
  local m; m="$(mirror_path)"
  [ -d "$m" ] || { echo "未建鏡像（$m）——建議 backup.sh mirror-init"; return 0; }
  local head n
  head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo '')"
  n="$(git -C "$m" for-each-ref refs/snapshots | wc -l)"
  echo "鏡像：$m"
  echo "快照 ref 數：$n"
  if [ -n "$head" ] && git -C "$m" cat-file -e "$head^{commit}" 2>/dev/null; then
    echo "HEAD ${head:0:9}：✓ 鏡像中有此提交"
  else
    echo "HEAD ${head:0:9}：✗ 鏡像中沒有——立即 backup.sh mirror-sync"
    return 1
  fi
}

cmd_mirror_restore() {
  local dest="${1:-}" m; m="$(mirror_path)"
  [ -n "$dest" ] || die "用法：backup.sh mirror-restore <目標目錄>"
  [ -d "$m" ] || die "未建鏡像：$m"
  [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ] && die "目標目錄非空：$dest"
  git clone --quiet "$m" "$dest"
  local latest
  latest="$(git -C "$m" for-each-ref --sort=-refname --format='%(refname)' refs/snapshots | head -1)"
  [ -n "$latest" ] || die "鏡像裏沒有快照 ref"
  git -C "$dest" checkout --quiet -B main "$latest" 2>/dev/null || git -C "$dest" checkout --quiet "$latest"
  echo "已從鏡像還原到 $dest（快照 ${latest##*/}）"
  echo "後續：cd $dest && git remote add origin https://github.com/codelllc09/rime-xuma-L.git"
}

case "${1:-}" in
  create) shift; cmd_create "$@" ;;
  list) shift; cmd_list "$@" ;;
  verify) shift; cmd_verify "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  drill) cmd_drill ;;
  mirror-init) shift; cmd_mirror_init "$@" ;;
  mirror-sync) cmd_mirror_sync ;;
  mirror-status) shift; cmd_mirror_status ;;
  mirror-restore) shift; cmd_mirror_restore "$@" ;;
  *) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
