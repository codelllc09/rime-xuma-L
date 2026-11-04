#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_sources.py — 一鍵重建 sources/ 詞源區（2026-11-05 佈局）

用法（在本文件所在目錄執行；僅 L 源需 xlrd，uv 自動掛載）：
    cd .../dict_pipeline/corpus/sources
    uv run --with xlrd python build_sources.py            # 全量重建（已存在的文件跳過）
    uv run --with xlrd python build_sources.py --force    # 強制重下重做
    python build_sources.py --no-net                      # 只打印將要做的步驟

產物佈局（與 SHA256SUMS 對齊）：
    A.jieba_dict.txt            jieba/dict.txt 原樣（master 快照，滾動源）
    B.wordfreq_jieba_zh.txt     wordfreq 3.1.1 wheel 內 data/jieba_zh.txt 原樣
    C.thuocl/THUOCL_*.txt + LICENSE   原樣，唯 diming 需 BOM 剝離 + CRLF→LF
    D.bcc_multi_domain.txt      BCC zip 內裹 txt 原樣（token,count CSV）
    D.bcc_news.txt              同上；D 源 = 兩文件 max 合併（管道層職責，不在本腳本）
    G/H/I.bcc_*.txt             同上（文學/口語/近代頻道）
    E.subtlex/SUBTLEX-CH-WF.txt 原樣字節（GB18030 編碼，管道讀取時轉碼）
    K.googlebooks/chi_1gram_word_freq.txt   由官方 gz 聚合：剝 _POS 後綴、
                                純漢字 2–5 字、年份求和、按總次降序（詳 aggregate_gbooks）
    L.yuwei_word_freq.txt       國家語委 xls → 詞\t頻 兩列 TSV（行序照原表）
    _mapping/TS·ST×4            OpenCC data/dictionary 原樣（master，滾動源）
    _mapping/Unihan_*.txt       Unihan.zip 解出兩文件（latest，滾動源）
    upstream_xuma.dict.yaml     rime-xuma schema/xuma.dict.yaml 原樣（master，滾動源）

驗收：cd .. && sha256sum -c SHA256SUMS
    靜態源（B/C/D/E/G/H/I/K/L）應逐字節一致；
    滾動源（A/_mapping/upstream）若上游已更新則哈希不合，屬預期，以校驗單為本機快照憑據。
