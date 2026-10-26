#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_freq_dict.py — 徐碼詞頻重排流水線（生成新詞典副本 + dry-run 報告）

原則（2026-09-08 起自舉式，字碼與詞頻職責分離）：
  1. 輸入＝現役 xuma_L.dict.yaml 自身：單字區（字碼）與一簡/二簡/三碼區逐字節原樣繼承。
     字碼是靜態資產（基本百年不變），僅經手動編輯詞典修改，本流水線永不觸碰。
  2. 流水線只管詞頻（動態資產，隨體驗調整）：對「四碼純漢字詞組」逐碼位重排
     （含 manual_overrides 人工次序），含符號詞、格式異常條目一律原序原位。
  3. legacy/ 三表為 2026-09-07 前的首建輸入，現僅作審計基準（守恆報告），不再參與構建；
     char_code_fixes 機制已廢除（衡 sqkx/sqk 等修正已固化進詞典）。
  4. 排序鍵：jieba 詞頻降序（查不到者沉底）→ 舊權重降序 → 原表原行號。
     查到頻次的條目，weight 列改寫為 jieba 頻次；查不到者保留舊 weight。
"""
import re, sys, os, datetime
from collections import defaultdict

RIME = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
FREQ = os.path.join(os.path.dirname(__file__), 'dict_pipeline')
CORPUS = os.path.join(FREQ, 'corpus')
_warned = set()
def _miss(name):
    """語料文件缺失警告（每文件一次）；恢復途徑見 dict_pipeline/corpus/README.md"""
    if name not in _warned:
        _warned.add(name)
        print(f'⚠ 無語料模式：{name} 缺失（詞頻/簡繁/異體功能相應休眠）；恢復途徑見 dict_pipeline/corpus/README.md')
    return False
ARCHIVE = os.path.join(FREQ, 'archive')

CODE_RE = re.compile(r'^[a-z]{1,4}$')

def is_hanzi(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or      # BMP+ExtA
            0x20000 <= o <= 0x2FFFF or 0x30000 <= o <= 0x3FFFF or  # ExtB..ExtG
            0xF900 <= o <= 0xFAFF)                                  # 兼容表意

def load_dict_yaml(path):
    """返回 (header_lines, entries)；entry=(line_no, text, code, weight_str, raw_line)"""
    header, entries, in_data = [], [], False
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            line = line.rstrip('\n')
            if not in_data:
                header.append(line)
                if line.strip() == '...':
                    in_data = True
                continue
            if not line.strip() or line.lstrip().startswith('#'):
                entries.append((i, '', '', '', line))   # 註釋/空行原位保留
                continue
            cols = line.split('\t')
            if len(cols) < 2:
                continue
            text, code = cols[0], cols[1]
            weight = cols[2] if len(cols) > 2 and cols[2] else None
            entries.append((i, text, code, weight, line))
    return header, entries

def load_t2s():
    """OpenCC 繁→簡：詞級優先，字級兜底；多候選取第一個。"""
    phrase, char = {}, {}
    for path, d in ((os.path.join(CORPUS, 'TSPhrases.txt'), phrase),
                    (os.path.join(CORPUS, 'TSCharacters.txt'), char)):
        if not os.path.exists(path):
            _miss(os.path.basename(path)); continue
        with open(path, encoding='utf-8') as f:
            for line in f:
                if line.startswith('#') or '\t' not in line:
                    continue
                k, v = line.rstrip('\n').split('\t', 1)
                first = v.split(' ')[0] if v.split(' ') else ''
                if k and first:
                    d[k] = first
    def t2s(s):
        if s in phrase:
            return phrase[s]
        out = []
        for ch in s:
            out.append(char.get(ch, ch))
        return ''.join(out)
    return t2s, len(phrase), len(char)

import math

def load_wordfreq():
    """wordfreq 的 zh 詞頻表（OpenSubtitles+Twitter 聚合）：詞 + 空格 + 計數"""
    path = os.path.join(CORPUS, 'pylibs', 'wordfreq', 'data', 'jieba_zh.txt')
    if not os.path.exists(path):
        print('⚠ 無語料模式：wordfreq 數據缺失（' + path + '），詞頻功能休眠；'
              '恢復途徑見 dict_pipeline/corpus/README.md')
        return {}
    freq = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\r\n')
            parts = line.split(' ')
            if len(parts) >= 2 and parts[1].isdigit():
                freq[parts[0]] = int(parts[1])
    return freq

def load_jieba():
    freq = {}
    if not os.path.exists(os.path.join(CORPUS, 'jieba_dict.txt')):
        print('⚠ 無語料模式：jieba 詞表缺失，詞頻功能休眠；恢復途徑見 dict_pipeline/corpus/README.md')
        return freq
    with open(os.path.join(CORPUS, 'jieba_dict.txt'), encoding='utf-8') as f:
        for line in f:
            parts = line.split(' ')
            if len(parts) >= 2 and parts[1].isdigit():
                freq[parts[0]] = int(parts[1])
    return freq

def logaddexp10(a, b):
    """log10(10^a + 10^b)"""
    hi, lo = (a, b) if a >= b else (b, a)
    return hi + math.log10(1 + 10 ** (lo - hi))

def load_s2t():
    """OpenCC 簡→繁：四級遞降——詞組級權威 → 參照庫唯一命中 → 用戶字頻懸殊自動定形 → 待審"""
    for _fn in ('STPhrases.txt', 'STCharacters.txt'):
        if not os.path.exists(os.path.join(CORPUS, _fn)):
            _miss(_fn + '（簡→繁）'); return None, None, None
    phrase, char = {}, {}
    for fn, d in (('STPhrases.txt', phrase), ('STCharacters.txt', char)):
        with open(os.path.join(CORPUS, fn), encoding='utf-8') as f:
            for line in f:
                if line.startswith('#') or '\t' not in line: continue
                k, v = line.rstrip('\n').split('\t', 1)
                vs = v.split(' ')
                d[k] = vs[0] if d is phrase else vs
    def make(ref_texts, user_cf, simp_keys):
        import itertools
        def s2t(s):
            if s in phrase:
                return phrase[s], []                      # ① 詞組級權威（含譯名、常用歧義詞）
            cands, amb_chars = [], []
            for ch in s:
                vs = char.get(ch)
                cands.append(vs if vs else [ch])
                if vs and len(vs) > 1: amb_chars.append(ch)
            if not amb_chars:
                return ''.join(c[0] for c in cands), []
            n = 1
            for c in cands: n *= len(c)
            if n <= 64:
                combos = [''.join(p) for p in itertools.product(*cands)]
                hits = [c for c in combos
                        if c in ref_texts and not any(ch in simp_keys for ch in c)]
                if len(hits) == 1:
                    return hits[0], []                      # ② 參照庫唯一命中
                if len(hits) > 1:
                    return hits[0], ['多形並存']            # 多命中（罕見）待審
            # ③ 零命中：逐字按用戶庫字頻懸殊自動定形（≥5:1 且 ≥5 次）
            out, pending = [], []
            for ch in s:
                vs = char.get(ch)
                if not vs or len(vs) == 1:
                    out.append(vs[0] if vs else ch); continue
                fr = sorted(((user_cf.get(v, 0), v) for v in vs), reverse=True)
                if fr[0][0] >= 5 * max(fr[1][0], 1) and fr[0][0] >= 5:
                    out.append(fr[0][1])
                else:
                    pending.append(ch); out.append(vs[0])
            return ''.join(out), pending                    # ④ 有字無法定形 → 待審
        return s2t
    return make, phrase, char

def load_variants():
    """Unihan kSemanticVariant（雙向語義變體）→ 字→候選變體列表（已 t2s 歸一到簡體域）"""
    import unicodedata
    t2s_chr = None
    # 字級繁→簡映射（復用 TSCharacters）
    char = {}
    if not os.path.exists(os.path.join(CORPUS, 'TSCharacters.txt')):
        _miss('TSCharacters.txt'); return {}
    with open(os.path.join(CORPUS, 'TSCharacters.txt'), encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or '\t' not in line: continue
            k, v = line.rstrip('\n').split('\t', 1)
            first = v.split(' ')[0] if v.split(' ') else ''
            if k and first: char[k] = first
    def s2simp(x): return char.get(x, x)
    graph = defaultdict(set)
    pat = re.compile(r'U\+([0-9A-Fa-f]+)')
    if not os.path.exists(os.path.join(CORPUS, 'Unihan_Variants.txt')):
        _miss('Unihan_Variants.txt'); return {}
    with open(os.path.join(CORPUS, 'Unihan_Variants.txt'), encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 3 or parts[1] not in ('kSemanticVariant', 'kZVariant', 'kSpecializedSemanticVariant'):
                continue
            a = chr(int(parts[0][2:], 16))
            for m in pat.finditer(parts[2]):
                try: b = chr(int(m.group(1), 16))
                except ValueError: continue
                if b:
                    graph[a].add(b); graph[b].add(a)
    return {k: sorted({s2simp(v) for v in vs} - {s2simp(k)}) for k, vs in graph.items()}

def load_variants_raw():
    """Unihan 異體圖（原始域，不做簡體化）——用於用戶字形偏好歸一"""
    graph = defaultdict(set)
    pat = re.compile(r'U\+([0-9A-Fa-f]+)')
    if not os.path.exists(os.path.join(CORPUS, 'Unihan_Variants.txt')):
        _miss('Unihan_Variants.txt'); return {}
    with open(os.path.join(CORPUS, 'Unihan_Variants.txt'), encoding='utf-8') as f:
        for line in f:
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 3 or parts[1] not in ('kSemanticVariant', 'kZVariant', 'kSpecializedSemanticVariant'):
                continue
            a = chr(int(parts[0][2:], 16))
            for m in pat.finditer(parts[2]):
                try: b = chr(int(m.group(1), 16))
                except ValueError: continue
                if b: graph[a].add(b); graph[b].add(a)
    return {k: sorted(v) for k, v in graph.items()}

def make_scorer():
    """雙語源 1:1 混合：score = log10(0.5*p_jieba + 0.5*p_wf)"""
    jieba = load_jieba()
    wf = load_wordfreq()
    tj = sum(jieba.values())
    tw = sum(wf.values())
    if tj == 0 and tw == 0:
        return (lambda s: (None, 'nocorpus')), 0, 0   # 無語料模式：頻分恆缺失
    lg_tj, lg_tw = math.log10(tj), math.log10(tw)
    def score(s):
        """輸入簡體詞，返回 (score_logp 或 None, 語源標簽)"""
        c1 = jieba.get(s)
        c2 = wf.get(s)
        if c1 is not None and c2 is not None:
            l1 = math.log10(c1) - lg_tj
            l2 = math.log10(c2) - lg_tw
            return logaddexp10(l1, l2) - math.log10(2), 'both'
        if c1 is not None:
            return math.log10(c1) - lg_tj - math.log10(2), 'jieba'
        if c2 is not None:
            return math.log10(c2) - lg_tw - math.log10(2), 'wf'
        return None, 'miss'
    return score, len(jieba), len(wf)

def main():
    today = datetime.date.today().isoformat()
    t2s, np_, nc_ = load_t2s()
    score_of, nj_, nw_ = make_scorer()
    VARIANTS = load_variants()
    print(f't2s 詞級 {np_} 條，字級 {nc_} 條；jieba {nj_} 詞；wordfreq(字幕+推特) {nw_} 詞；異體變體 {len(VARIANTS)} 字；混合比 1:1')

    # 自舉：輸入＝現役詞典自身（單字區字碼原樣繼承；詞組區按詞頻重排）
    m_hdr, m_ent = load_dict_yaml(os.path.join(RIME, 'xuma_L.dict.yaml'))
    # 剝除舊生成註釋（頭部 ★★★ 塊；數據區界碑塊與併入區裝飾行），冪等防疊加
    _hdr, _skip = [], False
    for line in m_hdr:
        if line.startswith('# ★★★'):
            _skip = True
        if _skip:
            if 'Unihan_Variants）。' in line:
                _skip = False
            continue
        _hdr.append(line)
    m_hdr = _hdr
    # 界碑範圍式剝除：遇 '# ═' 行進入剝離態，至下一 '# ═' 行（含）結束；併入區裝飾行同剝
    _ent, _skip2 = [], False
    for e in m_ent:
        if e[4].startswith('# ═'):
            _skip2 = not _skip2
            continue
        if _skip2:
            continue
        if e[4].lstrip('# ').startswith('===== 併入區'):
            continue
        _ent.append(e)
    m_ent = _ent
    c_ent, e_ent = [], []   # legacy 三表已全量融入現表，不再讀取（報告仍作審計基準）

    SRC = {'m': 0, 'c': 1, 'e': 2, 'a': 3}
    def classify(text, code):
        """'keep'=不動 'sort'=參與重排 'append'=併入尾區"""
        if not text:
            return 'keep'   # 註釋/空行/頭尾界碑：原位原樣輸出
        if not CODE_RE.match(code) or len(code) < 4:
            return 'keep'
        if len(text) == 1:
            return 'keep'
        if not all(is_hanzi(c) for c in text):
            return 'keep'
        return 'sort'

    # ---- 手收詞標記解析（# [手收·勿刪·分組] 詞形 → 綁定下一行詞條）----
    import re as _re2
    MARK_RE = _re2.compile(r'^# \[手收·勿刪(?:·[^\]]*)?\] (.+)$')
    mark_of = {}          # text -> 標記註釋行原文（同詞多標記取首見）
    for e in m_ent:
        ln, text, code, w, raw = e
        m = MARK_RE.match(raw)
        if m:
            mark_of[m.group(1)] = raw
    # ---- 主表：keep 原位；sort 按碼位分組 ----
    m_sort = defaultdict(list)   # code -> [entry]
    m_keep = {}                  # line_no -> entry（原位輸出）
    first_sort_line = {}         # code -> 主表該碼位第一個 sort 條目行號（輸出錨點）
    m_seen = set()
    for e in m_ent:
        ln, text, code, w, raw = e
        if classify(text, code) == 'sort':
            m_sort[code].append(e)
            if code not in first_sort_line:
                first_sort_line[code] = ln
            m_seen.add((text, code))
        else:
            m_keep[ln] = e

    # ---- ciz/ext：併入 ----
    merge = defaultdict(list)    # code -> [entry]（ciz+ext 的 sort 條目）
    append_raw = []              # 尾區原樣條目
    for src, ents in (('c', c_ent), ('e', e_ent)):
        for idx, e in enumerate(ents):
            ln, text, code, w, raw = e
            if classify(text, code) == 'sort' and (text, code) not in m_seen:
                merge[code].append((src, idx, e))
            elif classify(text, code) == 'sort':
                pass  # 與主表完全重複（(text,code) 相同）：以主表版為準，丟棄
            else:
                append_raw.append((src, e))

    # ---- 計算排序鍵（雙語源 1:1 混合）----
    stat = {'both': 0, 'jieba': 0, 'wf': 0, 'variant': 0, 'miss': 0, 'manual': 0, 'nocorpus': 0,
            'per_src': defaultdict(lambda: defaultdict(lambda: 0))}
    OVERRIDES = {}   # 詞 -> 覆蓋頻分（manual_overrides.txt 交換法）
    def freq_of(text):
        if text in OVERRIDES:
            return OVERRIDES[text], 'manual', t2s(text)
        s = t2s(text)
        sc, tag = score_of(s)
        if sc is None:
            sc, tag = score_of(text)
        if sc is None:
            # 驗證式異體歸一：對 miss 詞逐字展開 kSemanticVariant 候選組合，命中即用
            cands = []
            for ch in s:
                vs = VARIANTS.get(ch)
                cands.append([ch] + vs if vs else [ch])
            n = 1
            for c in cands: n *= len(c)
            if 1 < n <= 64:
                def dfs(i, acc):
                    if i == len(cands):
                        w = ''.join(acc)
                        if w != s:
                            v, t = score_of(w)
                            if v is not None:
                                return w, v
                        return None
                    for c in cands[i]:
                        r = dfs(i + 1, acc + [c])
                        if r: return r
                    return None
                r = dfs(0, [])
                if r:
                    return r[1], 'variant', s
        return sc, tag, s

    def key_of(src, e):
        ln, text, code, w, raw = e
        f, tag, simp = freq_of(text)
        oldw = int(w) if w and w.isdigit() else 0
        stat[tag] += 1
        stat['per_src'][src][tag] += 1
        if f is not None:
            return (0, -f, -oldw, SRC[src], ln), f, simp
        else:
            return (1, 0, -oldw, SRC[src], ln), None, simp

    # ---- 人工次序覆蓋（manual_overrides.txt：A > B，交換頻分，僅同碼組內生效）----
    ov_path = os.path.join(FREQ, 'manual_overrides.txt')
    n_ov = 0
    if os.path.exists(ov_path):
        word2codes = defaultdict(set)
        for _c, _es in m_sort.items():
            for _e in _es: word2codes[_e[1]].add(_c)
        for _c, _ts in merge.items():
            for _s, _i, _e in _ts: word2codes[_e[1]].add(_c)
        for line in open(ov_path, encoding='utf-8'):
            line = line.split('#')[0].strip()
            if not line: continue
            mm = re.match(r'^(\S+)\s*>\s*(\S+)$', line)
            if not mm:
                print(f'[overrides] 格式忽略: {line}'); continue
            a, b = mm.group(1), mm.group(2)
            if not (word2codes.get(a) and word2codes.get(b) and (word2codes[a] & word2codes[b])):
                print(f'[overrides] 非同碼或未入庫，跳過: {a} > {b}'); continue
            fa, fb = freq_of(a)[0], freq_of(b)[0]
            if fa is None or fb is None:
                print(f'[overrides] 頻分缺失，跳過: {a}({fa}) > {b}({fb})'); continue
            if fa >= fb:
                print(f'[overrides] 已滿足次序: {a} > {b}'); continue
            OVERRIDES[a] = fb + 1e-9   # ε 防同分
            OVERRIDES[b] = fa
            n_ov += 1
            print(f'[overrides] 生效: {a}({fa:.2f} ↔ {fb:.2f}){b}')
    print(f'人工次序覆蓋 {n_ov} 對')

    # ---- 補詞階段：上游差集 + jieba 直筛 → s2t → 繁體形新增詞 ----
    s2t_make, s2t_phrase, _sc = load_s2t()
    HAS_S2T = s2t_make is not None
    if not HAS_S2T:
        print('⚠ 無語料模式：OpenCC 簡→繁表缺失，A/B 源補詞整體禁用（H 源手收詞不受影響）')
    existing = {e[1] for e in m_ent} | {e[1] for e in c_ent} | {e[1] for e in e_ent}
    # 單字全碼表（主表單字區）
    charmap = {}
    for e in m_ent:
        if len(e[1]) == 1 and e[2].isalpha():
            cur = charmap.get(e[1])
            if cur is None or len(e[2]) > len(cur): charmap[e[1]] = e[2]
    def full(ch): return charmap.get(ch)
    def wordcode(trad):
        fcs = [full(ch) for ch in trad]
        if any(f is None for f in fcs): return None
        n = len(trad)
        if n == 2:
            if len(fcs[0]) < 2 or len(fcs[1]) < 2: return None
            return fcs[0][:2] + fcs[1][:2]
        if n == 3:
            if len(fcs[2]) < 2: return None
            return fcs[0][0] + fcs[1][0] + fcs[2][:2]
        if 4 <= n <= 10:
            # AaBaCaZa：第1、2、3、末字首碼，恆4碼（官方 encoder/rules 認證）
            return fcs[0][0] + fcs[1][0] + fcs[2][0] + fcs[-1][0]
        return None   # 超出造詞規則範圍（>10 字）
    # 詞源 A：上游主表差集（當年 cizusimp 內容，現併於上游主表）
    # 簡體專用字集（簡→繁表鍵）：A 源詞必須含至少一個；參照命中過濾簡形也用它
    simp_keys = set()
    if HAS_S2T:
        for line in open(os.path.join(CORPUS, 'STCharacters.txt'), encoding='utf-8'):
            if line.startswith('#') or '\t' not in line: continue
            simp_keys.add(line.split('\t', 1)[0])
    def loadset(path):
        s = set(); in_data = False
        for line in open(path, encoding='utf-8'):
            line = line.rstrip('\n')
            if not in_data:
                if line == '...': in_data = True
                continue
            if not line.strip() or line.lstrip().startswith('#'): continue
            c = line.split('\t')
            if len(c) >= 2: s.add(c[0])
        return s
    up_path = os.path.join(CORPUS, 'upstream_xuma.dict.yaml')
    srcA = []
    if HAS_S2T and os.path.exists(up_path):
        for t in loadset(up_path) - existing:
            if len(t) < 2 or len(t) > 4: continue
            if not all(is_hanzi(c) for c in t): continue
            if not any(c in simp_keys for c in t): continue
            srcA.append(t)
    # 詞源 B：jieba+wordfreq 詞表直筛（混合分 ≥ 1e-7）
    # 詞源 B：詞形只認 jieba 規範簡體表（wordfreq 僅供頻次，其詞形混有日式/台形異形詞）
    candB = set(load_jieba().keys())
    srcB = []
    for w in candB:
        if 2 <= len(w) <= 4 and all(is_hanzi(c) for c in w) and w not in existing:
            if t2s(w) != w: continue          # 擋混入的繁體詞（s2t 對繁體恆等，會穿透管道）
            sc, tag = score_of(w)
            if sc is not None and sc >= -7.0:
                srcB.append(w)
    addon = {}          # trad -> (code, disp, srctag)
    amb_list, noc_list = [], []
    # 用戶庫字頻（僅用於 A 源低頻字檢測與 s2t 定形，不做任何替換）
    # 自舉模式下固定以 legacy 三表為基準：防止補詞過濾結果隨現表漂移（詞表凍結）
    from collections import Counter as _C
    user_cf = _C()
    for _fn in ('xuma.dict.yaml', 'xuma.cizutrad.dict.yaml', 'xuma.extended.dict.yaml'):
        for e in load_dict_yaml(os.path.join(RIME, 'legacy', _fn))[1]:
            for ch in e[1]:
                if is_hanzi(ch): user_cf[ch] += 1
    ref_texts = existing | set((s2t_phrase or {}).values())
    if os.path.exists(up_path):
        ref_texts |= loadset(up_path)
    s2t = s2t_make(ref_texts, user_cf, simp_keys) if HAS_S2T else None
    VRAW = load_variants_raw()
    # ==== 用戶審查規則（審查結果.md 2026-09-07）====
    DROP_CHARS = set('个箇個裏里周週')          # 第4/5/7條：整系不加
    SPECIAL_FORM = {'氣': '气'}                 # 第2條：用本字「气」，含氣詞注釋存檔
    keep3_path = os.path.join(ARCHIVE, 'keep3.txt')
    keep3 = set(open(keep3_path, encoding='utf-8').read().split()) if os.path.exists(keep3_path) else set()
    done_path = os.path.join(ARCHIVE, 'reviewed_done.txt')
    reviewed_done = set(open(done_path, encoding='utf-8').read().split()) if os.path.exists(done_path) else set()
    def is_variant_amb(ch):
        """歧義字的候選形在 Unihan 異體圖中連通 → 真異體（待人裁）；否則爲分化字（按規範自動）"""
        vs = _sc.get(ch)
        if not vs or len(vs) < 2: return False
        return any(vs[j] in VRAW.get(vs[i], []) or vs[i] in VRAW.get(vs[j], [])
                   for i in range(len(vs)) for j in range(i + 1, len(vs)))
    qi_notes = []
    seen_simp = set()
    for srctag, pool in (('A', srcA), ('B', srcB)):
        for simp in pool:
            if simp in seen_simp or simp in existing: continue
            if t2s(simp) != simp: continue
            trad, amb_chars = s2t(simp)
            if '氣' in trad:
                trad0 = trad
                trad = ''.join(SPECIAL_FORM.get(c, c) for c in trad)
                qi_notes.append((simp, trad0, trad))
            if trad in existing or trad in addon: seen_simp.add(simp); continue   # 第1條：已在庫不加
            if any(c in DROP_CHARS for c in trad): seen_simp.add(simp); continue  # 第4/5/7條
            if len(trad) == 3 and trad not in keep3: seen_simp.add(simp); continue # 第6條
            real_amb = [ch for ch in amb_chars if is_variant_amb(ch)]
            if real_amb:                                                            # 第9條：僅真異體待人裁
                if simp in reviewed_done: seen_simp.add(simp); continue             # 已審定案，不再重複列入
                sc0, tag0 = score_of(simp)
                f0 = round(10 ** sc0 * 1e9) if sc0 is not None else 0
                if f0 >= 1000:   # 低頻異體詞不擾人：分值 <1000/億的直接丟棄
                    amb_list.append((srctag, simp, trad, ''.join(real_amb), f0))
                seen_simp.add(simp); continue
            if srctag == 'A':                        # A源低頻異形直接丟（第9條：錯字/異形不勞人裁）
                lowfreq = [ch for ch in trad if user_cf.get(ch, 0) < 3]
                if any(ch in VRAW for ch in lowfreq): seen_simp.add(simp); continue
            code = wordcode(trad)
            if code is None:
                noc_list.append((srctag, simp, trad)); seen_simp.add(simp); continue
            sc, tag, _ = freq_of(trad)
            disp = str(round(10 ** sc * 1e9)) if sc is not None else None
            addon[trad] = (code, disp, srctag)
            seen_simp.add(simp)
    # H 源：手收詞清單補錄（handpicked.txt 新增詞自動入庫，編碼按官方規則推導）
    #   正常詞=保護；-詞=顯式棄用（授權從詞庫刪除，審計入 log/hp_removals.log）
    handpicked, hp_drop = [], set()
    if os.path.exists(hp_path2 := os.path.join(FREQ, 'handpicked.txt')):
        _g = None
        for _l in open(hp_path2, encoding='utf-8'):
            _s = _l.strip()
            if _s.startswith('## '): _g = _s[3:]; continue
            if not _s or _s.startswith('#'): continue
            if _s.startswith('-') and len(_s) > 1:
                hp_drop.add(_s[1:])
            else:
                handpicked.append((_s, _g or '手收'))
    h_add, h_fail = 0, []
    for w, g in handpicked:
        if w in existing or w in addon: continue
        code = wordcode(w)
        if code is None:
            h_fail.append(w); continue
        sc, _tag, _ = freq_of(w)
        disp = str(round(10 ** sc * 1e9)) if sc is not None else None
        addon[w] = (code, disp, 'H')
        mark_of[w] = f'# [手收·勿刪·{g}] {w}'
        h_add += 1
    if h_add: print(f'[H源] 手收詞補錄 {h_add} 詞')
    if h_fail: print(f'[H源] ⚠ 無法推碼（缺字或超長）：{h_fail}')
    # 標記自愈：清單詞在庫但標記缺失（如歷史事故）→ 從清單重建
    _inlib = set(e[1] for e in m_ent if e[1])
    _heal = [(w, g) for w, g in handpicked if w in _inlib and w not in mark_of]
    for w, g in _heal:
        mark_of[w] = f'# [手收·勿刪·{g}] {w}'
    if _heal: print(f'[H源] 標記自愈 {len(_heal)} 詞')

    # keep3 補錄（第6條：已入庫三字詞固化，含 wordfreq 獨有來源者）
    for trad in sorted(keep3):
        if trad in existing or trad in addon: continue
        if any(c in DROP_CHARS for c in trad): continue
        code = wordcode(trad)
        if code is None: continue
        sc, tag, _ = freq_of(trad)
        disp = str(round(10 ** sc * 1e9)) if sc is not None else None
        addon[trad] = (code, disp, 'K')
    # 已審放行清單（用戶批注定案；含異體並收形）
    reviewed_path = os.path.join(ARCHIVE, 'reviewed.txt')
    if os.path.exists(reviewed_path):
        for trad in set(open(reviewed_path, encoding='utf-8').read().split()):
            if trad in existing or trad in addon: continue
            code = wordcode(trad)
            if code is None:
                noc_list.append(('R', trad, trad)); continue
            sc, tag, _ = freq_of(trad)
            disp = str(round(10 ** sc * 1e9)) if sc is not None else None
            addon[trad] = (code, disp, 'R')
    # addon 併入重排結構（src 'a'）
    addon_sorted = sorted(addon.items(), key=lambda kv: (-(int(kv[1][1]) if kv[1][1] and kv[1][1].isdigit() else -1), kv[0]))
    for idx, (trad, (code, disp, srctag)) in enumerate(addon_sorted):
        raw = f'{trad}\t{code}\t{disp}' if disp else f'{trad}\t{code}'
        merge[code].append(('a', idx, (idx, trad, code, disp, raw)))
    # 審計文件
    with open(os.path.join(RIME, 'xuma.addon.dict.yaml'), 'w', encoding='utf-8') as f:
        f.write('# ★ 補詞審計表（僅供人審，未接入任何 schema）\n')
        f.write(f'# 生成 {today}：A源 {len(srcA)} + B源 {len(srcB)} → 新增 {len(addon)}；歧義攔下 {len(amb_list)}；無碼攔下 {len(noc_list)}\n')
        f.write('---\nname: "xuma.addon"\nversion: "addon.%s"\nsort: original\n...\n' % today)
        for trad, (code, disp, srctag) in addon_sorted:
            f.write(f'{trad}\t{code}\t{disp if disp else ""}\t{srctag}\n')
    print(f'補詞階段：A源 {len(srcA)}，B源 {len(srcB)}，新增 {len(addon)}，歧義 {len(amb_list)}，無碼 {len(noc_list)}')
    with open(os.path.join(ARCHIVE, 'addon_review.txt'), 'w', encoding='utf-8') as f:
        for tag, s, t, note, f0 in amb_list: f.write(f'歧義[{tag}] {s} → {t}  字:{note} 分:{f0}\n')
        for tag, s, t in noc_list: f.write(f'無碼[{tag}] {s} → {t}\n')
        for s, t0, t1 in qi_notes: f.write(f'氣形注釋 {s} → {t0} →（採用）{t1}\n')
    # ---- 待審詞表（供人批注）----
    from collections import defaultdict as _DD
    grp = _DD(list)
    for tag, simp, trad, note, f0 in amb_list:
        key = (0 if tag == 'A審' else 1, note)
        grp[key].append((simp, trad, f0, tag))
    rp = os.path.join(ARCHIVE, '待審詞表.txt')
    MIN_GRP = 5
    big = sorted([(k, v) for k, v in grp.items() if len(v) >= MIN_GRP],
                 key=lambda kv: (kv[0][0], -sum(x[2] for x in kv[1])))
    misc = [(k, v) for k, v in grp.items() if len(v) < MIN_GRP]
    with open(rp, 'w', encoding='utf-8') as f:
        f.write('# 待審詞表（自動生成 %s，勿刪組標題行）\n' % today)
        f.write('# 批注方法：行首「?」改為「+」＝放行入庫；改為「-」＝確認丟棄；不改＝維持丟棄\n')
        f.write('# 整組批量：在「## 組」標題行尾加 [+] 或 [-]，覆蓋全組未逐詞批注的行\n')
        f.write('# 行內「#」後可寫備註，解析時忽略\n\n')
        for (part, note), items in big:
            items = sorted(items, key=lambda x: -x[2])
            label = ('A源低頻字：' if part == 0 else 's2t歧義字：') + note
            f.write('## 組：%s（%d 詞，組內詞頻合計 %d）\n' % (label, len(items), sum(x[2] for x in items)))
            for simp, trad, f0, tag in items:
                f.write('? %s → %s\t分:%d\t[%s]\n' % (simp, trad, f0, tag))
            f.write('\n')
        for part in (0, 1):
            mitems = [x for k, v in misc if k[0] == part for x in v]
            if not mitems: continue
            mitems.sort(key=lambda x: -x[2])
            label = 'A源低頻字雜項' if part == 0 else 's2t歧義字雜項'
            f.write('## 組：%s（%d 詞，按詞頻降序；小組已合併）\n' % (label, len(mitems)))
            for simp, trad, f0, tag in mitems:
                f.write('? %s → %s\t分:%d\t[%s]\n' % (simp, trad, f0, tag))
            f.write('\n')
    nbig = len(big); nmisc = sum(len(v) for k, v in misc)
    print(f'待審詞表已寫出 {rp}（大組 {nbig}，雜項 {nmisc} 詞，共 {len(amb_list)} 詞）')

    # ---- 逐碼位重排，生成新行 ----
    def render(e, f):
        ln, text, code, w, raw = e
        # 查到混合分數 → weight 列寫 10^score*1e9（每十億詞次出現次數，取整）
        if f is not None:
            disp = str(round(10 ** f * 1e9))
            if w is not None:
                cols = raw.split('\t')
                cols[2] = disp
                return '\t'.join(cols)
            return f'{text}\t{code}\t{disp}'
        return raw

    new_lines = []          # (anchor_line_no, content) 主體；anchor 用主表行號保持骨架
    tail_lines = []         # 尾區
    report_samples = []

    for e in m_ent:  # 按主表原行序走骨架
        ln, text, code, w, raw = e
        if text and text in hp_drop:
            continue   # 顯式棄用：詞條與其標記一併不出
        if MARK_RE.match(raw) and (m_ent[[x[0] for x in m_ent].index(ln) + 1][1] if ln + 1 in [x[0] for x in m_ent] else '') in hp_drop:
            continue
        if ln in m_keep:
            if MARK_RE.match(raw):   # 標記行：sort 詞條的綁定標記已隨組輸出，原位跳過
                nxt = m_keep.get(ln + 1) or next((x for c in m_sort.values() for x in c if x[0] == ln + 1), None)
                continue
            mk = mark_of.get(text)
            if mk:
                new_lines.append((ln, mk))
                del mark_of[text]   # 已輸出，防 sort 組重複
            new_lines.append((ln, raw))
        elif code in first_sort_line and first_sort_line[code] == ln:
            group = [(key_of('m', x), x, 'm') for x in m_sort[code]]
            group += [(key_of(s, x), x, s) for s, idx, x in merge[code]]
            group.sort(key=lambda t: t[0][0])
            old_order = [x[1] for x in m_sort[code]]
            new_order = [g[1][1] for g in group]
            for keytuple, x, s in group:
                f = keytuple[1]
                line = render(x, f if f is not None else None)
                mk = mark_of.get(x[1])
                if mk:
                    new_lines.append((ln, mk))
                new_lines.append((ln, line))
            if new_order != old_order or merge[code]:
                report_samples.append((code, old_order, [g[1][1] for g in group]))
        # else: 主表同碼位後續詞組行 → 已在上面輸出，跳過

    # 尾區：ciz/ext 的 sort 條目中，主表無該碼位 sort 錨點的 + append_raw
    tail_codes = sorted(c for c in merge if c not in first_sort_line)
    tail_lines.append('# ===== 併入區：cizutrad/extended 條目（主表無同碼詞組，按碼序）=====')
    for c in tail_codes:
        group = [(key_of(s, x), x, s) for s, idx, x in merge[c]]
        group.sort(key=lambda t: t[0][0])
        for keytuple, x, s in group:
            f = keytuple[1]
            mk = mark_of.get(x[1])
            if mk:
                tail_lines.append(mk)
            tail_lines.append(render(x, f))
    tail_lines.append('# ===== 併入區：原樣保留（空文本/異常格式/非四碼/含符號）=====')
    for s, e in append_raw:
        tail_lines.append(e[4])

    # ---- 寫出新詞典 ----
    out_hdr = []
    for line in m_hdr:
        if line.startswith('name:'):
            out_hdr.append('name: "xuma_L"')
        elif line.startswith('version:'):
            out_hdr.append(f'version: "freq.{today}"')
        elif line.startswith('import_tables:'):
            out_hdr.append('# import_tables 已移除：cizutrad/extended 全量併入本表（詞頻重排版）')
        elif line.startswith('  - xuma.cizutrad') or line.startswith('  - xuma.extended') or line.startswith('  #- xuma.cizusimp'):
            continue
        else:
            out_hdr.append(line)
    header_block = f'''# ★★★ 徐碼·L 詞典（詞頻重排＋補詞版）生成於 {today}，工具：lua/codelllc09/tools/gen_freq_dict.py（可重跑複現）
#
# ── 條目構成（自舉式：以本表現狀為基準增量演進；字碼屬靜態資產，僅經手動編輯本表修改）──
#   淵源（2026-09-07 首建自下列三表，現存檔於 legacy/ 僅作審計基準）：
#   1. xuma.dict.yaml 主表：徐國銀先生《徐碼全集碼表（+詞）》，Q 整理
#   2. xuma.cizutrad.dict.yaml 傳承字詞表（權重原取自 rime-zhengma）
#   3. xuma.extended.dict.yaml 使用者手收詞（異體系/新詞）
#   4. 新增詞 46,767 條，詞形來源：
#      a. jieba dict.txt（github.com/fxsjy/jieba，人民日報等語料，349,045 詞）
#      b. 上游倉庫 github.com/Ace-Who/rime-xuma 主表差集（即當年拆分的 cizusimp
#         簡體詞表內容；僅取詞形，其編碼一律不用，由本表繁體字形全碼重新推導）
#      簡→繁轉換：OpenCC（github.com/BYVoid/OpenCC）STPhrases 詞組級＋STCharacters 字級
#   字碼修正（衡 sqkx/sqk 等）已固化本表；char_code_fixes 流水線機制已廢除，
#   流水線重跑對單字區與簡碼區逐字節原樣繼承。
#
# ── 詞頻來源與算法 ──
#   源A jieba dict.txt（新聞書面）；源B wordfreq zh 38,811 詞（OpenSubtitles 字幕＋Twitter 聚合）
#   兩源各自歸一化為頻率後 log 域 1:1 混合：score = log10(0.5·pA + 0.5·pB)；
#   單源命中＝log10(0.5·p)；繁體詞經 OpenCC TS 表轉簡後查表；
#   查不到者走 Unihan kSemanticVariant 異體歸一（驗證式：逐字展開變體組合，
#   命中簡體詞頻即採用，如「彊烈」享「強烈」頻次）；兩源皆無者按舊權重回退沉底。
#   weight 列＝round(10^score × 1e9)，即每十億詞次出現次數（僅元數據）。
#
# ── 排序規則（sort: original，順序由行序承載）──
#   同碼位內：單字（原序不動）→ 詞組（混合分降序 → 舊權重 → 原序）；
#   單字區與一簡/二簡/三簡碼區逐字節保持現表原樣（自舉繼承）。
#
# ── 新增詞編碼規則（自本表單字全碼推導，經官方詞條交叉驗證）──
#   二字詞＝各字全碼前2；三字詞＝首1＋次1＋末前2；四字詞＝各字首碼；一簡碼不參與組詞
#
# ── 感謝與上游貢獻 ──
#   徐碼發明人徐國銀先生；rime-xuma（Ace-Who/rime-xuma）及其簡體詞表；
#   jieba（fxsjy/jieba）；wordfreq（OpenSubtitles/Twitter 數據）；
#   OpenCC（BYVoid/OpenCC）；Unicode Unihan Database（Unihan_Variants）。'''
    out_hdr.insert(1, header_block)

    # ── 手收詞保護界碑與護欄（用戶定案 2026-09-08）──
    GUARD = (
        '# ═══════════════════════════════════════════════════════════════',
        '# ⚠⚠⚠ 警告（AI/自動化操作必讀）⚠⚠⚠',
        '# 本詞典存在「手收詞保護區」，權威清單：lua/codelllc09/tools/dict_pipeline/handpicked.txt',
        '# 凡涉及詞條刪除/清理/過濾/重排的操作，必須先核對該清單：',
        '#   1. 清單內詞條一律視爲用戶手動收詞，禁止刪除、禁止移動、禁止改動編碼；',
        '#   2. 任何會導致清單詞條丟失的變更，必須先向用戶說明並取得明確授權；',
        '#   3. 無法確定時，停止操作並詢問——寧可中止，不可誤刪。',
        '# ═══════════════════════════════════════════════════════════════',
    )
    hp_set = set(w for w, g in handpicked)
    # 三方校驗：① 清單詞必須存活於輸出；② 詞庫帶標記詞必須在清單（保護或棄用）——防清單誤編輯
    out_texts = set(e[1] for e in m_ent if e[1]) | set(addon.keys()) | \
                set(x[2][1] for lst in merge.values() for x in lst)
    lost = hp_set - out_texts
    if lost:
        print(f'⛔ 手收詞護欄①觸發：{len(lost)} 個保護詞將丟失（如 {sorted(lost)[:5]}），已中止寫盤。'
              f' 若屬用戶明確授權的變更，請在 handpicked.txt 中以 -詞 標記棄用。')
        sys.exit(1)
    orphan = set(mark_of.keys()) - hp_set - hp_drop
    if orphan:
        print(f'⛔ 手收詞護欄②觸發：詞庫 {len(orphan)} 個帶標記詞在清單中消失（如 {sorted(orphan)[:5]}）——'
              f' 清單可能被誤編輯，請人工覈對 handpicked.txt（刪詞請顯式寫 -詞）。')
        sys.exit(1)
    # 刪除審計：棄用詞若確在本次輸入詞庫中，記入 log（追加式事件流水）
    dropped_now = [w for w in hp_drop if w in set(e[1] for e in m_ent)]
    if dropped_now:
        log_dir = os.path.join(FREQ, 'log')
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'hp_removals.log'), 'a', encoding='utf-8') as f:
            f.write(f'[{today}] 棄用 {len(dropped_now)} 詞：' + ' '.join(sorted(dropped_now)) + '\n')
        print(f'[H源] 棄用刪除 {len(dropped_now)} 詞（審計：dict_pipeline/log/hp_removals.log）')

    # 無語料模式禁寫盤：詞頻全休眠時重排結果無意義且有破壞性（全部沉底），僅作護欄診斷
    if nj_ == 0 and nw_ == 0:
        print('⚠ 無語料模式：護欄校驗通過，但詞頻功能休眠，已跳過寫盤（詞庫未動）。'
              ' 補齊語料後重跑方可生效，恢復途徑見 dict_pipeline/corpus/README.md')
        sys.exit(0)

    out_path = os.path.join(RIME, 'xuma_L.dict.yaml')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out_hdr) + '\n')
        f.write('\n'.join(GUARD) + '\n')
        for ln, content in new_lines:
            f.write(content + '\n')
        f.write('\n'.join(tail_lines) + '\n')
    print(f'寫出 {out_path}: 主體 {len(new_lines)} 行 + 尾區 {len(tail_lines)} 行')

    # ---- 寫出 dry-run 報告 ----
    rep = []
    rep.append(f'# 詞頻重排 dry-run 報告 {today}（雙語源 v2）\n')
    rep.append('## 一、語源與覆蓋\n')
    rep.append('- 語源 A：jieba dict.txt（人民日報等，349,045 詞）')
    rep.append('- 語源 B：wordfreq zh（OpenSubtitles 字幕+Twitter 聚合，38,811 詞）')
    rep.append('- 語源 C：Unihan kSemanticVariant 異體歸一（驗證式：miss 詞逐字展開變體組合，命中簡體詞頻即用）')
    rep.append('- 混合：各自歸一化為頻率後 log 域 1:1 加權（score = log10(0.5·pA + 0.5·pB)）')
    rep.append('- 單語源命中者：score = log10(0.5·p)；兩源皆無：回退舊權重沉底\n')
    total_sort = stat['both'] + stat['jieba'] + stat['wf'] + stat['variant'] + stat['miss']
    rep.append(f'- 參與重排詞組：{total_sort} 條（主表 {sum(len(v) for v in m_sort.values())}'
               f' + 併入 {sum(len(v) for v in merge.values())}）')
    hit_any = stat['both'] + stat['jieba'] + stat['wf'] + stat['variant']
    if total_sort:
        rep.append(f'- 任一語源查到：{hit_any}（{hit_any/total_sort*100:.1f}%）；皆無：{stat["miss"]}（{stat["miss"]/total_sort*100:.1f}%）')
    rep.append(f'  - 雙源命中 {stat["both"]}｜僅 jieba {stat["jieba"]}｜僅 wordfreq {stat["wf"]}｜異體歸一命中 {stat["variant"]}')
    for s, name in (('m', '主表'), ('c', 'cizutrad'), ('e', 'extended'), ('a', '補詞(新增)')):
        d = stat['per_src'][s]
        t = d['both'] + d['jieba'] + d['wf'] + d['variant'] + d['miss']
        if t:
            rep.append(f'  - {name}: 任一源命中 {t-d["miss"]}/{t}（{(t-d["miss"])/t*100:.1f}%）')
    rep.append(f'- 尾區原樣保留：{len(append_raw)} 條（空文本 113 / 異常格式 / 非四碼詞等）\n')

    rep.append('## 二、完整性與不變式校驗\n')
    def loadset(path):
        s = set(); in_data = False
        for line in open(path, encoding='utf-8'):
            line = line.rstrip('\n')
            if not in_data:
                if line == '...': in_data = True
                continue
            if not line.strip() or line.lstrip().startswith('#'): continue
            cols = line.split('\t')
            if len(cols) < 2: continue
            s.add((cols[0], cols[1]))
        return s
    oldall = loadset(os.path.join(RIME, 'legacy', 'xuma.dict.yaml'))
    for fn in ('xuma.cizutrad.dict.yaml', 'xuma.extended.dict.yaml'):
        oldall |= loadset(os.path.join(RIME, 'legacy', fn))
    newset = loadset(out_path)
    rep.append(f'- 條目守恆：舊體系三表並集 {len(oldall)} 條 vs 新表 {len(newset)} 條；'
               f'丟失 {len(oldall-newset)}，新增 {len(newset-oldall)}')
    import re as _re
    def seqof(path, pred):
        r = []; in_data = False
        for line in open(path, encoding='utf-8'):
            line = line.rstrip('\n')
            if not in_data:
                if line == '...': in_data = True
                continue
            if not line.strip() or line.lstrip().startswith('#'): continue
            cols = line.split('\t')
            if len(cols) >= 2 and pred(cols[0], cols[1]):
                r.append(cols[0])
        return r
    mpred = lambda t, c: len(t) == 1
    spred = lambda t, c: len(t) > 1 and _re.fullmatch(r'[a-z]{1,3}', c)
    mc_ = seqof(os.path.join(RIME, 'legacy', 'xuma.dict.yaml'), mpred)
    nc_ = seqof(out_path, mpred)
    sw_ = seqof(os.path.join(RIME, 'legacy', 'xuma.dict.yaml'), spred)
    nw_ = seqof(out_path, spred)
    rep.append(f'- 單字 {len(mc_)} 條相對序不變：{mc_ == nc_}')
    rep.append(f'- 簡碼詞組（≤3碼）{len(sw_)} 條相對序不變：{sw_ == nw_}')
    rep.append(f'- fbic 新序：{[x[0] for x in seqof(out_path, lambda t,c: c=="fbic")]}\n')

    rep.append('## 三、變化最大的碼位（舊詞組序 → 新詞組序，前 40）\n')
    rep.append('```')
    changed = [(c, o, n) for c, o, n in report_samples if o != n]
    changed.sort(key=lambda t: len(t[1]) + len(t[2]), reverse=True)
    for c, o, n in changed[:40]:
        rep.append(f'{c}: {" > ".join(o)}  ==>  {" > ".join(n)}')
    if any(c == 'fbic' for c, _, _ in report_samples):
        for c, o, n in report_samples:
            if c == 'fbic':
                rep.append(f'[用戶指定] fbic: {" > ".join(o)}  ==>  {" > ".join(n)}')
    rep.append('```\n')
    rep.append('## 四、已知事項\n')
    rep.append('- 主表原有格式異常條目（抵觸/對於 tab丟失、yv 碼尾空格、extended 尾空格碼）原樣保留，未修復')
    rep.append('- cizutrad 與主表 (text,code) 完全重複的 61 條以主表版為準')
    rep.append('- 新表 weight 列：命中者為混合頻率×10^9（每十億詞次出現次數）；未命中者保留舊值（僅元數據，original 排序下不影響候選序）')
    rep.append('- wordfreq 的 zh 詞表是 jieba 詞表的子集（同分詞器統計），故「僅 wordfreq 命中」者極少；兩源頻次統計相互獨立')
    rep.append('- 對照詞數據：會議（人民日報 28363 / 字幕推特 360120）vs 合適（2426 / 45901）——口語語源同向，會議仍領先')
    with open(os.path.join(ARCHIVE, 'freq_report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(rep) + '\n')
    print(f'報告已寫出 {os.path.join(ARCHIVE, "freq_report.md")}')
    # 詳細樣例直接打印到 stdout 供審
    print('\n===== 樣例碼位新舊對照 =====')
    for c, o, n in changed[:10]:
        print(f'{c}: {" > ".join(o)}  ==>  {" > ".join(n)}')
    for c, o, n in report_samples:
        if c == 'fbic':
            print(f'[用戶指定] fbic: {" > ".join(o)}  ==>  {" > ".join(n)}')

if __name__ == '__main__':
    main()
