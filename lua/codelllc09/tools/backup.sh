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
#   offsite [目標] [--fresh]      把最新快照投遞到另一臺設備並校驗（默認 yoga:D:/Backups/rime）
#                                 --fresh 先重造快照（確保對應當前 HEAD）
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
  local full=0 out="$OUT_DEFAULT" userdb=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --full) full=1 ;;
      --with-userdb) userdb=1 ;;
      --out) out="$2"; shift ;;
      *) die "未知參數：$1" ;;
    esac
    shift
  done
  mkdir -p "$out"
  # 磁盤餘量：快照+鏡像長期增長，寫滿盤會連帶毀掉工作區。低於 5G 先告警。
  local avail_g
  avail_g="$(df -BG --output=avail "$out" 2>/dev/null | tail -1 | tr -dc '0-9')"
  if [ -n "${avail_g:-}" ] && [ "$avail_g" -lt 5 ]; then
    c_y "⚠ 目標盤僅餘 ${avail_g}G——請先清理再備份"
  fi
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
  if [ "$userdb" = 1 ]; then
    c_y "   --with-userdb：含個人學習庫（userdb 含擊鍵學習記錄，默認不含）"
    for d in xuma_L.userdb luna_pinyin.userdb stroke.userdb; do
      [ -d "$ROOT/$d" ] || continue
      mkdir -p "$tmp/ignored"
      cp -a "$ROOT/$d" "$tmp/ignored/$d"
      printf '    %-48s %s\n' "$d" "$(du -sh "$ROOT/$d" | cut -f1)"
    done
  fi
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

## B2. 歷史被改寫／倉庫被清空（filter-repo 事故類）

工具已隨工作樹一起被刪，只能用純 git：

```sh
git clone ~/Backups/rime/mirror.git /tmp/recover        # 本地裸鏡像
git fetch origin '+refs/snapshots/*:refs/snapshots/*'   # 取回全部快照點
git -C /tmp/recover log --oneline --all | head
```

或自改寫守衛留的 bundle：`~/Backups/rime/pre-rewrite/pre-rewrite-*.bundle`。
（2026-11-05 演練：0 提交 → 恢復 27 提交／112 件，十一項門禁全綠。）

## C. 不可再生區說明

