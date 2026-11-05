#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wishd.py — 詞庫意願台（2026-11-05）：意願寫入的唯一後端

為什麼要有它：詞庫的三個「意願通道」此前只有「手動編輯文字檔」一種寫法——
  handpicked.txt        手收詞 / `-詞` 棄用
  manual_overrides.txt  次序裁定（A > B / A > B > C）
  archive/rulings/rejected.txt  否決（防補詞階段復活）
手改的問題不是麻煩，是**沒有校驗**：同碼與否、詞在不在庫、重複、組內其餘詞怎麼辦，
全靠人記。本模塊把校驗收成一份，CLI、Web UI、IME 側共用，避免分叉繞過護欄。

鐵律（與 AGENTS 一致）：
  · 永不直接改 xuma_L.dict.yaml（生成物，下次管道覆蓋）
  · 只寫上述三個通道；寫入前全部校驗；寫入用原子替換
  · 每次意願變動記台賬

用法（CLI）：
    wishd.py list  <碼|詞>          看該同碼組全部成員（頻分、來源、是否手收）
    wishd.py search <子串>          詞庫搜尋
    wishd.py suspicious             可疑組：同分並列、異體雙形
    wishd.py overrides              現有次序裁定 + 生效狀態推定
    wishd.py add    "詞1 詞2"       加入暫存（與 /add 共用 spool）
    wishd.py drop   "詞1 詞2"       棄用：同時寫 handpicked 的 -詞 與 rejected（防補詞復活）
    wishd.py pending                看待應用：暫存詞 / 已改未重跑的意願檔
    wishd.py order  <碼> A>B>C [--dry-run] [--note 說明]
    wishd.py apply  --yes [--no-deploy]   跑管道（+44碼閘門+部署）；無 --yes 只列待辦
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
RIME = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
FREQ = os.path.join(HERE, 'dict_pipeline')
DICT = os.path.join(RIME, 'xuma_L.dict.yaml')
HP = os.path.join(FREQ, 'handpicked.txt')
OV = os.path.join(FREQ, 'manual_overrides.txt')
REJ = os.path.join(FREQ, 'archive', 'rulings', 'rejected.txt')
SPOOL = os.path.join(FREQ, 'spool_pending.txt')      # 與 /add 共用同一個暫存
STATE = os.path.join(FREQ, '.applied.json')          # 已應用指紋（派生狀態，不入庫）

HAN2_4 = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]{2,4}$')


# ── 讀取層 ──────────────────────────────────────────────────────
_CACHE = {'entries': None, 'mtime': None, 'groups': None, 'wc': None}


def load_entries(refresh=False):
    """解析詞典為 [(word, code, weight, lineno, tag, section)]，按 mtime 緩存。

    19.6 萬條，每次請求重解析約 0.5s——務必走緩存。section 由併入區標記行切分。
    """
    mt = os.path.getmtime(DICT)
    if not refresh and _CACHE['entries'] is not None and _CACHE['mtime'] == mt:
        return _CACHE['entries']
    entries = []
    section = '主體'
    tag = ''
    started = False
    for i, line in enumerate(io.open(DICT, encoding='utf-8'), 1):
        if not started:
            if line.rstrip('\n') == '...':
                started = True
            continue
        if line.startswith('#'):
            if '併入區' in line:
                section = '併入區'
            m = re.match(r'#\s*\[([^\]]+)\]', line)
            if m:
                tag = m.group(1)
            continue
        if '\t' not in line:
            continue
        c = line.rstrip('\n').split('\t')
        w = c[0]
        code = c[1] if len(c) > 1 else ''
        wt = c[2] if len(c) > 2 else ''
        entries.append((w, code, wt, i, tag, section))
        tag = ''
    _CACHE.update(entries=entries, mtime=mt, groups=None, wc=None)
    return entries


