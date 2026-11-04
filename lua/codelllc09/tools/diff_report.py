#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diff_report.py — 詞表改動對賬報告（2026-11-05，用戶複覈通道）

用法：python3 diff_report.py <舊提交> [新提交=HEAD] [報告名]
產出 tmp/詞表改動-<名>.md：五類計數（新增上屏/注釋退隱/恢復/消失/僅變權）＋全量明細；
同時記 dict_pipeline/log/IGNORED_CHANGES.log 台賬一行（tmp/ 報告屬忽略區）。
"""
import os, subprocess, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
TMP = os.path.join(ROOT, 'tmp')


def rev_blob(rev, path='xuma_L.dict.yaml'):
    r = subprocess.run(['git', '-C', ROOT, 'show', f'{rev}:{path}'],
                       capture_output=True)
    if r.returncode != 0:
        sys.exit(f'git show {rev}:{path} 失敗：{r.stderr.decode()[:200]}')
    return r.stdout.decode('utf-8')


def parse(text):
    act, com = {}, {}
    started = False
    for line in text.split('\n'):
        if not started:
            if line == '...': started = True
            continue
        if not line.strip():
            continue
        c = line.split('\t')
        if len(c) < 2: continue
        t, code = c[0], c[1]
        w = c[2] if len(c) > 2 else ''
        if line.lstrip().startswith('#'):
            if t: com[t] = (code, w)
        else:
            act[t] = (code, w)
    return act, com


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    old = sys.argv[1]
    new = sys.argv[2] if len(sys.argv) > 2 else 'HEAD'
    name = sys.argv[3] if len(sys.argv) > 3 else (new if new != 'HEAD' else '對賬')
    a_act, a_com = parse(rev_blob(old))
    b_act, b_com = parse(rev_blob(new))
    added = sorted(set(b_act) - set(a_act) - set(a_com))
    revived = sorted(t for t in set(b_act) & set(a_com) if t not in set(a_act))
    gone = sorted(set(a_act) - set(b_act) - set(b_com))
    commented = sorted(set(a_act) & set(b_com))
    wdiff = sorted(t for t in set(a_act) & set(b_act)
                   if a_act[t] != b_act[t])
    rep = [f'# 詞表改動對賬 · {old}…{new}', '',
           f'新增（上屏）**{len(added)}**｜注釋（退隱）**{len(commented)}**｜'
           f'恢復 **{len(revived)}**｜消失 **{len(gone)}**｜僅變權 **{len(wdiff)}**']
    for title, items, note in (('新增（上屏）', added, '此前無此詞+碼'),
                                ('注釋（退隱）', commented, '由活轉注'),
                                ('恢復', revived, '由注轉活'),
                                ('消失', gone, '此前有、現無（含注釋行整體移除）'),
                                ('僅變權', wdiff, '')):
        if not items: continue
        rep += ['', f'## {title}（{len(items)}）', '', note, '', '```']
        for t in items:
            c, w = (b_act.get(t) or b_com.get(t) or a_act.get(t) or ('', ''))
            if title.startswith('僅變權'):
                c2, w2 = a_act[t]
                rep.append(f'{t}\t{c}\t{w2} → {w}')
            else:
                rep.append(f'{t}\t{c}' + (f'\t{w}' if w else ''))
        rep.append('```')
    os.makedirs(TMP, exist_ok=True)
    out = os.path.join(TMP, f'詞表改動-{name}.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(rep) + '\n')
    print(f'報告已寫出 {out}：新增{len(added)} 注釋{len(commented)} '
          f'恢復{len(revived)} 消失{len(gone)} 變權{len(wdiff)}')
    log = os.path.join(HERE, 'dict_pipeline', 'log', 'IGNORED_CHANGES.log')
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, 'a', encoding='utf-8') as lg:
        lg.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\t"
                 f"../tmp/詞表改動-{name}.md\t生成\t對賬 {old}…{new}："
                 f"新增{len(added)} 注釋{len(commented)} 恢復{len(revived)} 消失{len(gone)}\n")


if __name__ == '__main__':
    main()
