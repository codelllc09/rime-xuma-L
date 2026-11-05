#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""learn_weights.py — 語源權重學習【原型】（2026-11-05）

想法：詞頻 = log10(Σ wᵢ·pᵢ)，十個 wᵢ 現在是手調的常數。人工調序（manual_overrides）
本身是一批**成對偏好標籤**（A 應排在 B 前面），可以用來反推怎樣的 w 最符合你的語感。

本原型只做三件事，不做承諾：
  1. 用當前權重，逐條檢查每對偏好是否已被滿足（基線）
  2. 以 softmax 參數化 + logistic 成對損失做梯度下降，擬合出一組 w
  3. 用 **留一交叉驗證** 誠實報告擬合效果——標籤只有十幾對、參數有十個，
     過擬合幾乎必然，這一步是為了把它量化出來而不是掩蓋

用法：
    python3 learn_weights.py [--iters 4000] [--lr 0.15] [--json]

注意：不修改任何詞庫檔案，只輸出報告。
"""
import argparse
import importlib
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

g = importlib.import_module('gen_freq_dict')   # 模組層無副作用，可安全 import

OV = os.path.join(HERE, 'dict_pipeline', 'manual_overrides.txt')


# ── 從 gen_freq_dict.py 讀出語源定義（避免與管道分叉）────────────
def parse_sources():
    """解析 SRC 元組，得到 [(key, name, weight)]；表格本身另行加載。"""
    src = open(g.__file__, encoding='utf-8').read()
    out = []
    for m in re.finditer(r"\('([a-z])',\s*'([^']+)',.*?,\s*([0-9.]+)\)", src):
        out.append((m.group(1), m.group(2), float(m.group(3))))
    seen = {}
    for k, n, w in out:            # 同名可能出現多次（D 源兩表），取首次
        seen.setdefault(k, (n, w))
    return [(k, n, w) for k, (n, w) in seen.items()]


def pmax(f1, f2):
    t1 = sum(f1.values()) or 1
    t2 = sum(f2.values()) or 1
    m = {}
    for tbl, t in ((f1, t1), (f2, t2)):
        for w, c in tbl.items():
            if c > 0:
                v = c / t
                if v > m.get(w, 0):
                    m[w] = v
    return {w: round(v * 1e9) for w, v in m.items()}


LOADERS = {
    'a': lambda: g.load_jieba(),
    'b': lambda: g.load_wordfreq(),
    'c': lambda: g.load_thuocl(),
    'd': lambda: pmax(g.load_bcc('D.bcc_multi_domain.txt'), g.load_bcc('D.bcc_news.txt')),
    'e': lambda: g.load_subtlex(),
    'g': lambda: g.load_bcc('G.bcc_literature.txt'),
    'h': lambda: g.load_bcc('H.bcc_dialogue.txt'),
    'i': lambda: g.load_bcc('I.bcc_modern_chinese.txt'),
    'k': lambda: g.load_googlebooks(),
    'l': lambda: g.load_yuwei(),
}


# ── 偏好標籤 ────────────────────────────────────────────────────
def load_pairs():
    """manual_overrides 的名次語法 A > B > C 展開為全部成對偏好。"""
    pairs = []
    if not os.path.exists(OV):
        return pairs
    for line in open(OV, encoding='utf-8'):
        body = line.split('#')[0].strip()
        if not body:
            continue
        ws = [w for w in re.split(r'\s*>\s*', body) if w]
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                pairs.append((ws[i], ws[j]))
    return pairs


# ── 打分 ────────────────────────────────────────────────────────
def build_probs(words, srcs, t2s):
    """返回 {word: [p_i...]}，缺失源為 0。"""
    tabs = {k: LOADERS[k]() for k, _, _ in srcs}
    tot = {k: (sum(t.values()) or 1) for k, t in tabs.items()}
    out = {}
    for w in words:
        s = t2s(w) if t2s else w
        out[w] = [(tabs[k].get(s, 0) / tot[k]) for k, _, _ in srcs]
    return out


def score(probs, theta):
    acc = 0.0
    for p, w in zip(probs, theta):
        if p > 0:
            acc += w * p
    return math.log10(acc) if acc > 0 else None


def softmax(z):
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


def sigmoid(x):
    if x > 40:
        return 1.0
    if x < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def accuracy(probs, theta, pairs):
    ok = tot = 0
    for a, b in pairs:
        sa, sb = score(probs[a], theta), score(probs[b], theta)
        if sa is None or sb is None:
            continue
        tot += 1
        ok += 1 if sa > sb else 0
    return ok, tot


# ── 訓練 ────────────────────────────────────────────────────────
def fit(probs, pairs, iters=4000, lr=0.15):
    n = len(next(iter(probs.values())))
    z = [0.0] * n                      # softmax 參數，等權初始化
    for it in range(iters):
        theta = softmax(z)
        gz = [0.0] * n
        for a, b in pairs:
            sa, sb = score(probs[a], theta), score(probs[b], theta)
            if sa is None or sb is None:
                continue
            d = sa - sb                # >0 表示已滿足
            p = sigmoid(d)
            # 對 -log σ(d) 求導：-σ(-d) * ∂d/∂w
            coef = -(1 - p)
            for i in range(n):
                # ∂score/∂w_i = p_i / (ln10 · Σ w p)
                pa, pb = probs[a][i], probs[b][i]
                den_a = 10 ** sa if sa is not None else 1
                den_b = 10 ** sb if sb is not None else 1
                gz[i] += coef * (pa / (2.302585 * den_a) - pb / (2.302585 * den_b))
        step = lr / (1 + it / 500)
        for i in range(n):
            gz[i] -= sum(gz) / n       # 去掉整體縮放方向，穩定 softmax
            z[i] -= step * gz[i]
    return softmax(z)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--iters', type=int, default=3000)
    ap.add_argument('--lr', type=float, default=0.3)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    srcs = parse_sources()
    pairs = load_pairs()
    words = sorted({w for p in pairs for w in p})
    t2s = None
    try:
        t2s, _, _ = g.load_t2s()
    except Exception:
        pass
    probs = build_probs(words, srcs, t2s)

    cur = [w for _, _, w in srcs]
    ok0, n0 = accuracy(probs, cur, pairs)
    fitw = fit(probs, pairs, iters=a.iters, lr=a.lr)
    ok1, n1 = accuracy(probs, fitw, pairs)

    # 留一交叉驗證：去掉第 i 對訓練，預測它
    loo_ok = loo_tot = 0
    for i in range(len(pairs)):
        sub = pairs[:i] + pairs[i + 1:]
        th = fit(probs, sub, iters=max(400, a.iters // 5), lr=a.lr)
        sa, sb = score(probs[pairs[i][0]], th), score(probs[pairs[i][1]], th)
        if sa is None or sb is None:
            continue
        loo_tot += 1
        loo_ok += 1 if sa > sb else 0

    rep = {
        'sources': [{'key': k, 'name': n, 'current': w, 'learned': round(f, 4)}
                    for (k, n, w), f in zip(srcs, fitw)],
        'pairs': len(pairs), 'words': len(words),
        'baseline': {'ok': ok0, 'of': n0, 'pct': round(ok0 / n0 * 100, 1) if n0 else None},
        'fitted': {'ok': ok1, 'of': n1, 'pct': round(ok1 / n1 * 100, 1) if n1 else None},
        'loo': {'ok': loo_ok, 'of': loo_tot,
                'pct': round(loo_ok / loo_tot * 100, 1) if loo_tot else None},
        'params': len(srcs),
    }
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return

    print(f'偏好標籤 {rep["pairs"]} 對（涉及 {rep["words"]} 個詞）｜參數 {rep["params"]} 個')
    print(f'當前權重滿足 {rep["baseline"]["ok"]}/{rep["baseline"]["of"]}'
          f'（{rep["baseline"]["pct"]}%）')
    print(f'擬合後滿足 {rep["fitted"]["ok"]}/{rep["fitted"]["of"]}'
          f'（{rep["fitted"]["pct"]}%）')
    print(f'留一交叉驗證   {rep["loo"]["ok"]}/{rep["loo"]["of"]}（{rep["loo"]["pct"]}%）'
          ' ← 這一欄才反映「換一批詞還準不準」')
    print()
    print(f'{"源":<12}{"當前":>8}{"擬合":>10}{"倍率":>10}')
    for s in rep['sources']:
        r = s['learned'] / s['current'] if s['current'] else 0
        print(f'{s["name"]:<12}{s["current"]:>8.3f}{s["learned"]:>10.4f}'
              f'{"×%.2f" % r:>10}')
    print()
    print('判讀：')
    if rep['pairs'] <= 2 * rep['params']:
        print(f'  標籤 {rep["pairs"]} 對 < 參數 {rep["params"]} × 2 —— 模型不可辨識，'
              '擬合結果不可用；需要更多偏好樣本（至少數百對）才有意義。')
    if rep['loo']['pct'] is not None and rep['baseline']['pct'] is not None:
        delta = rep['loo']['pct'] - rep['baseline']['pct']
        print(f'  交叉驗證相對基線 {delta:+.1f} 個百分點；'
              + ('有提升，但仍不足以取代人工。' if delta > 5 else '無顯著提升。'))


if __name__ == '__main__':
    main()