| 目錄 | 是否可重建 |
|---|---|
| `lua/codelllc09/tools/std_tables/download/` | **否**（一次性考據原件，README 有出處無釘版 URL） |
| `lua/codelllc09/tools/dict_pipeline/log/` | **否**（台賬與管道明細，二重歷史） |
| `tmp/` | **否**（對賬報告與審查答辯） |
| `dict_pipeline/corpus/sources/` | 是（`build_sources.py` ＋ `SHA256SUMS`，淨室實測 29/29） |
| `dict_pipeline/cache/` | 是（管道重算，熱跑約 10s／冷跑約 50s） |
MD

  # 恢復卡的**倉庫外副本**：RECOVERY.md 雖入庫，但倉庫沒了時你打不開它。
  # 在快照旁放一份，並在 offsite 時一併投遞。
  if [ -f "$ROOT/RECOVERY.md" ]; then
    cp -f "$ROOT/RECOVERY.md" "$out/RECOVERY.md"
    printf '    %s\n' "$out/RECOVERY.md"
  fi

  c_g "⑤ 打包"
  tar -czf "$out/$name" -C "$tmp" repo.bundle ignored MANIFEST.json RESTORE.md battery.log
  chmod 600 "$out/$name"
  printf '    %s\n' "$out/$name"
  ls -lh "$out/$name" | awk '{print "    大小",$5}'
  # 自動校驗（0.5s）：寫完立刻驗，比等到要用的那天才發現壞了強
  c_g "⑥ 自校驗"
  cmd_verify "$out/$name" | sed 's/^/    /' || { c_y "⚠ 新快照自校驗未過"; }

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
  local T0; T0=$(date +%s)
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
  # ⑥ 鏡像可用性：真的能 clone 出與 HEAD 相同的樹（不是只看 ref 存在）
  echo "⑥ 鏡像可用性："
  local mrc=0
  if [ -d "$(mirror_path)" ]; then
    git clone -q "$(mirror_path)" "$work/mirror-clone" 2>/dev/null || mrc=1
    if [ "$mrc" = 0 ]; then
      local a b
      a="$(git -C "$work/mirror-clone" ls-tree -r --name-only HEAD 2>/dev/null | sort | sha256sum | cut -d' ' -f1)"
      b="$(git -C "$ROOT" ls-tree -r --name-only HEAD | sort | sha256sum | cut -d' ' -f1)"
      [ "$a" = "$b" ] && echo "     ✓ 克隆樹與 HEAD 一致（$(git -C "$work/mirror-clone" ls-files | wc -l) 件）" \
                      || { echo "     ✗ 克隆樹與 HEAD 不一致"; mrc=1; }
    else
      echo "     ✗ 鏡像無法 clone"
    fi
  else
    echo "     − 未建鏡像，跳過"
  fi
  [ "$mrc" = 0 ] || rc=1

  # ⑦ 確定性：雙 PYTHONHASHSEED 復跑管道，兩次產出須逐字節一致。
  # 這一項要活庫語料，故只能在真實倉庫跑（還原體無語料會 SKIP）。
  # 它會重寫詞典等生成物——故僅在工作區潔淨時執行，跑完立即 checkout 還原。
  echo "⑦ 確定性（雙 seed 復跑管道）："
  local dirty_d
  dirty_d="$(git -C "$ROOT" status --porcelain | wc -l)"
  if [ "$dirty_d" -gt 0 ]; then
    echo "     − 工作區有 $dirty_d 處未提交改動，跳過（避免覆蓋你的工作）"
  elif ! [ -d "$ROOT/lua/codelllc09/tools/dict_pipeline/corpus/sources/_mapping" ]; then
    echo "     − 無語料，跳過"
  else
    local det=0
    ( cd "$TOOLS" && python3 check_consistency.py --determinism ) >/tmp/rime-drill-det.log 2>&1 || det=1
    grep -E '確定性' /tmp/rime-drill-det.log | tail -1 | sed 's/^/     /'
    if [ "$det" != 0 ]; then
      echo "     ✗ 確定性未過（詳見 /tmp/rime-drill-det.log）"; rc=1
    fi
    # 復跑必然重寫生成物；還原到提交態（上面已確認跑前是潔淨的）
    git -C "$ROOT" checkout -- . 2>/dev/null || true
    local left
    left="$(git -C "$ROOT" status --porcelain | wc -l)"
    [ "$left" = 0 ] && echo "     ✓ 生成物已還原，工作區潔淨" \
                    || { echo "     ⚠ 還原後仍有 $left 處改動，請人工確認"; rc=1; }
  fi

  local RTO=$(( $(date +%s) - T0 ))
  echo "⑧ 恢復耗時（RTO）：${RTO}s"
  if [ "$rc" = 0 ]; then
    c_g "⑤ 演練通過：快照可還原、鏡像可 clone，還原體門禁全綠（RTO ${RTO}s）"
  else
    c_y "⑤ 演練未通過（rc=$rc）——快照不足以重建可工作倉庫，須修復後重演"
    return 1
  fi
  python3 "$TOOLS/_ledger.py" drill 演練 \
    "快照 $(basename "$snap") 還原並復跑門禁：通過；RTO ${RTO}s" >/dev/null
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
  # 裸庫的 HEAD 指向一個不存在的分支時，`git clone` 會得到空倉——先把它指好，
  # 否則出事後最自然的動作（clone 鏡像）會失敗。實測教訓。
  git -C "$dest" symbolic-ref HEAD refs/heads/main
  echo "已建裸鏡像：$dest"
  cmd_mirror_sync
}