"""
import os, sys, io, re, csv, gzip, zipfile, subprocess, shutil, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NET = '--force' not in sys.argv and '--no-net' not in sys.argv
FORCE = '--force' in sys.argv
TMP = os.path.join(HERE, '.build_tmp')

RAW_JIEBA = 'https://raw.githubusercontent.com/fxsjy/jieba/master/jieba/dict.txt'
WHEEL_WORDFREQ = ('https://files.pythonhosted.org/packages/24/61/62835c475d69872d'
                  '30689f284497853fe33fe1d6dd18f57346d13305861d/wordfreq-3.1.1-py3-none-any.whl')
THUOCL_BASE = 'https://raw.githubusercontent.com/thunlp/THUOCL/master/data/'
THUOCL_FILES = ['THUOCL_IT.txt', 'THUOCL_animal.txt', 'THUOCL_caijing.txt',
                'THUOCL_car.txt', 'THUOCL_chengyu.txt', 'THUOCL_diming.txt',
                'THUOCL_food.txt', 'THUOCL_law.txt', 'THUOCL_lishimingren.txt',
                'THUOCL_medical.txt', 'THUOCL_poem.txt']
THUOCL_LICENSE = 'https://raw.githubusercontent.com/thunlp/THUOCL/master/LICENSE'
BCC_BASE = 'https://bcc.blcu.edu.cn/api/datasets/{}/download'
BCC_MAP = {  # 下載名（API 數據集名）→ 本地目標
    'multi_domain_total_word_freq.txt': 'D.bcc_multi_domain.txt',
    'news_total_word_freq.txt':         'D.bcc_news.txt',
    'literature_word_freq.txt':         'G.bcc_literature.txt',
    'dialogue_word_freq.txt':           'H.bcc_dialogue.txt',
    'modern_chinese_word_freq.txt':     'I.bcc_modern_chinese.txt',
}
SUBTLEX = 'https://www.lexique.org/databases/SUBTLEX-CH/SUBTLEX-CH-WF.tsv'
GBOOKS_GZ = 'https://storage.googleapis.com/books/ngrams/books/20200217/chi_sim/1-00000-of-00001.gz'
YUWEI_XLS = ('https://github.com/bedlate/cn-corpus/raw/master/'
             '%E7%8E%B0%E4%BB%A3%E6%B1%89%E8%AF%AD%E8%AF%AD%E6%96%99%E5%BA%93%E8%AF%8D%E9%A2%91%E8%A1%A8.xls')
OPENCC_BASE = 'https://raw.githubusercontent.com/BYVoid/OpenCC/master/data/dictionary/'
UNIHAN_ZIP = 'https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip'
UPSTREAM = 'https://raw.githubusercontent.com/Ace-Who/rime-xuma/master/schema/xuma.dict.yaml'


def fetch(url, dest):
    """curl 下載（跟隨重定向）；dest 為絕對路徑。"""
    print(f'  ↓ {url.rsplit("/", 1)[-1][:44]:<44} → {os.path.relpath(dest, HERE)}')
    subprocess.run(['curl', '-sSfL', '--retry', '3', '-o', dest, url], check=True)


def place(rel, data, binary=True):
    p = os.path.join(HERE, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb' if binary else 'w', **({} if binary else {'encoding': 'utf-8', 'newline': ''})) as f:
        f.write(data)
    print(f'  ✓ {rel}')


def exists(rel):
    p = os.path.join(HERE, rel)
    return os.path.exists(p) and not FORCE


def step(name):
    print(f'[{name}]')


# ── A 源：jieba（滾動）────────────────────────────────────────────
def build_a():
    step('A jieba')
    if exists('A.jieba_dict.txt'): return skip()
    t = os.path.join(TMP, 'jieba.txt'); fetch(RAW_JIEBA, t)
    place('A.jieba_dict.txt', open(t, 'rb').read())


# ── B 源：wordfreq wheel 內提取（靜態，版本釘 3.1.1）──────────────
def build_b():
    step('B wordfreq')
    if exists('B.wordfreq_jieba_zh.txt'): return skip()
    t = os.path.join(TMP, 'wordfreq.whl'); fetch(WHEEL_WORDFREQ, t)
    with zipfile.ZipFile(t) as z:
        place('B.wordfreq_jieba_zh.txt', z.read('wordfreq/data/jieba_zh.txt'))


# ── C 源：THUOCL（靜態快照；diming 需規範化）──────────────────────
def build_c():
    step('C THUOCL')
    need = [f for f in THUOCL_FILES if not exists(f'C.thuocl/{f}')]
    if not need and exists('C.thuocl/LICENSE'): return skip()
    for f in need:
        t = os.path.join(TMP, f); fetch(THUOCL_BASE + f, t)
        raw = open(t, 'rb').read()
        if f == 'THUOCL_diming.txt':
            if raw.startswith(b'\xef\xbb\xbf'): raw = raw[3:]      # 剝 BOM
            raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')  # CRLF→LF
        place(f'C.thuocl/{f}', raw)
    if FORCE or not exists('C.thuocl/LICENSE'):
        t = os.path.join(TMP, 'LICENSE'); fetch(THUOCL_LICENSE, t)
        place('C.thuocl/LICENSE', open(t, 'rb').read())


# ── D/G/H/I 源：BCC（API 下發 zip 內裹 txt）───────────────────────
def build_bcc():
    step('D/G/H/I BCC')
    todo = {k: v for k, v in BCC_MAP.items() if not exists(v)}
    if not todo: return skip()
    for api_name, dest in todo.items():
        t = os.path.join(TMP, api_name + '.zip'); fetch(BCC_BASE.format(api_name), t)
        with zipfile.ZipFile(t) as z:
            inner = [n for n in z.namelist() if n.endswith('.txt')]
            assert len(inner) == 1, f'{api_name}: zip 內 txt 數 {len(inner)}'
            place(dest, z.read(inner[0]))


# ── E 源：SUBTLEX-CH（原樣字節，GB18030 保留給管道轉碼）───────────
def build_e():
    step('E SUBTLEX')
    if exists('E.subtlex/SUBTLEX-CH-WF.txt'): return skip()
    t = os.path.join(TMP, 'subtlex.tsv'); fetch(SUBTLEX, t)
    place('E.subtlex/SUBTLEX-CH-WF.txt', open(t, 'rb').read())


# ── K 源：Google Books 1-gram 聚合（20200217 版，靜態）────────────
CJK = re.compile(r'^[\u3400-\u9FFF\uF900-\uFAFF' + ''.join(
    chr(cp) for cp in ()) + r']+$')  # 佔位，build_k 內動態構造

def build_k():
    step('K GoogleBooks 聚合')
    if exists('K.googlebooks/chi_1gram_word_freq.txt'): return skip()
    t = os.path.join(TMP, 'gbooks.gz'); fetch(GBOOKS_GZ, t)
    place('K.googlebooks/chi_1gram_word_freq.txt', aggregate_gbooks(t).encode('utf-8'))


def aggregate_gbooks(gz_path):
    """官方 1-gram（word[_POS]\\t年,次 年,次…）→ 詞\\t總次 降序 TSV。

    規則（與 2026-11-05 首建一致，經 SHA256SUMS 校準）：
      1) 詞形剝 _POS 後綴（取第一個 '_' 前）；
      2) 僅留 2–5 字純漢字詞（CJK 擴展 A/B 兼收）；
      3) 同詞跨年份、跨詞性合併求和；
      4) 按總次降序輸出 詞\\t總次。
    """
    han = re.compile(r'^[\u3400-\u9FFF\U00020000-\U0002FFFF\U00030000-\U0003FFFF'
                     r'\U00020000-\U0002A6DF\U0002A700-\U0002EBEF]'
                     r'|\U00020000')  # 由下方逐字碼位判斷取代正則
    import collections
    total = collections.Counter()
    with gzip.open(gz_path, 'rt', encoding='utf-8', errors='replace') as f:
        for line in f:
            tok, _, rest = line.partition('\t')
            w = tok.split('_', 1)[0]
            if not (2 <= len(w) <= 5):
                continue
            if not all(is_han(c) for c in w):
                continue
            n = 0
            for pair in rest.split():
                _, _, c = pair.rpartition(',')
                n += int(c)
            total[w] += n
    out = io.StringIO()
    for w, n in total.most_common():
        out.write(f'{w}\t{n}\n')
    return out.getvalue()


def is_han(ch):
    cp = ord(ch)
    return (0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF
            or 0x20000 <= cp <= 0x2FA1F or 0x30000 <= cp <= 0x3134A)


# ── L 源：國家語委 xls → TSV（靜態）───────────────────────────────
def build_l():
    step('L 語委')
    if exists('L.yuwei_word_freq.txt'): return skip()
    t = os.path.join(TMP, 'yuwei.xls'); fetch(YUWEI_XLS, t)
    import xlrd
    wb = xlrd.open_workbook(t)
    sh = wb.sheet_by_index(0)
    buf = io.StringIO()
    for r in range(sh.nrows):
        word = str(sh.cell_value(r, 0)).strip()
        freq = sh.cell_value(r, 1)
        if not word: continue
        freq = int(freq) if isinstance(freq, float) else str(freq).strip()
        buf.write(f'{word}\t{freq}\n')
    place('L.yuwei_word_freq.txt', buf.getvalue())


# ── _mapping：OpenCC + Unihan（滾動）──────────────────────────────
def build_mapping():
    step('_mapping OpenCC+Unihan')
    occ = {'TSCharacters.txt', 'TSPhrases.txt', 'STCharacters.txt', 'STPhrases.txt'}
    todo = [f for f in occ if not exists(f'_mapping/{f}')]
    for f in todo:
        t = os.path.join(TMP, f); fetch(OPENCC_BASE + f, t)
        place(f'_mapping/{f}', open(t, 'rb').read())
    if todo or FORCE or not (exists('_mapping/Unihan_Variants.txt')
                             and exists('_mapping/Unihan_Readings.txt')):
        t = os.path.join(TMP, 'Unihan.zip'); fetch(UNIHAN_ZIP, t)
        with zipfile.ZipFile(t) as z:
            place('_mapping/Unihan_Variants.txt', z.read('Unihan_Variants.txt'))
            place('_mapping/Unihan_Readings.txt', z.read('Unihan_Readings.txt'))
    if not todo: print('  （OpenCC 已齊）')


# ── upstream：補詞基準（滾動）─────────────────────────────────────
def build_upstream():
    step('upstream')
    if exists('upstream_xuma.dict.yaml'): return skip()
    t = os.path.join(TMP, 'xuma.dict.yaml'); fetch(UPSTREAM, t)
    place('upstream_xuma.dict.yaml', open(t, 'rb').read())


def skip():
    print('  （已存在，跳過；--force 重做）')


def main():
    if not NET: print('（--no-net：僅列步驟）')
    os.makedirs(TMP, exist_ok=True)
    for fn in (build_a, build_b, build_c, build_bcc, build_e, build_k,
               build_l, build_mapping, build_upstream):
        if NET: fn()
        else: print(fn.__name__, '→', fn.__doc__.strip()[:40] if fn.__doc__ else '')
    shutil.rmtree(TMP, ignore_errors=True)
    print('\n驗收：cd .. && sha256sum -c SHA256SUMS')


if __name__ == '__main__':
    main()
