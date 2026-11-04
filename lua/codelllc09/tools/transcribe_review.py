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
    ok, drop, pend = [], [], []
    for l in open(REP, encoding='utf-8'):
        m = re.match(r'^([+-?]) (\S+) → (\S+)\t', l)
        if not m: continue
        {'+': ok, '-': drop, '?': pend}[m.group(1)].append((m.group(2), m.group(3)))
    print(f'批注統計：放行 {len(ok)}｜否決 {len(drop)}｜未批 {len(pend)}')
    if pend:
        print('  （未批詞維持丟棄語義，不轉抄；想處理請批完再跑本腳本）')

    rw = words_of(os.path.join(RUL, 'reviewed.txt'))
    rj = words_of(os.path.join(RUL, 'rejected.txt'))
    dn = words_of(os.path.join(RUL, 'reviewed_done.txt'))
    add_rw = {t for _, t in ok} - rw
    add_dn = ({sp for sp, _ in ok} | {sp for sp, _ in drop}) - dn
    add_rj = ({t for _, t in drop} | {sp for sp, _ in drop}) - rj
    plan = [('reviewed.txt（繁形入庫）', add_rw), ('reviewed_done.txt（定案）', add_dn),
            ('rejected.txt（雙形否決）', add_rj)]
    for name, ws in plan:
        if ws:
            print(f'  將追加 {name}：{len(ws)} 詞')
            if not DRY:
                append(os.path.join(RUL, name.split("（")[0]), 'TO_BE_REVIEWED 批注轉抄', ws)
    if DRY:
        print('（dry-run：未寫盤）'); return

    # 回讀驗證：每條批注的義務必須滿足
    rw2, rj2, dn2 = (words_of(os.path.join(RUL, f)) for f in
                     ('reviewed.txt', 'rejected.txt', 'reviewed_done.txt'))
    bad = [f'+{t} 未入 reviewed' for _, t in ok if t not in rw2]
    bad += [f'-{sp}/{t} 未入 rejected' for sp, t in drop
            if sp not in rj2 or t not in rj2]
    bad += [f'{sp} 未入 reviewed_done' for sp, _ in ok + drop if sp not in dn2]
    for b in bad: print('  ✗', b)
    print('轉抄驗證：', '全滿足 ✓' if not bad else f'{len(bad)} 缺口 ✗')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