def load_dict():
    """返回 (groups, wordcodes)：groups[code] = [(word, weight, lineno, tag)]；
    wordcodes[word] = {該詞出現過的全部碼}（一詞多碼，判同碼必須用交集）"""
    if _CACHE['groups'] is not None:
        return _CACHE['groups'], _CACHE['wc']
    groups, wc = {}, {}
    for w, code, wt, ln, tag, _sec in load_entries():
        groups.setdefault(code, []).append((w, wt, ln, tag))
        wc.setdefault(w, set()).add(code)
    _CACHE.update(groups=groups, wc=wc)
    return groups, wc


def invalidate():
    _CACHE.update(entries=None, mtime=None, groups=None, wc=None)


def load_handpicked_parts():
    """返回 (正常詞集, 棄用詞集)。棄用＝行首 `-` 的詞條（管線據此從詞庫刪除並記審計）。"""
    add, drop = set(), set()
    if not os.path.exists(HP):
        return add, drop
    for l in io.open(HP, encoding='utf-8'):
        l = l.strip()
        if not l or l.startswith('#') or l.startswith('## '):
            continue
        if l.startswith('-') and len(l) > 1:
            drop.add(l[1:])
        else:
            add.add(l)
    return add, drop


def load_handpicked():
    add, drop = load_handpicked_parts()
    return add | drop


def load_overrides():
    """返回 [(lineno, words, raw)]，跳過註釋空行。"""
    out = []
    if not os.path.exists(OV):
        return out
    for i, l in enumerate(io.open(OV, encoding='utf-8'), 1):
        body = l.split('#')[0].strip()
        if not body:
            continue
        ws = [w for w in re.split(r'\s*>\s*', body) if w]
        out.append((i, ws, l.rstrip('\n')))
    return out


# ── 校驗層 ──────────────────────────────────────────────────────
def check_order(code, seq, groups, words):
    """校驗一次次序意願。返回 (ok, 錯誤列表, 警告列表)。"""
    err, warn = [], []
    code = code.lower()
    if code not in groups:
        return False, [f'碼組 {code} 不存在於詞庫'], []
    members = {w for w, _, _, _ in groups[code]}
    if len(seq) < 2:
        err.append('至少要兩個詞才有次序可言')
    if len(set(seq)) != len(seq):
        err.append('清單內有重複詞')
    for w in seq:
        if w not in words:
            err.append(f'「{w}」不在詞庫')
        elif code not in words[w]:
            err.append(f'「{w}」不在 {code} 組（其碼：{"/".join(sorted(words[w]))}）')
    if err:
        return False, err, []
    if len(set.intersection(*[words[w] for w in seq])) == 0:
        err.append('這些詞沒有共同碼組')
        return False, err, []
    rest = sorted(members - set(seq))
    if rest:
        warn.append(f'同碼組另有 {len(rest)} 詞未列入（不受次序約束，可能插入其間）：'
                    + '、'.join(rest[:8]) + ('…' if len(rest) > 8 else ''))
    if len(seq) == 2:
        warn.append('兩詞語法＝交換頻分（A 上移到 B 原位置之上）；'
                    '若要「整組重排」請把全部成員都列出')
    return True, err, warn


def existing_conflicts(seq):
    """找出與本次意願共享任一词的既有裁定行（這些行會被移除後重寫）。"""
    hits = []
    for ln, ws, raw in load_overrides():
        if set(ws) & set(seq):
            hits.append((ln, raw))
    return hits


# ── 寫入層 ──────────────────────────────────────────────────────
def write_override(code, seq, note=''):
    """先把共享詞的舊裁定行移除，再追加新行（附註釋）。原子替換。"""
    lines = io.open(OV, encoding='utf-8').read().splitlines(True) if os.path.exists(OV) else []
    killed = {ln for ln, _ in existing_conflicts(seq)}
    kept = [l for i, l in enumerate(lines, 1) if i not in killed]
    while kept and not kept[-1].strip():
        kept.pop()
    stamp = datetime.date.today().isoformat()
    add = [f"\n# {stamp} {code} 調序（wishd{'：' + note if note else ''}）\n",
           ' > '.join(seq) + '\n']
    tmp = OV + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        f.write(''.join(kept) + ''.join(add))
    os.replace(tmp, OV)
    return len(killed)


