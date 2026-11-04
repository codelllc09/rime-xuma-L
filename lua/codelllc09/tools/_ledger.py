#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_ledger.py — 統一留痕庫（2026-11-05 加固；台賬唯一寫入口）

背景：2026-11-05 事故能恢復，靠的是「本地日誌與提交記錄互相印證」。要讓這個性質
恆久成立，凡**寫入 gitignore 區的數據承載文件**，改動必須留痕；且留痕本身必須入庫
（台賬若也不在 git，硬盤損毀則二重歷史與 git 同歸於盡）。

兩級落點（同目錄，皆 append-only、皆入庫）：
    IGNORED_CHANGES.log   人讀 TSV：ISO時間 \\t 路徑 \\t 動作 \\t 說明
    events.jsonl          機讀 JSONL：{"ts","actor","action","path","detail","tracked"}

路由規則：一次 note() 必定寫 events.jsonl；目標路徑**被 gitignore** 時**另**寫 TSV 行。
已入庫文件（git 本身即歷史）只進 events.jsonl，不污染人讀台賬——避免兩份記錄互相稀釋。

用法：
    from _ledger import note
    note('spool_pending.txt', '入庫', '收詞 3 個：甲/乙/丙')
    note('ibus', '部署', '重啟後錯誤 0')          # 非文件事件亦可（path 作通道名）

命令行（供 shell 腳本與人工補記）：
    python3 _ledger.py <路徑> <動作> <說明>      # 追加一行
    python3 _ledger.py --selfcheck               # 格式／時序／雙檔對賬
    python3 _ledger.py --tail 10                 # 看最近十條
