#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_tendency.py — 詞表 × 用詞傾向表 一致性校驗（2026-11-05）

用法：cd tools && python3 check_tendency.py
讀 dict_pipeline/archive/rulings/用詞傾向.txt 與 xuma_L.dict.yaml，
輸出三類報告：
  [禁族·存量]  含族字活詞計數（歷史保留，僅通報；新增閘門在 gen_freq_dict 補詞階段）
  [偏好·雙活]  取形/捨形同時在表的詞（待裁：準則說捨形應注釋）
  [偏好·偏枯]  捨形在表而取形不在（潛在漏詞，待裁）
退出碼：「偏枯」或「混形」任一存在即 1；僅雙活/族內存量時 0（待裁非錯誤）。
"""
import os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DICT = os.path.join(HERE, '..', '..', '..', 'xuma_L.dict.yaml')
TEND = os.path.join(HERE, 'dict_pipeline', 'archive', 'rulings', '用詞傾向.txt')

prefs, fams, excepts = [], set(), set()
for line in open(TEND, encoding='utf-8'):
    line = line.split('#', 1)[0].strip()
    if not line: continue
    cols = line.split('\t')
    if cols[0] == '偏好' and len(cols) >= 3:
        prefs.append((cols[1], cols[2].split(',')))
    elif cols[0] == '禁族':
        fams.update(cols[1].split())
    elif cols[0] == '例外':
        excepts.update(cols[1].split())

act, com = set(), set()
started = False
for line in open(DICT, encoding='utf-8'):
    line = line.rstrip('\n')
    if not started:
        if line == '...': started = True
        continue
    if not line.strip(): continue
    t = line.lstrip('# ').split('\t')[0]
    if len(t) > 1:
        (com if line.lstrip().startswith('#') else act).add(t)

rc = 0
if fams:
    hits = collections.Counter(w for w in act if fams & set(w))
    print(f'[禁族·存量] 含族字活詞 {len(hits)} 個（歷史保留，僅通報）')
    for w, n in hits.most_common(5):
        print(f'    {w}（現 {n} 條）')
    print('    …' if len(hits) > 5 else '', end='\n')

# s2t 最長匹配（STPhrases 優先，STCharacters 兜底）——決定性判據
_S = os.path.join(HERE, 'dict_pipeline', 'corpus', 'sources', '_mapping')
_p, _c = {}, {}
for fn, d in (('STPhrases.txt', _p), ('STCharacters.txt', _c)):
    for line in open(os.path.join(_S, fn), encoding='utf-8'):
        line = line.split('#', 0)[0] if False else line
        line = line.split('#')[0].strip()
        if not line or '\t' not in line: continue
        cols = line.split('\t')
        d.setdefault(cols[0], cols[1].split()[0])

def s2t(x):
    out, i, n = [], 0, len(x)
    while i < n:
        for L in (min(4, n - i), 3, 2, 1):
            seg = x[i:i + L]
            if seg in _p: out.append(_p[seg]); i += L; break
        else:
            out.append(_c.get(x[i], x[i])); i += 1
    return ''.join(out)

FAM = set('裏里周週個箇')

for take, drops in prefs:
    both = lone_take = lone_drop = 0
    ex_both, ex_lone_drop = [], []
    for w in act:
        if any(d in w for d in drops):
            counterpart = w
            for d in drops:
                counterpart = counterpart.replace(d, take)
            if counterpart in act: both += 1; ex_both.append(w)
            elif w in com or counterpart in com:
                pass  # 捨形活且對照注釋存在：仍屬雙活（準則說捨形應注釋）
            else: lone_drop += 1; ex_lone_drop.append(w)  # 捨活取無取注
        elif take in w:
            pass  # 取形活：正常
    tag = '/'.join(drops)
    # 混形：取形詞經 s2t 規整（捨形還原爲取形）後應與自身一致
    mixed = fam_keep = 0
    ex_mixed = []
    for w in act:
        if take not in w or w in excepts: continue
        want = s2t(w)
        for d in drops: want = want.replace(d, take)
        if w == want: continue
        diff = {a for a, b in zip(w, want) if a != b} | {b for a, b in zip(w, want) if a != b}
        if diff <= FAM: fam_keep += 1
        else:
            mixed += 1
            ex_mixed.append(f'{w}→{want}')
    print(f'[偏好 {take}>{tag}] 雙活 {both}；捨形獨活 {lone_drop}；'
          f'混形 {mixed}（應注釋）；族內存量 {fam_keep}（不動）')
    for x in (ex_both[:2] + ex_lone_drop[:2] + ex_mixed[:4]):
        print(f'    例：{x}')
    if lone_drop or mixed:
        rc = 1
sys.exit(rc)