def parse_seq(text):
    return [w.strip() for w in re.split(r'\s*>\s*', text.strip()) if w.strip()]


# ── 診斷：探測可能不合理的詞頻（只用詞典自身，不依賴語料）────────
def diagnose(limit=60):
    """原則：**只在同一尺標內比較**。

    詞典裏有兩套權重尺標——簡碼區（1–3 碼）是從原表逐字節繼承的舊值，全碼區（4 碼）
    是管道重算的 10^score×1e9。跨區比大小沒有意義（例：內容 簡碼 1.24e9 / 全碼 30.5 萬），
    故所有判據要麼同組內、要麼只看「是否為零」這類尺標無關的性質。
    """
    groups, wc = load_dict()
    rows_of = {}
    for w, code, wt, ln, tag, sec in load_entries():
        rows_of.setdefault(w, []).append((code, int(wt) if wt and wt.isdigit() else 0, ln))
    charw = {w: max(v for _, v, _ in rs) for w, rs in rows_of.items() if len(w) == 1}
    # 常用門檻取「非零字頻」的中位數——七萬多個擴展區字權重為 0，含零取中位數會得到 0
    nz = sorted(v for v in charw.values() if v > 0)
    common = nz[len(nz) // 2] if nz else 0
    HAN = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]+$')

    same_wt, zero_common, long_over_short, overflow = [], [], [], []
    for code, rs in groups.items():
        ranked = sorted(rs, key=lambda r: -(int(r[1]) if r[1].isdigit() else 0))
        bywt = {}
        for w, wt, ln, tag in ranked:
            v = int(wt) if wt and wt.isdigit() else 0
            if len(w) < 2 or not HAN.match(w):
                continue
            bywt.setdefault(v, []).append(w)
            if v == 0 and common > 0:
                comps = [charw.get(c, 0) for c in w]
                if comps and min(comps) >= common:
                    zero_common.append({'w': w, 'code': code, 'minchar': min(comps)})
            if len(code) == 4 and v > 10 ** 9:
                overflow.append({'w': w, 'code': code, 'wt': v})
        for v, ws in bywt.items():
            if len(set(ws)) > 1 or len(ws) > 1:
                same_wt.append({'code': code, 'wt': v, 'words': ws})
        for a in range(len(ranked)):
            for b in range(a + 1, len(ranked)):
                wa, wb = ranked[a][0], ranked[b][0]
                if len(wa) > len(wb) >= 2 and HAN.match(wa):
                    long_over_short.append({'code': code, 'hi': wa, 'lo': wb,
                                            'hiwt': ranked[a][1], 'lowt': ranked[b][1]})
                    break

    invert = []
    for w, rs in rows_of.items():
        if len(rs) < 2 or len(w) < 2:
            continue
        ranks = []
        for code, v, ln in rs:
            names = [r[0] for r in sorted(groups[code],
                     key=lambda r: -(int(r[1]) if r[1].isdigit() else 0))]
            ranks.append((names.index(w) + 1, len(names), code))
        if len(ranks) < 2:
            continue
        pcts = [(n / tot) if tot > 1 else 0 for n, tot, _ in ranks]
        if max(pcts) - min(pcts) >= 0.6:
            invert.append({'w': w,
                           'ranks': [{'code': c, 'rank': n, 'of': tot} for n, tot, c in ranks]})

    return {
        'A': {'name': '同組同分（不同詞）', 'n': len(same_wt),
              'desc': '同一碼組內不同詞拿到完全相同的頻分——多半是缺頻回退到同一默認值，或異體並列',
              'items': sorted(same_wt, key=lambda x: -len(x['words']))[:limit]},
        'B': {'name': '零頻分但字皆常用', 'n': len(zero_common),
              'desc': '組成字都不算冷僻，詞本身卻沒有頻分——語料缺該詞，排序會沉底',
              'items': sorted(zero_common, key=lambda x: -x['minchar'])[:limit]},
        'C': {'name': '長詞壓短詞', 'n': len(long_over_short),
              'desc': '同碼組內字數更多的詞排在同碼短詞之前——不一定是錯，但值得看一眼',
              'items': long_over_short[:limit]},
        'D': {'name': '跨區排名倒掛', 'n': len(invert),
              'desc': '同一個詞在簡碼組與全碼組的相對名次差距 ≥60%——兩套權重尺標不一致所致，僅供參考',
              'items': invert[:limit]},
        'E': {'name': '全碼區權重溢出', 'n': len(overflow),
              'desc': '4 碼條目權重 >1e9，而重算規則下 p≤1 ⇒ 應 ≤1e9；非 0 即為真異常',
              'items': overflow[:limit]},
    }


