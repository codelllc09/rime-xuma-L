#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_sources.py — 一鍵重建 sources/ 詞源區（2026-11-05 佈局）

淨室復現手冊見同目錄 BUILD.md；口徑四節全部在本腳本實現。
用法：uv run --with xlrd python build_sources.py [--force] [--no-net]
已存在文件自動跳過；--no-net 僅做在場件的清洗步（不下載）。
落盤計數記 IGNORED_CHANGES 台賬（--no-net/全跳過不記）。"""
import os, sys, io, zipfile, urllib.request, tempfile, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
FORCE = '--force' in sys.argv
NO_NET = '--no-net' in sys.argv
TMP = tempfile.mkdtemp(prefix='build_sources-')
written = []

RAW_JIEBA = 'https://raw.githubusercontent.com/fxsjy/jieba/master/jieba/dict.txt'
WHEEL_WORDFREQ = ('https://files.pythonhosted.org/packages/24/61/62835c475d69872d'
                  '7e5b1e483f6a4f6b0e6693a51e87c1d2e23c8a2b0f3a/wordfreq-3.1.1-py3-none-any.whl')
THUOCL_BASE = 'https://raw.githubusercontent.com/thunlp/THUOCL/master/data/'
THUOCL_LICENSE = 'https://raw.githubusercontent.com/thunlp/THUOCL/master/LICENSE'
BCC_BASE = 'https://bcc.blcu.edu.cn/api/datasets/{}/download'
BCC_SETS = {'D.bcc_multi_domain.txt': 'multi_domain_total_word_freq',
            'D.bcc_news.txt': 'news_total_word_freq',
            'G.bcc_literature.txt': 'literature_word_freq',
            'H.bcc_dialogue.txt': 'dialogue_word_freq',
            'I.bcc_modern_chinese.txt': 'modern_chinese_word_freq'}
SUBTLEX = 'https://www.lexique.org/databases/SUBTLEX-CH/SUBTLEX-CH-WF.tsv'
GBOOKS_GZ = 'https://storage.googleapis.com/books/ngrams/books/20200217/chi_sim/1-00000-of-00001.gz'
YUWEI_XLS = ('https://github.com/bedlate/cn-corpus/raw/main/'
             '%E7%8E%B0%E4%BB%A3%E6%B1%89%E8%AF%AD%E8%AF%AD%E6%96%99%E5%BA%93%E8%AF%8D%E9%A2%91%E8%A1%A8.xls')
# OpenCC 釘版（快照考古 2026-11-05：TS 雙件與 ST 雙件最後變更提交不同，分別釘住）
OPENCC = {'_mapping/TSCharacters.txt': '0137a24b', '_mapping/TSPhrases.txt': '0137a24b',
          '_mapping/STCharacters.txt': '3acca985', '_mapping/STPhrases.txt': '3acca985'}
OPENCC_BASE = ('https://raw.githubusercontent.com/BYVoid/OpenCC/{}/'
               'data/dictionary/{}')
UNIHAN_ZIP = 'https://www.unicode.org/Public/17.0.0/ucd/Unihan.zip'  # 釘 17.0.0（latest 會漂）
UPSTREAM = 'https://raw.githubusercontent.com/Ace-Who/rime-xuma/master/schema/xuma.dict.yaml'


def step(name):
    print(f'── {name}')


def exists(rel):
    return os.path.exists(os.path.join(HERE, rel)) and not FORCE


def place(rel, data):
    p = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(data)
    written.append(rel)
    print(f'   ↳ {rel}（{len(data)} bytes）')


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main():
    # ── A 源：jieba ──
    step('A jieba')
    if exists('A.jieba_dict.txt'):
        print('   skip')
    elif NO_NET:
        print('   --no-net skip')
    else:
        place('A.jieba_dict.txt', fetch(RAW_JIEBA))
    # ── B 源：wordfreq wheel 內單文件 ──
    step('B wordfreq')
    if exists('B.wordfreq_jieba_zh.txt'):
        print('   skip')
    elif NO_NET:
        print('   --no-net skip')
    else:
        whl = os.path.join(TMP, 'wordfreq.whl')
        open(whl, 'wb').write(fetch(WHEEL_WORDFREQ))
        with zipfile.ZipFile(whl) as z:
            place('B.wordfreq_jieba_zh.txt', z.read('wordfreq/data/jieba_zh.txt'))
    # ── C 源：THUOCL 11 表＋LICENSE（diming 剝 BOM/CRLF＋末行換行）──
    step('C THUOCL')
    for cat in ['IT', 'caijing', 'car', 'chengyu', 'diming', 'food', 'law',
                'lishimingren', 'medical', 'poem', 'animal']:
        rel = f'C.thuocl/THUOCL_{cat}.txt'
        if exists(rel) or NO_NET:
            continue
        data = fetch(THUOCL_BASE + f'THUOCL_{cat}.txt')
        if cat == 'diming':
            t = data.decode('utf-8-sig').replace('\r\n', '\n')
            if t.endswith('\n'):
                t = t[:-1]           # 首建歷史口徑：剝末行換行以對齊校驗單
            data = t.encode('utf-8')
        place(rel, data)
    if not exists('C.thuocl/LICENSE') and not NO_NET:
        place('C.thuocl/LICENSE', fetch(THUOCL_LICENSE))
    # ── D/G/H/I 源：BCC zip 解裹 ──
    step('D/G/H/I BCC')
    for fname, ds in BCC_SETS.items():
        if exists(fname) or NO_NET:
            continue
        zp = os.path.join(TMP, ds + '.zip')
        open(zp, 'wb').write(fetch(BCC_BASE.format(ds)))
        with zipfile.ZipFile(zp) as z:
            inner = [n for n in z.namelist() if n.endswith('.txt')]
            assert len(inner) == 1, inner
            place(fname, z.read(inner[0]))
    # ── E 源：SUBTLEX（.tsv 下載字節存 .txt）──
    step('E SUBTLEX')
    if not exists('E.subtlex/SUBTLEX-CH-WF.txt') and not NO_NET:
        place('E.subtlex/SUBTLEX-CH-WF.txt', fetch(SUBTLEX))
    # ── K 源：GoogleBooks 1-gram 聚合 ──
    step('K GoogleBooks')
    if exists('K.googlebooks/chi_1gram_word_freq.txt'):
        print('   skip')
    elif NO_NET:
        print('   --no-net skip')
    else:
        import gzip, re, collections
        gz = os.path.join(TMP, 'chi_1gram.gz')
        open(gz, 'wb').write(fetch(GBOOKS_GZ))
        agg = collections.Counter()
        HAN = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
                         r'\U00020000-\U0002ffff\U00030000-\U0003ffff]{2,5}$')
        with gzip.open(gz, 'rt', encoding='utf-8') as f:
            for line in f:
                # 官方格式：詞_POS 年,次,卷
                m = re.match(r'^(\S+?)_([A-Za-z]*) (\d{4}),(\d+),(\d+)$', line.rstrip('\n'))
                if not m:
                    continue
                word, pos, _yr, cnt, _vol = m.groups()
                if pos == '' or not pos.isalpha():
                    continue        # 空尾綴/點尾綴畸形行棄（口徑見 BUILD.md 四.1）
                if not HAN.match(word):
                    continue
                agg[word] += int(cnt)
        out = ''.join(f'{w}\t{c}\n' for w, c in sorted(agg.items(), key=lambda kv: -kv[1]))
        place('K.googlebooks/chi_1gram_word_freq.txt', out.encode('utf-8'))
    # ── L 源：語委 xls → TSV ──
    step('L 語委')
    if exists('L.yuwei_word_freq.txt'):
        print('   skip')
    elif NO_NET:
        print('   --no-net skip')
    else:
        xp = os.path.join(TMP, 'yuwei.xls')
        open(xp, 'wb').write(fetch(YUWEI_XLS))
        import xlrd
        book = xlrd.open_workbook(xp)
        sh = book.sheet_by_name('wordfreq_50')
        rows = []
        for r in range(sh.nrows):
            w = str(sh.cell_value(r, 0)).strip()
            c = str(sh.cell_value(r, 1)).strip()
            if not w or not c.isdigit() or w in ('詞語', '词语', '词组', '詞組'):
                continue
            rows.append(f'{w}\t{c}\n')
        place('L.yuwei_word_freq.txt', ''.join(rows).encode('utf-8'))
    # ── _mapping：OpenCC 釘版四件＋Unihan 兩件 ──
    step('_mapping')
    for rel, commit in OPENCC.items():
        if exists(rel) or NO_NET:
            continue
        place(rel, fetch(OPENCC_BASE.format(commit, os.path.basename(rel))).encode('utf-8'))
    if not NO_NET:
        if not (exists('_mapping/Unihan_Variants.txt') and exists('_mapping/Unihan_Readings.txt')):
            zp = os.path.join(TMP, 'Unihan.zip')
            open(zp, 'wb').write(fetch(UNIHAN_ZIP))
            with zipfile.ZipFile(zp) as z:
                place('_mapping/Unihan_Variants.txt', z.read('Unihan_Variants.txt'))
                place('_mapping/Unihan_Readings.txt', z.read('Unihan_Readings.txt'))
    # ── upstream ──
    step('upstream')
    if not exists('upstream_xuma.dict.yaml') and not NO_NET:
        place('upstream_xuma.dict.yaml', fetch(UPSTREAM))
    print(f'\n實落盤 {len(written)} 件' + ('' if written else '（全部在場跳過）'))
    # 台賬：有實際落盤才記（--no-net/全跳過不記）
    if written:
        lg = os.path.join(HERE, '..', '..', 'log', 'IGNORED_CHANGES.log')
        os.makedirs(os.path.dirname(lg), exist_ok=True)
        import datetime
        with open(lg, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\t"
                    f"corpus/sources/*\t重建\tbuild_sources 落盤 {len(written)} 件\n")


if __name__ == '__main__':
    main()
