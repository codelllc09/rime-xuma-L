#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transcribe_review.py — TO_BE_REVIEWED 批注轉抄器（2026-11-05）

用法：cd tools && python3 transcribe_review.py [--dry-run]

讀 archive/reports/TO_BE_REVIEWED.txt 的批注行（+ 放行 / - 否決 / ? 未批），
按裁定通道語義轉抄並自驗：
  + → reviewed.txt（繁形，帶碼入庫）＋ reviewed_done.txt（簡形，不再重列）
  - → rejected.txt（繁簡雙形，雙閘全封）＋ reviewed_done.txt（簡形）
冪等：已在目的清單的詞不重錄；無批注時報告掛起數即退出。
轉抄後立即回讀三清單驗證義務全滿足，不滿足即非零退出。
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(HERE, 'dict_pipeline', 'archive', 'reports', 'TO_BE_REVIEWED.txt')
RUL = os.path.join(HERE, 'dict_pipeline', 'archive', 'rulings')
DRY = '--dry-run' in sys.argv


def words_of(path):
    """清單詞集：非註釋行按空白切，取每行首列。"""
    if not os.path.exists(path): return set()
    out = set()
    for l in open(path, encoding='utf-8'):
        l = l.split('#', 1)[0].strip()
        if l: out.add(l.split()[0])
    return out


def append(path, header, words):
    if not words: return
    with open(path, 'a', encoding='utf-8') as f:
        f.write(f'\n# {header}\n' + '\n'.join(sorted(words)) + '\n')


def main():
    if not os.path.exists(REP):
        sys.exit(f'找不到 {REP}（先跑 gen_freq_dict.py 生成）')
    rej_p = os.path.join(RUL, 'rejected.txt')
    rev_p = os.path.join(RUL, 'reviewed.txt')
    done_p = os.path.join(RUL, 'reviewed_done.txt')
    rejected, reviewed, done = words_of(rej_p), words_of(rev_p), words_of(done_p)

    plus, minus, pending = [], [], []
    for line in open(REP, encoding='utf-8'):
        line = line.rstrip('\n')
        if not line.strip() or line.startswith('#'):
            continue
        line = line.split('#', 1)[0]
        m = re.match(r'^([+\-?])\s+(\S+)\s*→\s*(\S+)', line.strip())
        if not m:
            continue
        mark, simp, trad = m.group(1), m.group(2), m.group(3)
        if mark == '+': plus.append((simp, trad))
        elif mark == '-': minus.append((simp, trad))
        else: pending.append((simp, trad))

    if not plus and not minus:
        print(f'無批注（掛起 {len(pending)} 詞）；不動三清單。')
        return

    import datetime
    today = datetime.date.today().isoformat()
    tag = f'TO_BE_REVIEWED 批注（{today}）'
    add_rev = {t for s, t in plus} - reviewed
    add_done = ({s for s, t in plus} | {s for s, t in minus}) - done
    add_rej = ({t for s, t in minus} | {s for s, t in minus}) - rejected
    print(f'批注統計：放行 {len(plus)}，否決 {len(minus)}，未批 {len(pending)}')
    print(f'轉抄：reviewed +{len(add_rev)}；rejected +{len(add_rej)}；reviewed_done +{len(add_done)}')
    if DRY:
        print('（--dry-run：不落盤）')
        return
    append(rev_p, tag + '：放行繁形入庫', add_rev)
    append(rej_p, tag + '：否決繁簡雙形，雙閘全封', add_rej)
    append(done_p, tag + '：定案簡形，不再重列', add_done)

    # 回讀自驗：義務全滿足
    rejected2, reviewed2, done2 = words_of(rej_p), words_of(rev_p), words_of(done_p)
    miss = (add_rev - reviewed2) | (add_done - done2) | (add_rej - rejected2)
    if miss:
        sys.exit(f'⛔ 自驗失敗，未入清單：{sorted(miss)[:5]}')
    print('自驗 ✓ 三清單義務全滿足。')


if __name__ == '__main__':
    main()