# ── 展示層 ──────────────────────────────────────────────────────
def fmt_group(code, groups, hp, mark=()):
    rows = groups.get(code, [])
    if not rows:
        return f'碼組 {code}：無成員'
    out = [f'碼組 {code}（{len(rows)} 詞）']
    for w, wt, ln, tag in rows:
        flags = []
        if w in hp:
            flags.append('手收')
        if tag:
            m = re.search(r'\[([^\]]+)\]', tag)
            if m:
                flags.append(m.group(1)[:40])
        if w in mark:
            flags.append('★')
        f = ('  [' + ' / '.join(flags) + ']') if flags else ''
        out.append(f'  {w}\t{wt or "(無頻分)"}\t第{ln}行{f}')
    return '\n'.join(out)


def suspicious(groups, hp, limit=40):
    """可疑組：同分並列（可能是異體重複）、以及同組內互為異體形的成對詞。"""
    out = []
    for code, rows in groups.items():
        if len(rows) < 2:
            continue
        byw = {}
        for w, wt, ln, _ in rows:
            if wt:
                byw.setdefault(wt, []).append(w)
        for wt, ws in byw.items():
            if len(ws) >= 2:
                out.append((code, '同分並列', ws, wt))
    return out[:limit]


# ── 加詞（與 /add 共用 spool）────────────────────────────────────
def load_spool():
    if not os.path.exists(SPOOL):
        return []
    return [l.strip() for l in io.open(SPOOL, encoding='utf-8') if l.strip()]


def add_words(words):
    """校驗並追加到 spool（與 /add 同一條管道：spool → handpick_commit → 管道 → 閘門 → 部署）。

    校驗與 handpick_commit.sh 一致：2–4 個純漢字、不在詞庫、不在手收清單、不在暫存。
    禁族字與三字詞**不攔**——顯式收錄意圖壓倒黑名單（AGENTS 收錄規則第 4 條）。
    """
    groups, wc = load_dict()
    hp = load_handpicked()
    spool = load_spool()
    added, skipped = [], []
    for w in words:
        w = w.strip()
        if not w:
            continue
        if not HAN2_4.match(w):
            skipped.append((w, '不是 2–4 個純漢字'))
        elif w in wc:
            skipped.append((w, f'已在詞庫（{"/".join(sorted(wc[w]))}）'))
        elif w in hp:
            skipped.append((w, '已手收'))
        elif w in spool or w in [a for a, _ in added]:
            skipped.append((w, '已在暫存'))
        else:
            added.append((w, ''))
    if added:
        os.makedirs(os.path.dirname(SPOOL), exist_ok=True)
        tmp = SPOOL + '.tmp'
        with io.open(tmp, 'w', encoding='utf-8') as f:
            f.write(''.join(l + '\n' for l in spool + [w for w, _ in added]))
        os.replace(tmp, SPOOL)
        try:
            from _ledger import note as _note
            _note('spool_pending.txt', '加詞',
                  '＋' + '／'.join(w for w, _ in added) + '（意願台）')
        except Exception:
            pass
    return {'added': [w for w, _ in added],
            'skipped': [{'w': w, 'why': r} for w, r in skipped]}


