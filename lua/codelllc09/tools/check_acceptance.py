#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_acceptance.py — 候選首選回歸基準校驗（2026-11-05，自詞頻驗收快照機化）

用法：cd tools && python3 check_acceptance.py
基準：dict_pipeline/archive/rulings/驗收基準.txt（code<TAB>期望首選）
判定：對每碼取詞表中**首個「基本區可見」活條目**（≤U+9FFF 全部字——
     模擬通用字集模式下用戶實際所見；擴展區單字在通用檔被閘，不佔首位）。
不符即印出並以非零碼退出——首選漂移即回歸，改動方須解釋或更新基準。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DICT = os.path.join(ROOT, 'xuma_L.dict.yaml')
BASE = os.path.join(HERE, 'dict_pipeline', 'archive', 'rulings', '驗收基準.txt')


def basic(s):
    return all(ord(c) <= 0x9FFF for c in s)


def firsts(codes):
    got, started = {}, False
    for line in open(DICT, encoding='utf-8'):
        line = line.rstrip('\n')
        if not started:
            if line == '...': started = True
            continue
        if not line or line.lstrip().startswith('#'): continue
        c = line.split('\t')
        if len(c) < 2: continue
        code = c[1].strip()
        if code in codes and code not in got and basic(c[0]):
            got[code] = c[0]
    return got


def main():
    exp, order = {}, []
    for line in open(BASE, encoding='utf-8'):
        line = line.split('#', 1)[0].strip()
        if not line or '\t' not in line: continue
        code, word = line.split('\t')[0].strip(), line.split('\t')[1].strip()
        exp[code] = word
        order.append(code)
    got = firsts(set(order))
    bad = [(c, exp[c], got.get(c, '∅')) for c in order if got.get(c) != exp[c]]
    for c, e, g in bad:
        print(f'[首選漂移] {c}: 基準 {e} → 現 {g}')
    print(f'驗收基準：{len(order)} 碼，{len(bad)} 漂移' + ('' if bad else ' ✓'))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
