#!/usr/bin/env bash
# handpick_commit.sh — spool 攢批入庫：校驗→併入 handpicked.txt（## 日收）→重跑詞頻管道→部署
# 流程：spool 暫存 → 校驗（2-4 字純漢字·不在詞典·不在清單）→ 追加 handpicked.txt（## 日收）
# 運行全量明細由調用方 add_word_proc.lua 重定向至 add_commit.log（兩級記錄：明細在 log，索引自動記台賬）
set -euo pipefail
TOOLS="$(cd "$(dirname "$0")" && pwd)"
SPOOL="$TOOLS/dict_pipeline/spool_pending.txt"
HP="$TOOLS/dict_pipeline/handpicked.txt"
LOCK="$TOOLS/dict_pipeline/.add_commit.lock"
[ -f "$SPOOL" ] || { echo "無 spool：$SPOOL"; exit 0; }
exec 9>"$LOCK"; flock -n 9 || { echo "入庫進行中（鎖佔用），稍後再試"; exit 1; }
python3 - "$SPOOL" "$HP" "$TOOLS" <<'PY'
import sys, io, re, datetime, subprocess, os
spool, hp, dictp = sys.argv[1:4]
def dict_words(p):
    s = set(); started = False
    for l in io.open(p, encoding='utf-8'):
        if not started:
            if l.rstrip('\n') == '...': started = True
            continue
        c = l.split('\t')
        if len(c) >= 2 and not l.startswith('#'): s.add(c[0])
    return s
HAN = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]{2,4}$')
hp_words = {l.split()[0] for l in io.open(hp, encoding='utf-8')
            if l.strip() and not l.startswith('#')}
d_words = dict_words(os.path.join(dictp, '..', '..', '..', 'xuma_L.dict.yaml'))
cands = [w.strip() for w in io.open(spool, encoding='utf-8') if w.strip()]
take, skip = [], []
for w in cands:
    (take if HAN.match(w) and w not in hp_words and w not in d_words else skip).append(w)
for w in take:
    print(f'[handpick] 收詞 1：{w}')
if not take:
    print('（無可入庫詞）')
    sys.exit(0)
with io.open(hp, 'a', encoding='utf-8') as f:
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    f.write(f'## 日收（{today}，spool 攢批）\n' + '\n'.join(take) + '\n')
io.open(spool, 'w', encoding='utf-8').write('')
PY
# 重跑詞頻管道＋部署（明細由調用方重定向；此處僅保證入庫與重跑）
cd "$TOOLS"
echo "$(date '+%F %T') 重跑詞頻管道……"
python3 gen_freq_dict.py
echo "$(date '+%F %T') 部署（ibus restart）……"
ibus restart && sleep 3
echo "$(date '+%F %T') 入庫完成"
# 本行僅向 IGNORED_CHANGES 台賬記一行索引（手動調用本腳本時，明細無落點，此行即唯一痕跡）
printf '%s\tspool_pending.txt\t入庫\t/add 收詞固化：重跑詞頻管道並部署\n' \
  "$(date '+%FT%T')" >> "$TOOLS/dict_pipeline/log/IGNORED_CHANGES.log"