def drop_words(words, reason=''):
    """就地棄用：**同時**寫 handpicked 的 `-詞` 與 rejected 台账。

    兩者缺一不可：
      只寫 `-詞` → 詞從現有詞庫刪掉，但補詞階段會從語料撈回來
      只寫 rejected → 補詞不復活，但現有詞庫那條還在
    另外會把該詞從 handpicked 的正常分組裏移除（否則同時「收」與「棄」，語義打架），
    並從暫存 spool 裏剔掉（避免剛棄用又入庫）。
    """
    groups, wc = load_dict()
    add, already = load_handpicked_parts()
    spool = load_spool()
    rejected = set()
    if os.path.exists(REJ):
        rejected = {l.strip() for l in io.open(REJ, encoding='utf-8')
                    if l.strip() and not l.lstrip().startswith('#')}

    done, skipped = [], []
    for w in words:
        w = w.strip()
        if not w:
            continue
        if w not in wc and w not in add and w not in already:
            skipped.append((w, '不在詞庫，也非手收'))
            continue
        if w in already:
            skipped.append((w, '已標記棄用（待應用）'))
            continue
        done.append(w)
    if not done:
        return {'dropped': [], 'skipped': [{'w': w, 'why': r} for w, r in skipped]}

    # handpicked：移除正常分組中的該詞，末尾追加「## 棄用」區
    lines = io.open(HP, encoding='utf-8').read().splitlines(True) if os.path.exists(HP) else []
    kept = [l for l in lines if l.strip() not in done or l.lstrip().startswith('#')]
    while kept and not kept[-1].strip():
        kept.pop()
    stamp = datetime.date.today().isoformat()
    need_hdr = '## 棄用' not in ''.join(kept)
    tail = ([f'\n## 棄用（wishd，{stamp} 起；行首 - 表示從詞庫刪除）\n'] if need_hdr else ['\n'])
    tail += [f'-{w}\n' for w in done]
    tmp = HP + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8') as f:
        f.write(''.join(kept) + ''.join(tail))
    os.replace(tmp, HP)

    # rejected：防補詞階段復活
    new_rej = [w for w in done if w not in rejected]
    if new_rej:
        with io.open(REJ, 'a', encoding='utf-8') as f:
            f.write(f"\n# wishd 棄用（{stamp}）" + (f"：{reason}" if reason else '') + '\n')
            f.write(''.join(w + '\n' for w in new_rej))

    # spool：剛棄用的不該還在待入庫隊列
    if spool:
        left = [w for w in spool if w not in done]
        if len(left) != len(spool):
            tmp = SPOOL + '.tmp'
            with io.open(tmp, 'w', encoding='utf-8') as f:
                f.write(''.join(w + '\n' for w in left))
            os.replace(tmp, SPOOL)

    try:
        from _ledger import note as _note
        _note('handpicked.txt', '棄用',
              '−' + '／'.join(done) + '（同時寫 rejected 防復活）'
              + (f'｜{reason}' if reason else ''))
    except Exception:
        pass
    return {'dropped': done, 'skipped': [{'w': w, 'why': r} for w, r in skipped],
            'rejected_added': new_rej}


# ── 待應用狀態 ──────────────────────────────────────────────────
def _sha(path):
    import hashlib
    if not os.path.exists(path):
        return ''
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()[:16]


def pending():
    """回傳「尚未應用到詞庫」的意願：暫存詞、改過但未重跑管道的意願檔。"""
    import json
    st = {}
    if os.path.exists(STATE):
        try:
            st = json.load(open(STATE, encoding='utf-8'))
        except Exception:
            st = {}
    cur = {'overrides_sha': _sha(OV), 'handpicked_sha': _sha(HP), 'dict_sha': _sha(DICT)}
    dirty_ov = st.get('overrides_sha') not in (None, cur['overrides_sha'])
    dirty_hp = st.get('handpicked_sha') not in (None, cur['handpicked_sha'])
    return {
        'spool': load_spool(),
        'overrides_changed': dirty_ov,
        'handpicked_changed': dirty_hp,
        'first_run': not st,
        'fingerprint': f"{cur['overrides_sha']}:{cur['handpicked_sha']}:{_sha(SPOOL)}",
        'count': len(load_spool()) + int(dirty_ov) + int(dirty_hp),
    }