cmd_mirror_sync() {
  local m; m="$(mirror_path)"
  [ -d "$m" ] || return 0        # 鏡像未建：靜默跳過（鉤子調用時不得阻塞提交）
  local head ref
  head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null)" || return 0
  ref="refs/snapshots/$(date '+%Y%m%d-%H%M%S')-${head:0:8}"
  # 推兩個 ref：
  #   refs/heads/main          —— 便民用，讓 `git clone 鏡像` 拿到東西；會移動
  #   refs/snapshots/<…>-<sha> —— **只增不刪**的歷史，恢復時真正的依據
  # clone 默認只拷 refs/heads/*，不拷 snapshots——故兩者都要。
  # 必須繞過鉤子：本 push 會觸發 pre-push，而 pre-push 跑的電池⑨正是檢查
  # 「鏡像是否覆蓋 HEAD」——尚未同步時必敗，遂成死鎖（實測踩到）。
  # 除 HEAD 外，還要保住三類容易漏掉的 ref（2026-11-05 補）：
  #   refs/tags/*   —— 里程碑「已知良好點」，出事後最想要的東西；此前完全沒進鏡像
  #   refs/notes/*  —— 提交註記
  #   refs/stash    —— 未提交的暫存工作（最容易丟的一類）
  local extra=()
  local r
  while IFS= read -r r; do
    [ -n "$r" ] && extra+=("+$r:$r")
  done < <(git -C "$ROOT" for-each-ref --format='%(refname)' refs/tags refs/notes 2>/dev/null)
  # refs/stash 是 "funny ref"，远端拒收——改存 refs/archive/stash-<ts>，只增不覆盖
  if git -C "$ROOT" for-each-ref refs/stash 2>/dev/null | grep -q .; then
    extra+=("+refs/stash:refs/archive/stash-$(date '+%Y%m%d-%H%M%S')")
  fi

  git -C "$ROOT" -c core.hooksPath=/dev/null push --quiet --force "$m" \
      "HEAD:refs/heads/main" "HEAD:$ref" ${extra[@]+"${extra[@]}"} 2>/dev/null || {
    echo "⚠ 鏡像同步失敗（$m）" >&2; return 0; }   # 失敗也不阻塞提交
  # 自愈：老鏡像的 HEAD 可能還指着 refs/heads/master（默認初始化值），
  # 那樣 `git clone 鏡像` 會得到空工作樹（實測踩過）。每次同步都糾正一次。
  [ "$(git -C "$m" symbolic-ref --quiet HEAD 2>/dev/null)" = "refs/heads/main" ] \
    || git -C "$m" symbolic-ref HEAD refs/heads/main 2>/dev/null || true
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

# ── offsite：投遞到另一臺設備 ──────────────────────────────────────
# 本地做再多，也擋不住「盤壞／整機丟」。異地是唯一真正的外部副本。
# 本子命令把「造快照→傳→遠端校驗」串成一條命令，並兼容 Unix(sha256sum) 與
# Windows(PowerShell Get-FileHash) 兩種遠端。
OFFSITE_DEFAULT="yoga:D:/Backups/rime"

remote_hash() {   # $1=host  $2=遠端檔案路徑
  local h
  h="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$1" \
        "sha256sum '$2' 2>/dev/null | cut -d' ' -f1" 2>/dev/null | tr -d '\r')"
  if [ -z "${h:-}" ]; then
    h="$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$1" \
          "powershell -NoProfile -Command \"(Get-FileHash -Algorithm SHA256 '$2').Hash\"" \
          2>/dev/null | tr -d '\r' | tail -1)"
  fi
  echo "$h" | tr 'A-Z' 'a-z'
}