"""
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
LOG_DIR = os.path.join(HERE, 'dict_pipeline', 'log')
TSV = os.path.join(LOG_DIR, 'IGNORED_CHANGES.log')
JSONL = os.path.join(LOG_DIR, 'events.jsonl')
TSV_HEADER = ('# IGNORED_CHANGES 台賬（append-only）：被 gitignore 的數據承載文件每次改動'
              '在此留痕（git 之外的歷史手段）\n# 格式：ISO時間\t路徑\t動作\t說明\n')


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def _clean(s):
    """單行化：制表符／換行會破壞 TSV 與 JSONL 的行結構。"""
    return ' '.join(str(s).replace('\t', ' ').split())


def _scrub(s):
    """脫敏（2026-11-05）：家目錄 → `~`、倉庫根 → `.`。
    台賬入庫後會進公開歷史，絕對路徑等於把用戶名與目錄結構一併公開。
    此前 events.jsonl 因寫入 `/home/<user>/Backups/...` 而被隱私掃描判 HIGH。"""
    s = str(s)
    home = os.path.expanduser('~')
    if home and home != '/':
        s = s.replace(home, '~')
    if ROOT and ROOT != '/':
        s = s.replace(ROOT, '.')
    return _clean(s)


def is_ignored(path):
    """該路徑是否落在 gitignore 區。git 不可用時保守返回 True（寧可多記）。"""
    if not path or os.path.isabs(path) or path.startswith('..'):
        return False          # 通道名／外部路徑：非倉庫數據文件，走 JSONL 即可
    try:
        r = subprocess.run(['git', '-C', ROOT, 'check-ignore', '-q', '--', path],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return True


def note(path, action, detail, actor=None):
    """記一條事件。返回寫入的記錄 dict（便於調用方打印）。"""
    path, action, detail = _scrub(path), _clean(action), _scrub(detail)
    if not (path and action):
        raise ValueError('note(): path 與 action 必填')
    actor = _clean(actor or os.path.basename(sys.argv[0] or 'unknown'))
    tracked = not is_ignored(path)
    rec = {'ts': _now(), 'actor': actor, 'action': action,
           'path': path, 'detail': detail, 'tracked': tracked}
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(JSONL, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    if not tracked:
        if not os.path.exists(TSV):
            with open(TSV, 'w', encoding='utf-8') as f:
                f.write(TSV_HEADER)
        with open(TSV, 'a', encoding='utf-8') as f:
            f.write(f"{rec['ts']}\t{path}\t{action}\t{detail}\n")
    return rec


def note_if_changed(path, action, detail, before_hash, actor=None):
    """僅在文件字節確實變動時留痕（防「每次運行都寫一行」的日誌洪水）。

    before_hash：調用方寫盤**之前**取得的摘要；None 表示文件原先不存在。
    """
    import hashlib
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    after = None
    if os.path.exists(full):
        after = hashlib.sha256(open(full, 'rb').read()).hexdigest()
    if after == before_hash:
        return None
    return note(path, action, detail, actor)


def hash_of(path):
    import hashlib
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.exists(full):
        return None
    return hashlib.sha256(open(full, 'rb').read()).hexdigest()


def read_tsv():
    """解析台賬爲 (ts, 路徑, 動作, 說明) 列表（跳過註釋與空行）。"""
    if not os.path.exists(TSV):
        return []
    rows = []
    for ln in open(TSV, encoding='utf-8'):
        if ln.startswith('#') or not ln.strip():
            continue
        parts = ln.rstrip('\n').split('\t')
        if len(parts) == 4:
            rows.append(tuple(parts))
    return rows


def read_jsonl():
    if not os.path.exists(JSONL):
        return []
    out = []
    for ln in open(JSONL, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append({'__bad__': ln})
    return out


def selfcheck():
    """台賬衛生：四欄完整、時間非降、JSONL 每行可解析、雙檔對賬。"""
    bad = []
    rows, js = read_tsv(), read_jsonl()
    prev = ''
    for ts, p, a, d in rows:
        try:
            datetime.datetime.fromisoformat(ts)
        except ValueError:
            bad.append(f'時間格式壞：{ts!r}')
        if ts < prev:
            bad.append(f'時間倒退：{prev} → {ts}')
        prev = ts
        if not p or not a:
            bad.append(f'空欄位：{p!r}/{a!r}')
    for r in js:
        if '__bad__' in r:
            bad.append(f'JSONL 壞行：{r["__bad__"][:60]}')
        elif not {'ts', 'actor', 'action', 'path'} <= set(r):
            bad.append(f'JSONL 缺欄：{r}')
    # 對賬：TSV 每一行都應能在 JSONL 找到同 ts+path 的非 tracked 記錄
    jset = {(r.get('ts'), r.get('path'), r.get('action')) for r in js if '__bad__' not in r}
    missing = [f'{ts} {p} {a}' for ts, p, a, _ in rows if (ts, p, a) not in jset]
    if missing:
        bad.append(f'{len(missing)} 條 TSV 行在 events.jsonl 無對應（雙檔失同步）：{missing[:3]}')
    return rows, js, bad


def main():
    args = sys.argv[1:]
    if not args or args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if args[0] == '--selfcheck':
        rows, js, bad = selfcheck()
        print(f'TSV {len(rows)} 行｜JSONL {len(js)} 行')
        if bad:
            print('✗ 台賬衛生：')
            for b in bad[:10]:
                print('   ', b)
            return 1
        print('✓ 台賬衛生：格式／時序／雙檔對賬全部通過')
        return 0
    if args[0] == '--backfill':
        # 一次性遷移：歷史 TSV 行（本模塊之前寫的）補進 events.jsonl，actor 標 migrated
        rows, js, _ = selfcheck()
        have = {(r.get('ts'), r.get('path'), r.get('action')) for r in js if '__bad__' not in r}
        n = 0
        with open(JSONL, 'a', encoding='utf-8') as f:
            for ts, p, a, d in rows:
                if (ts, p, a) in have:
                    continue
                f.write(json.dumps({'ts': ts, 'actor': 'migrated', 'action': a,
                                    'path': p, 'detail': d, 'tracked': False},
                                   ensure_ascii=False) + '\n')
                n += 1
        print(f'已補 {n} 條歷史台賬行進 events.jsonl')
        return 0
    if args[0] == '--tail':
        n = int(args[1]) if len(args) > 1 else 10
        for ts, p, a, d in read_tsv()[-n:]:
            print(f'{ts}\t{p}\t{a}\t{d}')
        return 0
    if len(args) < 3:
        sys.exit('用法：_ledger.py <路徑> <動作> <說明>')
    rec = note(args[0], args[1], args[2], actor='cli')
    print(f"已記：{rec['ts']} {rec['path']} {rec['action']}"
          f"{'' if rec['tracked'] else '（TSV+JSONL）'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