def mark_applied():
    import json
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump({'overrides_sha': _sha(OV), 'handpicked_sha': _sha(HP),
                   'dict_sha': _sha(DICT),
                   'ts': datetime.datetime.now().isoformat(timespec='seconds')}, f)


# ── 動作 ────────────────────────────────────────────────────────
def do_order(args):
    dry = '--dry-run' in args
    note = ''
    if '--note' in args:
        i = args.index('--note')
        note = args[i + 1] if i + 1 < len(args) else ''
        args = args[:i] + args[i + 2:]
    args = [a for a in args if a != '--dry-run']
    if len(args) < 2:
        sys.exit('用法：wishd.py order <碼> "A > B > C" [--dry-run] [--note 說明]')
    code, seq = args[0].lower(), parse_seq(args[1])

    groups, words = load_dict()
    ok, err, warn = check_order(code, seq, groups, words)
    for w in warn:
        print(f'⚠ {w}')
    for e in err:
        print(f'✗ {e}')
    if not ok:
        return 1

    print(f'\n目標次序：{" > ".join(seq)}')
    hits = existing_conflicts(seq)
    if hits:
        print(f'將取代 {len(hits)} 條既有裁定：')
        for ln, raw in hits:
            print(f'  第{ln}行 {raw.strip()}')
    if dry:
        print('\n（--dry-run：未寫盤）')
        return 0

    n = write_override(code, seq, note)
    print(f'✓ 已寫入 {os.path.relpath(OV, RIME)}（取代 {n} 條）')
    sys.path.insert(0, HERE)
    from _ledger import note as _note
    _note('manual_overrides.txt', '次序裁定',
          f'{code}: {" > ".join(seq)}' + (f'｜{note}' if note else ''))
    print('  下一步：wishd.py apply（跑管道＋44 碼閘門＋部署）')
    return 0


def do_apply(args, log=print):
    """跑管道 → 44 碼閘門 → 部署。暫存有詞時走 handpick_commit.sh（與 /add 同一條路，
    含 spool→handpicked 的校驗與 flock）。

    二次確認在本函數之外：CLI 要 --yes，Web 要一次性 token。此處只負責執行。
    """
    no_deploy = '--no-deploy' in args
    p = pending()
    if p['spool']:
        log(f'① 暫存有 {len(p["spool"])} 詞 → 走 handpick_commit（含管道與閘門）……')
        r = subprocess.run(['bash', os.path.join(HERE, 'handpick_commit.sh')],
                           cwd=HERE, capture_output=True, text=True)
        log('\n'.join(r.stdout.splitlines()[-25:]) or '(無輸出)')
        if r.returncode != 0:
            log(r.stderr[-800:])
            return r.returncode
        mark_applied()
        log('完成（暫存已併入手收清單並部署）')
        return 0
    log('① 重跑詞頻管道……')
    r = subprocess.run([sys.executable, os.path.join(HERE, 'gen_freq_dict.py')],
                       cwd=HERE, capture_output=True, text=True)
    log('\n'.join([l for l in r.stdout.splitlines()
                    if l.startswith('[overrides') or '寫出' in l][-12:]))
    if r.returncode != 0:
        log(r.stderr[-1500:])
        return r.returncode
    log('② 部署前閘門：44 碼首選回歸……')
    a = subprocess.run([sys.executable, os.path.join(HERE, 'check_acceptance.py')],
                       cwd=HERE, capture_output=True, text=True)
    if a.returncode != 0:
        log('✗ 首選回歸未過，**中止部署**（詞庫已更新，git checkout 可回退）')
        log(a.stdout[-800:])
        return 1
    log('✓ 通過')
    if no_deploy:
        mark_applied()
        log('（--no-deploy：未部署）')
        return 0
    log('③ 部署（ibus restart）……')
    subprocess.run(['ibus', 'restart'], check=False)
    mark_applied()
    log('完成')
    return 0


