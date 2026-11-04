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
    wishd.py order  <碼> A>B>C [--dry-run] [--note 說明]
    wishd.py apply  [--no-deploy]   跑管道（+44碼閘門+部署）
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

HAN2_4 = re.compile(r'^[\u3400-\u4dbf\u4e00-\u9fff]{2,4}$')


# ── 讀取層 ──────────────────────────────────────────────────────
def load_dict():
    """返回 (groups, wordcodes)：groups[code] = [(word, weight, lineno, tag)]；
    wordcodes[word] = {該詞出現過的全部碼}

    tag 取自該詞上方的 `# [..]` 註釋（手收/裁定等），僅作展示。
    """
    groups, words, _all = {}, {}, {}
    started = False
    last_comment = ''
    for i, line in enumerate(io.open(DICT, encoding='utf-8'), 1):
        if not started:
            if line.rstrip('\n') == '...':
                started = True
            continue
        if line.startswith('#'):
            last_comment = line.strip()
            continue
        if '\t' not in line:
            continue
        c = line.rstrip('\n').split('\t')
        w, code = c[0], c[1] if len(c) > 1 else ''
        wt = c[2] if len(c) > 2 else ''
        groups.setdefault(code, []).append((w, wt, i, last_comment))
        last_comment = ''
    # 一词可有多码（同一词形在不同码组各有一条）；判定同码必须用交集，不能用单值映射
    for code, rows in groups.items():
        for w, *_ in rows:
            _all.setdefault(w, set()).add(code)
    return groups, _all


def load_handpicked():
    s = set()
    if not os.path.exists(HP):
        return s
    for l in io.open(HP, encoding='utf-8'):
        l = l.strip()
        if not l or l.startswith('#') or l.startswith('## '):
            continue
        s.add(l[1:] if l.startswith('-') else l)
    return s


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


def do_apply(args):
    """跑管道 → 部署前閘門 → 部署。與 /add 的入庫重跑同款路徑。"""
    no_deploy = '--no-deploy' in args
    print('① 重跑詞頻管道……')
    r = subprocess.run([sys.executable, os.path.join(HERE, 'gen_freq_dict.py')],
                       cwd=HERE, capture_output=True, text=True)
    sys.stdout.write(r.stdout[-2500:])
    if r.returncode != 0:
        print(r.stderr[-1500:], file=sys.stderr)
        return r.returncode
    print('\n② 部署前閘門：44 碼首選回歸……')
    a = subprocess.run([sys.executable, os.path.join(HERE, 'check_acceptance.py')],
                       cwd=HERE, capture_output=True, text=True)
    if a.returncode != 0:
        print('✗ 首選回歸未過，**中止部署**（詞庫已更新，git checkout 可回退）', file=sys.stderr)
        print(a.stdout[-800:], file=sys.stderr)
        return 1
    print('✓ 通過')
    if no_deploy:
        print('\n（--no-deploy：未部署）')
        return 0
    print('\n③ 部署（ibus restart）……')
    subprocess.run(['ibus', 'restart'], check=False)
    print('完成')
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
        return do_apply(rest)

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