cmd_offsite() {
  local target="$OFFSITE_DEFAULT" fresh=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --fresh) fresh=1 ;;
      -*) die "未知參數：$1" ;;
      *) target="$1" ;;
    esac
    shift
  done
  local host="${target%%:*}" rdir="${target#*:}"
  [ "$host" != "$target" ] || die "目標格式應為 host:目錄，例如 yoga:D:/Backups/rime"

  if [ "$fresh" = 1 ] || [ -z "$(ls -1 "$OUT_DEFAULT"/rime-snapshot-*.tar.gz 2>/dev/null)" ]; then
    cmd_create
  fi
  local snap; snap="$(ls -1t "$OUT_DEFAULT"/rime-snapshot-*.tar.gz 2>/dev/null | head -1)"
  [ -n "$snap" ] || die "找不到快照"
  local bn; bn="$(basename "$snap")"

  # 快照落後於 HEAD 時提醒（備份只含已提交狀態）
  local shead
  shead="$(tar -xzOf "$snap" MANIFEST.json 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin)["git_head"])' 2>/dev/null || echo '')"
  local now; now="$(git -C "$ROOT" rev-parse HEAD)"
  if [ -n "$shead" ] && [ "$shead" != "$now" ]; then
    c_y "⚠ 快照 HEAD ${shead:0:9} ≠ 當前 HEAD ${now:0:9}——用 --fresh 重造以對應當前狀態"
  fi

  c_g "① 校驗本地快照"
  cmd_verify "$snap" | sed 's/^/   /' || die "本地快照未過校驗，不投遞"

  c_g "② 傳輸 → $host:$rdir"
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$host" \
      "[ -d '$rdir' ] || mkdir -p '$rdir' || powershell -NoProfile -Command \"New-Item -ItemType Directory -Force -Path '$rdir' | Out-Null\"" \
      2>/dev/null || true
  timeout 900 scp -q "$snap" "$host:$rdir/" || die "scp 失敗"
  echo "   $(du -h "$snap" | cut -f1)"

  # 恢復卡也送一份：倉庫與快照都在的情況下它多餘，但「只剩這台機」時它是全部。
  [ -f "$ROOT/RECOVERY.md" ] && timeout 120 scp -q "$ROOT/RECOVERY.md" "$host:$rdir/" 2>/dev/null \
    && echo "   另送 RECOVERY.md"

  c_g "③ 遠端校驗"
  local lh rh
  lh="$(sha256sum "$snap" | cut -d' ' -f1)"
  rh="$(remote_hash "$host" "$rdir/$bn")"
  if [ -z "${rh:-}" ]; then
    c_y "   ⚠ 遠端無法計算雜湊（缺 sha256sum 且 PowerShell 不可用），僅確認檔案存在"
    ssh -o BatchMode=yes "$host" "test -f '$rdir/$bn' || powershell -NoProfile -Command \"Test-Path '$rdir/$bn'\"" >/dev/null 2>&1 \
      && echo "   檔案在" || { echo "   ✗ 遠端找不到檔案"; return 1; }
  elif [ "$lh" = "$rh" ]; then
    c_g "   ✓ 雜湊一致：${lh:0:16}…"
  else
    c_y "   ✗ 雜湊不一致：本地 ${lh:0:16}… / 遠端 ${rh:0:16}…"
    return 1
  fi

  python3 "$TOOLS/_ledger.py" "$target/$bn" 異地備份 \
    "快照 $(printf '%s' "$bn") 投遞並校驗通過（HEAD ${now:0:9}）" >/dev/null
  c_g "完成。異地副本：$host:$rdir/$bn"
}

cmd_mirror_backfill() {
  local m; m="$(mirror_path)"
  [ -d "$m" ] || { echo "未建鏡像：$m"; return 1; }
  local have n=0 c ts
  have="$(git -C "$m" for-each-ref --format='%(objectname)' refs/snapshots | cut -c1-40 | sort -u)"
  for c in $(git -C "$ROOT" rev-list HEAD); do
    printf '%s\n' "$have" | grep -qx "$c" && continue
    ts="$(git -C "$ROOT" log -1 --format=%cd --date=format:'%Y%m%d-%H%M%S' "$c")"
    git -C "$ROOT" -c core.hooksPath=/dev/null push --quiet --force "$m" \
        "$c:refs/snapshots/${ts}-${c:0:8}" 2>/dev/null && n=$((n+1))
  done
  echo "已補 $n 個缺失的快照 ref（總 $(git -C "$ROOT" rev-list HEAD --count) 個提交）"
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
  mirror-backfill) cmd_mirror_backfill ;;
  offsite) shift; cmd_offsite "$@" ;;
  *) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