def main():
    a = sys.argv[1:]
    if not a or a[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    cmd, rest = a[0], a[1:]

    if cmd == 'order':
        return do_order(rest)
    if cmd == 'apply':
        if '--yes' not in rest:
            p = pending()
            print('待應用：')
            print(f'  暫存詞 {len(p["spool"])}：' + ('、'.join(p['spool'][:12]) or '（空）'))
            print(f'  次序裁定已改動：{"是" if p["overrides_changed"] else "否"}')
            print(f'  手收清單已改動：{"是" if p["handpicked_changed"] else "否"}')
            print('\n確認要跑管道並部署，請加 --yes。')
            return 2
        return do_apply(rest)

    if cmd == 'add':
        if not rest:
            sys.exit('用法：wishd.py add "詞1 詞2 …"')
        words = [w for a in rest for w in re.split(r'[\s,、]+', a) if w]
        r = add_words(words)
        for w in r['added']:
            print(f'  ＋ {w}')
        for s in r['skipped']:
            print(f'  − {s["w"]}（{s["why"]}）')
        print(f'\n已加入暫存 {len(r["added"])} 詞；下一步 wishd.py apply --yes')
        return 0

    if cmd == 'drop':
        if not rest:
            sys.exit('用法：wishd.py drop "詞1 詞2" [--note 理由]')
        reason = ''
        if '--note' in rest:
            i = rest.index('--note')
            reason = rest[i + 1] if i + 1 < len(rest) else ''
            rest = rest[:i] + rest[i + 2:]
        words = [w for a in rest for w in re.split(r'[\s,、]+', a) if w]
        r = drop_words(words, reason)
        for w in r['dropped']:
            print(f'  − {w}')
        for s in r['skipped']:
            print(f'  · {s["w"]}（{s["why"]}）')
        if r['dropped']:
            print(f'\n已標記棄用 {len(r["dropped"])} 詞'
                  f'（rejected 新增 {len(r["rejected_added"])} 條）；下一步 wishd.py apply --yes')
        return 0

    if cmd == 'pending':
        p = pending()
        print(f'暫存詞 {len(p["spool"])}：' + ('、'.join(p['spool']) or '（空）'))
        print(f'次序裁定已改動：{"是" if p["overrides_changed"] else "否"}')
        print(f'手收清單已改動：{"是" if p["handpicked_changed"] else "否"}')
        return 0

    groups, words = load_dict()
    hp = load_handpicked()

    if cmd == 'list':
        if not rest:
            sys.exit('用法：wishd.py list <碼|詞>')
        key = rest[0]
        if key.lower() in groups:
            print(fmt_group(key.lower(), groups, hp))
            return 0
        codes = sorted(words.get(key, ()))
        if not codes:
            print(f'找不到：{key}')
            return 1
        for c in codes:
            print(fmt_group(c, groups, hp, mark=(key,)))
            print()
        return 0

    if cmd == 'search':
        q = rest[0] if rest else ''
        hits = [(w, c) for w, c in words.items() if q and q in w]
        for w, c in hits[:40]:
            print(f'  {w}\t{c}')
        print(f'共 {len(hits)} 條' + ('（顯示前 40）' if len(hits) > 40 else ''))
        return 0

    if cmd == 'suspicious':
        rows = suspicious(groups, hp)
        for code, kind, ws, wt in rows:
            print(f'  {code}\t{kind}\t{wt}\t{" / ".join(ws)}')
        print(f'共 {len(rows)} 組（上限內）')
        return 0

    if cmd == 'overrides':
        for ln, ws, raw in load_overrides():
            bad = [w for w in ws if w not in words]
            shared = (set.intersection(*[words[w] for w in ws])
                      if not bad else set())
            flag = '✓' if shared else '✗'
            note = '' if shared or bad else '（無共同碼組）'
            if bad:
                note += f'（未入庫：{bad}）'
            print(f'  {flag} 第{ln}行 {raw.strip()}{note}')
        return 0

    sys.exit(f'未知子命令：{cmd}（試 -h）')


if __name__ == '__main__':
    sys.exit(main())
