#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diff_report.py — 詞表改動對賬報告（2026-11-05，用戶複覈通道）

用法：python3 diff_report.py <舊ref> <新ref> [輸出.md]
       ref 爲任意 git 對象（提交/分支/HEAD~1…）；缺省輸出 tmp/詞表改動-<新ref短碼>.md

對 xuma_L.dict.yaml 數據區做條目級對賬（詞+碼 爲鍵），按淨變化分組輸出：
新增（上屏）/ 注釋（退隱）/ 恢復 / 消失 / 變權。供用戶逐條複覈。
"""
import os, subprocess, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # 60d66a8：自 rime/lua 上提一級
DICT = 'xuma_L.dict.yaml'


def entries_of(ref):
    out = subprocess.run(['git', 'show', f'{ref}:{DICT}'], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    ent = {}
    started = False
    for l in out.splitlines():
        if not started:
            if l == '...': started = True
            continue
        if not l.strip(): continue
        com = l.lstrip().startswith('#')
        body = l.lstrip('# ').split('] ')[-1] if com else l
        c = body.split('\t')
        if len(c) < 2: continue
        key = (c[0], c[1])
        w = c[2].strip() if len(c) > 2 else ''
        ent[key] = ('注' if com else '活', w)
    return ent


def short(ref):
    return subprocess.run(['git', 'rev-parse', '--short', ref], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()


def main():
    old_r, new_r = sys.argv[1], sys.argv[2]
    A, B = entries_of(old_r), entries_of(new_r)
    added  = sorted(k for k in B if k not in A and B[k][0] == '活')
    gone   = sorted(k for k in A if k not in B)
    tocom  = sorted(k for k in A if k in B and A[k][0] == '活' and B[k][0] == '注')
    revive = sorted(k for k in A if k in B and A[k][0] == '注' and B[k][0] == '活')
    wchg   = sorted(k for k in A if k in B and A[k][0] == B[k][0] and A[k][1] != B[k][1])
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
        ROOT, 'tmp', f'詞表改動-{short(new_r)}.md')
    if not os.path.dirname(out):   # 裸文件名 → 落 ROOT/tmp/，沿「詞表改動-<名>.md」慣例（2026-11-05）
        if not out.endswith('.md'): out += '.md'
        out = os.path.join(ROOT, 'tmp', f'詞表改動-{out}')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(f'# 詞表改動對賬 · {old_r}…{short(new_r)}\n\n')
        f.write(f'新增（上屏）**{len(added)}**｜注釋（退隱）**{len(tocom)}**｜'
                f'恢復 **{len(revive)}**｜消失 **{len(gone)}**｜僅變權 **{len(wchg)}**\n\n')
        for title, ks, note in (
                ('新增（上屏）', added, '此前無此詞+碼'),
                ('注釋（退隱）', tocom, '由活轉注'),
                ('恢復', revive, '由注轉活'),
                ('消失', gone, '此前有、現無（含注釋行整體移除）')):
            if not ks: continue
            f.write(f'## {title}（{len(ks)}）\n\n{note}\n\n```\n')
            for w, c in ks:
                f.write(f'{w}\t{c}\n')
            f.write('```\n\n')
        if wchg:
            f.write(f'## 僅變權（{len(wchg)}）\n\n```\n')
            for w, c in wchg:
                f.write(f'{w}\t{c}\t{A[(w,c)][1]} → {B[(w,c)][1]}\n')
            f.write('```\n')
    print(f'報告寫出 {out}（新增{len(added)} 注釋{len(tocom)} 恢復{len(revive)} '
          f'消失{len(gone)} 變權{len(wchg)}）')
    # 台賬（2026-11-05 加固）：統一走 _ledger 寫入口，tmp/ 報告屬忽略區→TSV+JSONL 雙寫
    sys.path.insert(0, HERE)
    from _ledger import note
    note(f'../tmp/{os.path.basename(out)}', '生成',
         f'對賬 {old_r}…{short(new_r)}：新增{len(added)} 注釋{len(tocom)} '
         f'恢復{len(revive)} 消失{len(gone)}')


if __name__ == '__main__':
    main()
