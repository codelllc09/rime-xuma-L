#!/bin/bash
# explore_pianhai.sh — 《四聲篇海》數據源探索（網絡恢復後一鍵執行）
# 目標：找到可編輯格式的篇海字表（只要漢字）；契丹文等非漢字不管
# 產出：std_tables/download/ 下建 pianhai/ 存檔，探針後評估增量

set -u
D='lua/codelllc09/tools/std_tables/download/pianhai'
mkdir -p "$D"

echo '== 1) 網絡探測'
for u in 'https://github.com' 'https://zh.wikisource.org' 'https://ctext.org' 'https://archive.org'; do
  printf '%s → ' "$u"; curl -sL -m 8 -o /dev/null -w '%{http_code}\n' "$u" || echo fail
done

echo '== 2) GitHub 數據集搜索（篇海/字彙補/金代字書）'
for q in '%E5%9B%9B%E9%9F%BF%E7%AF%87%E6%B5%B7' 'pianhai' '%E5%AD%97%E5%BD%99%E8%A3%A5' 'sishengpianhai'; do
  echo "-- query: $q"
  curl -sL -m 15 "https://api.github.com/search/repositories?q=$q&per_page=6" | \
    python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    for r in d.get('items',[]): print(f\"  {r['full_name']} S{r['stargazers_count']} {(r['description'] or '')[:70]}\")
except Exception as e: print('  parse fail', e)"
done

echo '== 3) ctext / 維基文庫錄文探測'
curl -sL -m 12 'https://ctext.org/wiki.pl?if=gb&searchu=%E5%9B%9B%E9%9F%BF%E7%AF%87%E6%B5%B7' -o "$D/ctext_search.html" && \
  grep -oP '四聲篇海[^<"]{0,40}' "$D/ctext_search.html" | head -5
curl -sL -m 12 'https://zh.wikisource.org/wiki/Special:%E6%90%9C%E7%B4%A2?search=%E5%9B%9B%E9%9F%BF%E7%AF%87%E6%B5%B7' -o "$D/ws_search.html" && \
  grep -oP '四聲篇海[^<"]{0,40}' "$D/ws_search.html" | head -5

echo '== 4) archive.org 影印本'
curl -sL -m 15 'https://archive.org/advancedsearch.php?q=%E5%9B%9B%E9%9F%BF%E7%AF%87%E6%B5%B7+OR+%E5%AD%97%E5%BD%99%E8%A3%A5&fl%5B%5D=identifier&fl%5B%5D=title&rows=8&output=json' -o "$D/ia.json" && \
  python3 -c "import json; d=json.load(open('$D/ia.json'))
for doc in d.get('response',{}).get('docs',[]): print(' ', doc.get('identifier'), '-', str(doc.get('title'))[:60])"

echo '== 完成。後續：找到源→抓取→探針（东们书会国 5/5 即拒）→與 17 表求差→入庫'
