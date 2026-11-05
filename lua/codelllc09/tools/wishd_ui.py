#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wishd_ui.py — 詞庫意願台（Web，2026-11-05）

本機單頁界面：調同碼組次序 + 加詞入暫存 + 應用（管道＋44 碼閘門＋部署）。
寫入一律走 wishd.py 的校驗，不另開一套邏輯——避免繞過護欄。

啟動：
    cd lua/codelllc09/tools
    python3 wishd_ui.py            # 瀏覽器開 http://127.0.0.1:8721

安全（三層，缺一不可）：
  ① 只綁 127.0.0.1——**共享機器勿開**：這是能改詞庫並重跑管道的服務。
  ② 所有 POST 必須帶 `X-Wishd: 1`：跨站表單帶不上自訂標頭，本服務也不發 CORS 標頭 ⇒ 擋 CSRF。
  ③ **應用是破壞性動作，需二次確認**：先取一次性 token（綁定當時的待辦指紋，TTL 120s），
     提交時還要明文 `APPLY`；指紋變了（待辦內容被改動）token 即失效——防 TOCTOU。
     前端亦要求手打 APPLY，不是點一下 yes。
"""
import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import wishd  # noqa: E402

HOST = '127.0.0.1'
PORT = int(os.environ.get('WISHD_PORT', '8721'))
TOKEN_TTL = 120

JOB = {'state': 'idle', 'log': '', 'started': 0.0, 'mode': ''}
JOB_LOCK = threading.Lock()
TOKENS = {}
TOKEN_LOCK = threading.Lock()


# ── 一次性 token（綁定待辦指紋）─────────────────────────────────
def issue_token():
    p = wishd.pending()
    tok = secrets.token_urlsafe(12)
    with TOKEN_LOCK:
        now = time.time()
        for k in [k for k, v in TOKENS.items() if v['exp'] < now]:
            TOKENS.pop(k, None)
        TOKENS[tok] = {'fp': p['fingerprint'], 'exp': now + TOKEN_TTL}
    return tok, p


def check_token(tok, fp_now):
    with TOKEN_LOCK:
        rec = TOKENS.pop(tok, None)     # 一次性：取出即作廢
    if not rec:
        return False, 'token 無效或已用過'
    if rec['exp'] < time.time():
        return False, 'token 已過期（120 秒）'
    if rec['fp'] != fp_now:
        return False, '待辦內容已變動，請重新確認'
    return True, ''


# ── 管線任務 ────────────────────────────────────────────────────
def run_apply(no_deploy=False):
    def work():
        t0 = time.time()
        p = wishd.pending()
        with JOB_LOCK:
            JOB.update(state='running', log='', started=t0,
                       mode='handpick' if p['spool'] else 'pipeline')
        def log(s):
            with JOB_LOCK:
                JOB['log'] += str(s) + '\n'
        try:
            rc = wishd.do_apply(['--no-deploy'] if no_deploy else [], log=log)
            wishd.invalidate()
            with JOB_LOCK:
                JOB['state'] = 'done' if rc == 0 else 'failed'
                JOB['elapsed'] = round(time.time() - t0, 1)
        except Exception as e:
            with JOB_LOCK:
                JOB['state'] = 'failed'
                JOB['log'] += '✗ 例外：' + str(e)
    threading.Thread(target=work, daemon=True).start()


# ── API ─────────────────────────────────────────────────────────
def api_group(code):
    groups, wc = wishd.load_dict()
    hp = wishd.load_handpicked()
    code = code.lower()
    rows = groups.get(code, [])
    ovs = []
    for ln, ws, raw in wishd.load_overrides():
        shared = set.intersection(*[wc.get(w, set()) for w in ws]) if ws else set()
        if code in shared:
            ovs.append({'line': ln, 'words': ws, 'raw': raw.strip()})
    return {'code': code, 'exists': bool(rows),
            'members': [{'w': w, 'wt': wt, 'line': ln, 'hp': w in hp,
                         'tag': (t or '').lstrip('# ')} for w, wt, ln, t in rows],
            'overrides': ovs}


def api_search(q):
    _, wc = wishd.load_dict()
    hits = [(w, sorted(c)) for w, c in wc.items() if q and q in w]
    hits.sort(key=lambda x: (len(x[0]), x[0]))
    return {'hits': [{'w': w, 'codes': c} for w, c in hits[:60]], 'total': len(hits)}


def api_recent(n=12):
    """最近動態：讀台賬尾部。比「可疑組」有用——那裏 89.7% 是上游帶來的零權重符號。"""
    import json as _j
    p = os.path.join(HERE, 'dict_pipeline', 'log', 'events.jsonl')
    out = []
    if os.path.exists(p):
        lines = io.open(p, encoding='utf-8').read().splitlines()[-200:]
        for ln in reversed(lines):
            try:
                r = _j.loads(ln)
            except Exception:
                continue
            out.append({'ts': r.get('ts', ''), 'action': r.get('action', ''),
                        'path': r.get('path', ''), 'detail': (r.get('detail') or '')[:80]})
            if len(out) >= n:
                break
    return {'rows': out}


def api_suspicious():
    groups, _ = wishd.load_dict()
    hp = wishd.load_handpicked()
    rows = wishd.suspicious(groups, hp, limit=80)
    return {'rows': [{'code': c, 'kind': k, 'wt': wt, 'words': ws} for c, k, ws, wt in rows]}


def api_dict(params):
    """字典瀏覽：篩選 + 分頁。

    默認按**詞形合併**——同一個詞的簡碼與全碼併成一行（如 `美麗` → x · xjgm）。
    詞典裏 8,675 個詞有多碼（多爲三碼簡碼＋四碼全碼），逐條目顯示會有大量重複行。
    `group=0` 可切回逐條目視圖（查行號時用）。
    """
    g = lambda k, d='': (params.get(k, [d])[0] if isinstance(params.get(k), list) else params.get(k, d)) or d
    word = g('word')
    code = g('code').lower()
    ln = g('len')
    src = g('src')
    sec = g('sec')
    clen = g('clen')            # '' | short(1–3) | full(4) | '1' | '2' | '3' | '4'
    sort = g('sort', 'line')
    grouped = g('group', '1') == '1'
    try:
        page = max(1, int(g('page', '1')))
    except ValueError:
        page = 1
    try:
        size = min(300, max(20, int(g('size', '100'))))
    except ValueError:
        size = 100

    entries = wishd.load_entries()
    hp_add, hp_drop = wishd.load_handpicked_parts()
    ovmap = override_codes()
    ov_words = {w for v in ovmap.values() for w in v['words']}

    def code_ok(c):
        """碼長篩選。簡碼＝1–3 碼（含一簡/二簡/三碼），全碼＝4 碼。"""
        if not clen:
            return True
        n = len(c)
        if clen == 'short':
            return n <= 3
        if clen == 'full':
            return n == 4
        return n == int(clen)

    def hit(w, c, tag):
        if word and word not in w:
            return False
        if ln:
            if ln == 'other':
                if len(w) <= 4:
                    return False
            elif len(w) != int(ln):
                return False
        if sec:
            pass    # 區段在聚合後按主條目判
        if src == 'hp' and w not in hp_add:
            return False
        if src == 'tag' and not tag:
            return False
        if src == 'drop' and w not in hp_drop:
            return False
        if src == 'ov' and w not in ov_words:
            return False
        if src == 'plain' and (tag or w in hp_add or w in hp_drop or w in ov_words):
            return False
        return True

    if not grouped:
        rows = []
        for w, c, wt, line, tag, sn, in entries:
            if code and not c.lower().startswith(code):
                continue
            if not code_ok(c):
                continue
            if sec and sn != sec:
                continue
            if not hit(w, c, tag):
                continue
            rows.append({'w': w, 'codes': [c], 'primary': c, 'wt': wt, 'line': line,
                         'ov': w in ov_words,
                         'lines': [line], 'tag': tag, 'sec': sn,
                         'hp': w in hp_add, 'dropped': w in hp_drop})
        if sort == 'weight':
            rows.sort(key=lambda r: (-(int(r['wt']) if r['wt'].isdigit() else -1), r['line']))
        elif sort == 'code':
            rows.sort(key=lambda r: (r['primary'], r['line']))
        elif sort == 'word':
            rows.sort(key=lambda r: (r['w'], r['line']))
        total = len(rows)
    else:
        # 先選出符合條件的詞形（碼前綴＝任一碼命中），再把該詞的全部條目聚合
        sel = set()
        for w, c, wt, line, tag, sn in entries:
            if code and not c.lower().startswith(code):
                continue
            if not code_ok(c):
                continue
            if hit(w, c, tag):
                sel.add(w)
        agg = {}
        for w, c, wt, line, tag, sn in entries:
            if w not in sel or not code_ok(c):
                continue
            a = agg.get(w)
            if a is None:
                agg[w] = {'w': w, 'codes': [c], 'entries': [(c, wt, line, tag, sn)]}
            else:
                a['codes'].append(c)
                a['entries'].append((c, wt, line, tag, sn))
        rows = []
        for w, a in agg.items():
            ents = a['entries']
            # 主條目＝碼最長者（全碼）；同長取頻分高者
            prim = max(ents, key=lambda e: (len(e[0]),
                                            int(e[1]) if e[1].isdigit() else -1))
            codes = sorted({e[0] for e in ents}, key=lambda c: (len(c), c))
            tag = next((e[3] for e in ents if e[3]), '')
            if sec and prim[4] != sec:
                continue
            rows.append({'w': w, 'codes': codes, 'primary': prim[0], 'wt': prim[1],
                         'ov': w in ov_words,
                         'line': prim[2], 'lines': sorted({e[2] for e in ents}),
                         'tag': tag, 'sec': prim[4],
                         'hp': w in hp_add, 'dropped': w in hp_drop})
        if sort == 'weight':
            rows.sort(key=lambda r: (-(int(r['wt']) if r['wt'].isdigit() else -1), r['line']))
        elif sort == 'code':
            rows.sort(key=lambda r: (r['primary'], r['line']))
        elif sort == 'word':
            rows.sort(key=lambda r: (r['w'], r['line']))
        total = len(rows)

    start = (page - 1) * size
    page_rows = rows[start:start + size]
    for r in page_rows:
        r.pop('entries', None)
    return {'total': total, 'page': page, 'size': size,
            'pages': max(1, (total + size - 1) // size),
            'grouped': grouped, 'rows': page_rows}


_OVC = {'key': None, 'v': {}}


def override_codes():
    """帶 mtime 快取的裁定位集合（避免每次請求重算）。"""
    import os as _os
    p = wishd.OV
    mt = _os.path.getmtime(p) if _os.path.exists(p) else 0
    dm = _os.path.getmtime(wishd.DICT)
    key = (mt, dm)
    if _OVC['key'] != key:
        _OVC['v'] = wishd.override_index()
        _OVC['key'] = key
    return _OVC['v']


def api_override_codes():
    ov = override_codes()
    return {'n': len(ov), 'codes': ov}


def api_map(params):
    """碼空間熱力地圖：給定前綴，返回 26 個字母分支的詞數／碼組數。

    徐碼最長 4 碼，故樹深 4；到第 4 層即為一個完整碼組。
    著色用 log(1+count) 歸一，否則 a 開頭（十萬詞）與 z 開頭會差幾個數量級、全糊成一片。
    """
    import math
    g = lambda k, d='': (params.get(k, [d])[0] if isinstance(params.get(k), list) else params.get(k, d)) or d
    prefix = g('prefix').lower()
    if prefix and (not prefix.isalpha() or len(prefix) > 4):
        return {'error': 'prefix 必須是 1–4 個字母'}
    entries = wishd.load_entries()
    words = {}
    for w, c, wt, ln, tag, sec in entries:
        if not c.startswith(prefix):
            continue
        words[c] = words.get(c, 0) + 1
    total_words = sum(words.values())
    exact = words.get(prefix, 0) if prefix else 0

    kids = {}
    for ch in 'abcdefghijklmnopqrstuvwxyz':
        kids[ch] = {'letter': ch, 'words': 0, 'groups': 0}
    for c, n in words.items():
        if len(c) <= len(prefix):
            continue
        d = kids[c[len(prefix)]]
        d['words'] += n
        d['groups'] += 1
    # 熱度＝本層內的線性歸一（min..max）。用對數會把 4k–11k 壓成 0.90–1.00，全糊成一片。
    mx = max(v['words'] for v in kids.values()) or 1
    mn = min(v['words'] for v in kids.values())
    span = (mx - mn) or 1
    ov = override_codes()
    for ch, v in kids.items():
        v['ov'] = sum(1 for c in ov if c.startswith(prefix + ch))
    for v in kids.values():
        v['heat'] = round((v['words'] - mn) / span, 3)
    return {'prefix': prefix, 'depth': len(prefix),
            'total_words': total_words, 'total_groups': len(words),
            'exact_words': exact, 'max': mx,
            'children': sorted(kids.values(), key=lambda v: -v['words'])}


def api_learn():
    """跑 learn_weights.py 原型並返回 JSON。約 2 秒。"""
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, 'learn_weights.py'),
                        '--json', '--iters', '1200'],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        return {'error': (r.stderr or r.stdout)[-400:]}
    try:
        return json.loads(r.stdout)
    except Exception as e:
        return {'error': f'解析失敗：{e}'}


def api_diag():
    """探測可疑詞頻：只用詞典自身能算出的矛盾，不需語料。"""
    return wishd.diagnose()


def api_dict_stats():
    import collections
    entries = wishd.load_entries()
    by_len = collections.Counter(len(w) for w, *r in entries)
    by_code_len = collections.Counter(len(c) for _, c, *r in entries)
    tagged = sum(1 for e in entries if e[4])
    hp = wishd.load_handpicked()
    return {'total': len(entries), 'words': len({e[0] for e in entries}),
            'by_len': dict(sorted(by_len.items())),
            'by_code_len': dict(sorted(by_code_len.items())),
            'tagged': tagged, 'handpicked': sum(1 for e in entries if e[0] in hp)}


def api_drop(payload):
    words = payload.get('words') or []
    reason = (payload.get('note') or '').strip()
    r = wishd.drop_words(words, reason)
    try:
        from _ledger import note as _note
        if r['dropped']:
            _note('handpicked.txt', '棄用',
                  '−' + '／'.join(r['dropped']) + '（Web UI 就地棄用）'
                  + (f'｜{reason}' if reason else ''))
    except Exception:
        pass
    return r


def api_order(payload):
    code = (payload.get('code') or '').lower()
    seq = payload.get('seq') or []
    groups, wc = wishd.load_dict()
    ok, err, warn = wishd.check_order(code, seq, groups, wc)
    conf = [{'line': ln, 'raw': raw.strip()}
            for ln, raw in wishd.existing_conflicts(seq)] if ok else []
    cur = [w for w, *_ in groups.get(code, [])]
    return {'ok': ok, 'errors': err, 'warnings': warn, 'conflicts': conf,
            'current': cur, 'currentFiltered': [w for w in cur if w in seq], 'target': seq}


def api_commit(payload):
    code = (payload.get('code') or '').lower()
    seq = payload.get('seq') or []
    note = (payload.get('note') or '').strip()
    groups, wc = wishd.load_dict()
    ok, err, warn = wishd.check_order(code, seq, groups, wc)
    if not ok:
        return {'ok': False, 'errors': err}
    n = wishd.write_override(code, seq, note)
    try:
        from _ledger import note as _note
        _note('manual_overrides.txt', '次序裁定',
              f'{code}: {" > ".join(seq)}（Web UI）' + (f'｜{note}' if note else ''))
    except Exception:
        pass
    return {'ok': True, 'replaced': n, 'warnings': warn}


# ── 日誌中心 ────────────────────────────────────────────────────
# 白名單制：只允許讀取下列具名來源，拒絕任意路徑（這是個綁在 127.0.0.1 的服務，
# 但「能讀任意檔案」仍然是個不必要的口子）。
LOG_SOURCES = {
    'events':      ('事件台賬',      ('file', 'dict_pipeline/log/events.jsonl'), 'jsonl'),
    'ledger':      ('人讀台賬',      ('file', 'dict_pipeline/log/IGNORED_CHANGES.log'), 'tsv'),
    'add_commit':  ('入庫明細',      ('file', 'dict_pipeline/log/add_commit.log'), 'text'),
    'hp_removals': ('棄用審計',      ('file', 'dict_pipeline/log/hp_removals.log'), 'text'),
    'engine':      ('引擎日誌（最新）', ('glob', '/tmp/rime.ibus.*.log.INFO.*'), 'text'),
    'precommit':   ('提交門禁',      ('path', '/tmp/rime-precommit.log'), 'text'),
    'prepush':     ('推送門禁',      ('path', '/tmp/rime-prepush.log'), 'text'),
    'privacy':     ('隱私門禁',      ('path', '/tmp/rime-prepush-privacy.log'), 'text'),
    'drill':       ('演練輸出',      ('path', '/tmp/rime-drill-det.log'), 'text'),
    'daemon':      ('意願台服務',    ('path', '/tmp/wishd.log'), 'text'),
    'git':         ('提交歷史',      ('git', ''), 'text'),
}


def _resolve(kind, arg):
    import glob as _glob
    if kind == 'file':
        return os.path.join(HERE, arg)
    if kind == 'path':
        return arg
    if kind == 'glob':
        hits = sorted(_glob.glob(arg))
        return hits[-1] if hits else None
    return None


def api_log_list():
    out = []
    for k, (name, spec, kind) in LOG_SOURCES.items():
        p = _resolve(spec[0], spec[1])
        st = None
        if spec[0] == 'git':
            n = len(subprocess.run(['git', '-C', wishd.RIME, 'log', '--oneline'],
                                   capture_output=True, text=True).stdout.splitlines())
            out.append({'key': k, 'name': name, 'kind': 'text', 'exists': True,
                        'size': n, 'unit': '條', 'mtime': ''})
            continue
        if p and os.path.exists(p):
            st = os.stat(p)
        out.append({'key': k, 'name': name, 'kind': kind, 'exists': bool(st),
                    'size': st.st_size if st else 0, 'unit': 'B',
                    'mtime': time.strftime('%m-%d %H:%M',
                                           time.localtime(st.st_mtime)) if st else '',
                    'file': os.path.basename(p) if p else ''})
    return {'sources': out}


def api_log(key, n=300, q='', errors=False, action=''):
    if key not in LOG_SOURCES:
        return {'error': '未知來源'}
    name, spec, kind = LOG_SOURCES[key]
    if spec[0] == 'git':
        args = ['git', '-C', wishd.RIME, 'log', f'-{n}', '--date=short',
                '--format=%h  %ad  %s']
        if q:
            args += ['--grep', q, '-i']
        r = subprocess.run(args, capture_output=True, text=True)
        return {'name': name, 'text': r.stdout, 'file': '(git)'}

    p = _resolve(spec[0], spec[1])
    if not p or not os.path.exists(p):
        return {'name': name, 'text': '', 'file': '(不存在)', 'missing': True}
    import collections as _c
    raw = open(p, encoding='utf-8', errors='replace').read().splitlines()
    if kind == 'jsonl':
        items, acts = [], _c.Counter()
        for ln in raw:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            acts[r.get('action', '')] += 1
            if action and r.get('action') != action:
                continue
            if q and q not in ln:
                continue
            items.append(f"{r.get('ts','')[5:19].replace('T',' ')}  "
                         f"{r.get('action',''):<6} {r.get('path','')[:34]:<34} "
                         f"{r.get('detail','')}")
        return {'name': name, 'text': '\n'.join(items[-n:]),
                'file': os.path.basename(p), 'actions': dict(acts),
                'total': len(items)}
    lines = raw
    if errors:
        lines = [l for l in lines
                 if re.search(r'error|ERROR|E[0-9]{4}|fatal|Traceback', l)]
    if q:
        lines = [l for l in lines if q in l]
    return {'name': name, 'text': '\n'.join(lines[-n:]),
            'file': os.path.basename(p), 'total': len(lines),
            'lines': len(raw)}


# ── 設置：開關 / 快捷鍵 / 引擎鏈 / 引用校驗 ──────────────────────
def _yaml(path):
    try:
        import yaml
        return yaml.safe_load(io.open(path, encoding='utf-8')) or {}
    except Exception as e:
        return {'__error__': f'{type(e).__name__}: {e}'}


def _walk_lua_refs(node, out):
    """從配置樹裏找出所有 lua_*@*模組 引用。"""
    if isinstance(node, str):
        # 形如 lua_filter@*ace/xuma_spelling*filter@xmsp —— 模組是第一個 * 之後、
        # 第二個 * 之前那段；第二段是命名空間/組件名，不是檔名的一部分
        m = re.search(r'lua_(translator|filter|processor|segmentor)@\*?([^@]+)', node)
        if m:
            mod = m.group(2).split('*')[0].strip()
            out.append((m.group(1), mod))
    elif isinstance(node, list):
        for x in node:
            _walk_lua_refs(x, out)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_lua_refs(v, out)


def _lua_requires(path):
    """掃出 lua 檔裏的 require('...')——隱藏依賴就是這麼漏掉的。"""
    out = []
    try:
        for i, line in enumerate(io.open(path, encoding='utf-8'), 1):
            for m in re.finditer(r"require\s*\(?\s*['\"]([^'\"]+)['\"]", line):
                out.append((m.group(1), i))
    except Exception:
        pass
    return out


def api_settings():
    schema_path = os.path.join(wishd.RIME, 'xuma_L.schema.yaml')
    default_path = os.path.join(wishd.RIME, 'build', 'default.yaml')
    sc = _yaml(schema_path)
    df = _yaml(default_path)

    # ① 開關
    switches = []
    for sw in (sc.get('switches') or []):
        if not isinstance(sw, dict):
            continue
        if 'options' in sw:
            switches.append({'group': True, 'name': '(選項組)', 'states': sw.get('states') or [],
                             'reset': sw.get('reset'), 'options': sw['options']})
        else:
            switches.append({'group': False, 'name': sw.get('name', ''),
                             'states': sw.get('states') or [], 'reset': sw.get('reset'),
                             'options': [sw.get('name', '')]})

    # ② 快捷鍵（兩處來源合併）
    keys = []

    def kb(bindings, src):
        for b in (bindings or []):
            if not isinstance(b, dict):
                continue
            act = b.get('toggle') or b.get('send') or b.get('select') or b.get('command')
            if b.get('accept'):
                keys.append({'key': b['accept'], 'act': str(act or ''),
                             'when': b.get('when', ''), 'src': src})
    kb((sc.get('key_bindings') or {}).get('__append'), 'schema')
    kb((sc.get('key_bindings') or {}).get('__include') if False else None, 'schema')
    kb((df.get('key_binder') or {}).get('bindings'), 'default')

    # 拼寫插件的 switch_key / cycle_key
    for k, v in (sc.get('xuma_spelling') or {}).items():
        if k in ('switch_key', 'cycle_key') and isinstance(v, str):
            keys.append({'key': v, 'act': k, 'when': '非簡模式組內', 'src': 'xuma_spelling'})
    xsp = sc.get('xuma_spelling')
    if isinstance(xsp, dict):
        for k in ('switch_key', 'cycle_key'):
            if isinstance(xsp.get(k), str):
                pass

    # ③ 引擎鏈
    eng = sc.get('engine') or {}
    chain = []
    for stage in ('processors', 'segmentors', 'translators', 'filters', 'punctuators'):
        items = eng.get(stage) or []
        for it in items:
            if not isinstance(it, str):
                continue
            m = re.search(r'lua_(translator|filter|processor|segmentor)@\*?([^@]+)', it)
            chain.append({'stage': stage, 'item': it,
                          'lua': m.group(2).split('*')[0].strip() if m else None})

    # ④ 引用校驗：配置 → lua 檔
    refs = []
    _walk_lua_refs(eng, refs)
    seen = set()
    ref_status = []
    LUADIR = os.path.join(wishd.RIME, 'lua')
    for kind, mod in refs:
        if (kind, mod) in seen:
            continue
        seen.add((kind, mod))
        rel = mod if mod.endswith('.lua') else mod + '.lua'
        p = os.path.join(LUADIR, rel)
        ref_status.append({'kind': kind, 'module': mod, 'file': rel,
                           'exists': os.path.exists(p)})

    # ⑤ lua 檔內部 require 的模組是否都在
    req_status = []
    for k, mod in sorted(seen):
        rel = mod if mod.endswith('.lua') else mod + '.lua'
        p = os.path.join(LUADIR, rel)
        if not os.path.exists(p):
            continue
        for dep, ln in _lua_requires(p):
            dp = os.path.join(LUADIR, dep + '.lua')
            if not os.path.exists(dp):
                dp2 = os.path.join(LUADIR, dep, 'init.lua')
                if os.path.exists(dp2):
                    continue
                req_status.append({'from': rel, 'need': dep, 'line': ln})

    # ⑥ 部署狀態：詞典檔案 vs 編譯產物
    dict_p = os.path.join(wishd.RIME, 'xuma_L.dict.yaml')
    bins = [os.path.join(wishd.RIME, 'build', f) for f in
            ('xuma_L.table.bin', 'xuma_L.prism.bin', 'xuma_L.reverse.bin')]
    bins = [b for b in bins if os.path.exists(b)]
    deploy = {'dict_mtime': os.path.getmtime(dict_p) if os.path.exists(dict_p) else 0,
              'bin_mtime': max([os.path.getmtime(b) for b in bins] or [0]),
              'bins': len(bins)}
    deploy['stale'] = bool(deploy['dict_mtime'] and deploy['bin_mtime']
                           and deploy['dict_mtime'] > deploy['bin_mtime'] + 1)

    return {'switches': switches, 'keys': keys, 'chain': chain,
            'refs': ref_status, 'requires': req_status, 'deploy': deploy,
            'yaml_error': sc.get('__error__') or df.get('__error__')}


# ── 任務中心：把重要腳本接成界面操作 ────────────────────────────
# level: safe＝只讀，直接跑；warn＝會改檔案，需確認；danger＝會部署/對外，需手打任務名
PY, BASH = sys.executable, 'bash'
TASKS = {
    # 檢查（只讀）
    'check': ('一致性電池（15 項）', '檢查', 'safe',
              [PY, 'check_consistency.py'], 600),
    'acceptance': ('44 碼首選回歸', '檢查', 'safe', [PY, 'check_acceptance.py'], 300),
    'tendency': ('字形傾向', '檢查', 'safe', [PY, 'check_tendency.py'], 300),
    'verify_nsd': ('攔截集零漂移', '檢查', 'safe',
                   [PY, 'gen_no_simple_data.py', '--verify'], 300),
    'provenance': ('溯源對賬', '檢查', 'safe', [PY, '_provenance.py', '--verify'], 300),
    'ledger': ('台賬衛生', '檢查', 'safe', [PY, '_ledger.py', '--selfcheck'], 120),
    'privacy': ('隱私門禁', '檢查', 'safe', [BASH, 'privacy-gate.sh'], 300),
    'predelete': ('刪除前清點', '檢查', 'safe', [BASH, 'pre-delete-check.sh'], 300),
    'mirror_status': ('鏡像狀態', '檢查', 'safe', [BASH, 'backup.sh', 'mirror-status'], 120),
    'backup_verify': ('校驗最新快照', '檢查', 'safe', [BASH, 'backup.sh', 'verify'], 300),
    # 生成（改檔案，不部署）
    'pipeline': ('重跑詞頻管道', '生成', 'warn', [PY, 'gen_freq_dict.py'], 900),
    'nsd': ('重生成攔截集', '生成', 'warn', [PY, 'gen_no_simple_data.py'], 300),
    'tone': ('重生成拼音調', '生成', 'warn', [PY, 'gen_pinyin_tone.py'], 300),
    # 工作流
    'transcribe': ('待審批注轉抄', '工作流', 'warn', [PY, 'transcribe_review.py'], 300),
    'handpick': ('spool 入庫（管道＋閘門＋部署）', '工作流', 'danger',
                 [BASH, 'handpick_commit.sh'], 900),
    # 備份
    'backup': ('生成快照', '備份', 'warn', [BASH, 'backup.sh', 'create'], 900),
    'drill': ('災難演練（造→還原→門禁）', '備份', 'warn', [BASH, 'backup.sh', 'drill'], 1800),
    'offsite': ('投遞異地', '備份', 'danger', [BASH, 'backup.sh', 'offsite', '--fresh'], 1800),
    'mirror_sync': ('鏡像同步', '備份', 'warn', [BASH, 'backup.sh', 'mirror-sync'], 300),
    # 一鍵
    'checkall': ('一鍵全檢（電池＋雙檢＋溯源＋台賬＋隱私）', '檢查', 'safe', None, 1800),
}
CHECKALL = ['check_consistency.py', 'check_acceptance.py', 'check_tendency.py']


def api_tasks():
    return {'tasks': [{'key': k, 'name': v[0], 'group': v[1], 'level': v[2],
                       'timeout': v[4]} for k, v in TASKS.items()]}


def run_task(key, extra=None):
    """在背景執行白名單任務；輸出寫進 JOB。"""
    if key not in TASKS:
        return False, '未知任務'
    name, group, level, argv, timeout = TASKS[key]
    if argv is None:                                   # 一鍵全檢
        argv = None
    def work():
        t0 = time.time()
        with JOB_LOCK:
            JOB.update(state='running', log='', started=t0, mode=name,
                       elapsed=None)
        def log(s):
            with JOB_LOCK:
                JOB['log'] += str(s) + '\n'
        try:
            if key == 'checkall':
                rc = 0
                for script in ('check_consistency.py', 'check_acceptance.py',
                               'check_tendency.py', '_provenance.py',
                               '_ledger.py'):
                    args = [PY, script] + (['--verify'] if script == '_provenance.py'
                                           else ['--selfcheck'] if script == '_ledger.py' else [])
                    log('── ' + script + ' ──')
                    r = subprocess.run(args, cwd=HERE, capture_output=True, text=True,
                                       timeout=600)
                    body = (r.stdout or '') + (r.stderr or '')
                    log('\n'.join(body.splitlines()[-14:]))
                    log(f'   exit={r.returncode}')
                    rc = rc or r.returncode
            else:
                cmd = list(argv)
                if key == 'diff':
                    pass
                r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                                   timeout=timeout)
                body = (r.stdout or '') + (r.stderr or '')
                log(body[-8000:] or '(無輸出)')
                rc = r.returncode
            with JOB_LOCK:
                JOB['state'] = 'done' if rc == 0 else 'failed'
                JOB['elapsed'] = round(time.time() - t0, 1)
                JOB['rc'] = rc
        except subprocess.TimeoutExpired:
            with JOB_LOCK:
                JOB['state'] = 'failed'
                JOB['log'] += f'\n✗ 逾時（{timeout}s）'
        except Exception as e:
            with JOB_LOCK:
                JOB['state'] = 'failed'
                JOB['log'] += f'\n✗ {e}'
    threading.Thread(target=work, daemon=True).start()
    return True, ''


class Handler(BaseHTTPRequestHandler):
    server_version = 'wishd/1.1'

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ('/', '/index.html'):
            return self._send(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8')
        if u.path == '/api/group':
            return self._send(200, json.dumps(api_group(q.get('code', [''])[0]), ensure_ascii=False))
        if u.path == '/api/search':
            return self._send(200, json.dumps(api_search(q.get('q', [''])[0]), ensure_ascii=False))
        if u.path == '/api/recent':
            return self._send(200, json.dumps(api_recent(), ensure_ascii=False))
        if u.path == '/api/job':
            with JOB_LOCK:
                return self._send(200, json.dumps(dict(JOB), ensure_ascii=False))
        if u.path == '/api/dict':
            return self._send(200, json.dumps(api_dict(q), ensure_ascii=False))
        if u.path == '/api/map':
            return self._send(200, json.dumps(api_map(q), ensure_ascii=False))
        if u.path == '/api/tasks':
            return self._send(200, json.dumps(api_tasks(), ensure_ascii=False))
        if u.path == '/api/settings':
            return self._send(200, json.dumps(api_settings(), ensure_ascii=False))
        if u.path == '/api/logs':
            return self._send(200, json.dumps(api_log_list(), ensure_ascii=False))
        if u.path == '/api/log':
            n = int(q.get('lines', ['300'])[0] or 300)
            return self._send(200, json.dumps(
                api_log(q.get('key', ['events'])[0], n,
                        q.get('q', [''])[0],
                        q.get('errors', ['0'])[0] == '1',
                        q.get('action', [''])[0]), ensure_ascii=False))
        if u.path == '/api/overrides':
            return self._send(200, json.dumps(wishd.list_overrides(), ensure_ascii=False))
        if u.path == '/api/override-codes':
            return self._send(200, json.dumps(api_override_codes(), ensure_ascii=False))
        if u.path == '/api/learn':
            return self._send(200, json.dumps(api_learn(), ensure_ascii=False))
        if u.path == '/api/diag':
            return self._send(200, json.dumps(api_diag(), ensure_ascii=False))
        if u.path == '/api/dict/stats':
            return self._send(200, json.dumps(api_dict_stats(), ensure_ascii=False))
        if u.path == '/api/pending':
            return self._send(200, json.dumps(wishd.pending(), ensure_ascii=False))
        return self._send(404, json.dumps({'error': 'not found'}))

    def do_POST(self):
        if self.headers.get('X-Wishd') != '1':
            return self._send(403, json.dumps({'error': 'missing X-Wishd header'}))
        n = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b'{}')
        except json.JSONDecodeError:
            return self._send(400, json.dumps({'error': 'bad json'}))
        try:
            return self._dispatch(payload)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send(500, json.dumps({'error': str(e)}, ensure_ascii=False))

    def _dispatch(self, payload):
        if self.path == '/api/order':
            return self._send(200, json.dumps(api_order(payload), ensure_ascii=False))
        if self.path == '/api/commit':
            return self._send(200, json.dumps(api_commit(payload), ensure_ascii=False))
        if self.path == '/api/drop':
            return self._send(200, json.dumps(api_drop(payload), ensure_ascii=False))
        if self.path == '/api/add':
            return self._send(200, json.dumps(wishd.add_words(payload.get('words') or []),
                                              ensure_ascii=False))
        if self.path == '/api/run':
            key = payload.get('key') or ''
            lvl = TASKS.get(key, (None, None, 'safe'))[2]
            if lvl == 'danger' and (payload.get('confirm') or '') != 'APPLY':
                return self._send(400, json.dumps({'ok': False, 'error': '危險操作需明文輸入 APPLY'}))
            with JOB_LOCK:
                if JOB['state'] == 'running':
                    return self._send(409, json.dumps({'ok': False, 'error': '已有任務在跑'}))
            ok, why = run_task(key)
            return self._send(200 if ok else 400,
                              json.dumps({'ok': ok, 'error': why}, ensure_ascii=False))
        if self.path == '/api/apply-token':
            tok, p = issue_token()
            return self._send(200, json.dumps({'token': tok, 'pending': p, 'ttl': TOKEN_TTL},
                                              ensure_ascii=False))
        if self.path == '/api/apply':
            if (payload.get('confirm') or '') != 'APPLY':
                return self._send(400, json.dumps({'ok': False, 'error': '需明文輸入 APPLY'}))
            p = wishd.pending()
            ok, why = check_token(payload.get('token') or '', p['fingerprint'])
            if not ok:
                return self._send(409, json.dumps({'ok': False, 'error': why}, ensure_ascii=False))
            with JOB_LOCK:
                if JOB['state'] == 'running':
                    return self._send(409, json.dumps({'ok': False, 'error': '已有任務在跑'},
                                                      ensure_ascii=False))
            run_apply(no_deploy=bool(payload.get('no_deploy')))
            return self._send(200, json.dumps({'ok': True}))
        return self._send(404, json.dumps({'error': 'not found'}))


PAGE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>詞庫意願台</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%232563eb'/%3E%3Ctext x='16' y='23' font-size='19' text-anchor='middle' fill='white' font-family='sans-serif'%3E%E8%A9%9E%3C/text%3E%3C/svg%3E">
<style>
:root{
  --bg:#f6f8fb; --surface:#ffffff; --surface-2:#f8fafc; --border:#e3e8ef; --border-strong:#cbd5e1;
  --fg:#0f172a; --fg-muted:#64748b; --fg-faint:#94a3b8;
  --primary:#2563eb; --primary-fg:#ffffff; --primary-soft:#eff6ff;
  --danger:#dc2626; --danger-fg:#ffffff; --danger-soft:#fef2f2;
  --ok:#15803d; --ok-soft:#f0fdf4; --warn:#b45309; --warn-soft:#fffbeb;
  --accent:#2563eb;
  --r-sm:6px; --r:10px; --r-lg:14px;
  --sh-1:0 1px 2px rgba(15,23,42,.06); --sh-2:0 4px 12px rgba(15,23,42,.10);
  --sh-3:0 16px 48px rgba(15,23,42,.22);
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK TC","PingFang TC","Microsoft JhengHei",sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
[data-theme=dark]{
    --bg:#0b1220; --surface:#111827; --surface-2:#0f172a; --border:#1f2937; --border-strong:#334155;
    --fg:#e5e7eb; --fg-muted:#94a3b8; --fg-faint:#64748b;
    --primary:#60a5fa; --primary-fg:#0b1220; --primary-soft:#172554;
    --danger:#f87171; --danger-fg:#0b1220; --danger-soft:#450a0a;
    --ok:#4ade80; --ok-soft:#052e16; --warn:#fbbf24; --warn-soft:#451a03;
    --accent:#60a5fa;
    --sh-1:0 1px 2px rgba(0,0,0,.4); --sh-2:0 4px 14px rgba(0,0,0,.5); --sh-3:0 18px 50px rgba(0,0,0,.65);
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 var(--font);
     -webkit-font-smoothing:antialiased}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
header{position:sticky;top:0;z-index:30;display:flex;gap:12px;align-items:center;
  padding:12px 20px;background:color-mix(in srgb,var(--surface) 88%,transparent);
  backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
.brand{display:flex;align-items:center;gap:9px;font-weight:650;letter-spacing:.01em}
.dot{width:9px;height:9px;border-radius:50%;background:var(--primary);box-shadow:0 0 0 4px var(--primary-soft)}
.search{position:relative;flex:0 0 auto}
.search input{width:300px;padding:8px 12px 8px 32px}
.search::before{content:"⌕";position:absolute;left:11px;top:7px;color:var(--fg-faint);font-size:15px}
.hint{margin-left:auto;color:var(--fg-faint);font-size:12.5px}
nav{display:flex;gap:2px;padding:0 20px;background:var(--surface);border-bottom:1px solid var(--border)}
nav button{border:0;background:none;padding:11px 16px;font:inherit;font-weight:500;color:var(--fg-muted);
  cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
nav button:hover{color:var(--fg)}
nav button[aria-selected=true]{color:var(--primary);border-bottom-color:var(--primary)}
main{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:16px;padding:16px 20px 28px;align-items:start}
main.dictmode{grid-template-columns:minmax(0,1fr)}
section[hidden],aside[hidden]{display:none!important}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r);
  box-shadow:var(--sh-1);padding:16px;margin-bottom:16px}
.card h2{margin:0 0 2px;font-size:13px;font-weight:650;letter-spacing:.02em;text-transform:none}
.card .sub{margin:0 0 12px;color:var(--fg-faint);font-size:12.5px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input,select,textarea{font:inherit;color:var(--fg);background:var(--surface);border:1px solid var(--border-strong);
  border-radius:var(--r-sm);padding:7px 10px}
input:hover,select:hover,textarea:hover{border-color:var(--fg-faint)}
textarea{width:100%;resize:vertical;min-height:56px}
button{font:inherit;font-weight:500;border:1px solid var(--border-strong);background:var(--surface);
  color:var(--fg);border-radius:var(--r-sm);padding:7px 13px;cursor:pointer;transition:.12s}
button:hover:not(:disabled){background:var(--surface-2);border-color:var(--fg-faint)}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--primary);color:var(--primary-fg);border-color:var(--primary)}
button.primary:hover:not(:disabled){filter:brightness(1.08)}
button.danger{background:var(--danger);color:var(--danger-fg);border-color:var(--danger)}
button.danger:hover:not(:disabled){filter:brightness(1.08)}
button.ghost{border-color:transparent;background:none;color:var(--fg-muted);padding:4px 8px}
button.iconbtn{border:1px solid transparent;background:none;color:var(--fg-muted);padding:6px 9px;
  font-size:15px;line-height:1;border-radius:var(--r-sm);cursor:pointer;
  display:inline-flex;align-items:center;justify-content:center}
button.iconbtn:hover{background:var(--surface-2);color:var(--fg);border-color:var(--border)}
button.iconbtn.on{background:var(--primary-soft);color:var(--primary);border-color:var(--primary)}
button.sm{padding:3px 9px;font-size:12.5px}
table{width:100%;border-collapse:separate;border-spacing:0}
th{position:sticky;top:0;z-index:1;background:var(--surface);color:var(--fg-muted);
  font-size:12px;font-weight:600;letter-spacing:.03em;text-align:left;padding:8px 10px;
  border-bottom:1px solid var(--border)}
td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tbody tr:hover{background:var(--surface-2)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;color:var(--fg-muted)}
.scroll{max-height:66vh;overflow:auto;border:1px solid var(--border);border-radius:var(--r-sm);background:var(--surface)}
table.plain th{background:var(--surface)}
.vlist{border:1px solid var(--border);border-radius:var(--r-sm);overflow:hidden;background:var(--surface)}
.vrow{position:relative;display:grid;
  /* grip | 序 | 詞 | 條 | 頻分 | 占比 | 行 | 徽章 */
  grid-template-columns:22px 20px minmax(70px,132px) minmax(70px,1fr) 82px 54px 58px 92px;
  align-items:center;gap:10px;padding:7px 12px;border-bottom:1px solid var(--border);
  background:var(--surface);transition:background .12s}
.vrow:last-child{border-bottom:0}
.vrow:hover{background:var(--surface-2)}
.vrow.dragging{opacity:.45}
.vrow.drop-top{box-shadow:inset 0 2px 0 0 var(--accent)}
.vrow.drop-bottom{box-shadow:inset 0 -2px 0 0 var(--accent)}
.vrow .rank{font-variant-numeric:tabular-nums;color:var(--fg-faint);font-size:12.5px;text-align:right}
.vrow .word{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vrow .track{position:relative;height:16px;border-radius:4px;background:var(--surface-2);
  border:1px solid var(--border);overflow:hidden}
.vrow .bar{height:100%;border-radius:3px 0 0 3px;
  background:linear-gradient(90deg,var(--primary),color-mix(in srgb,var(--primary) 60%,transparent));
  min-width:2px;transition:width .25s ease}
.vrow .bar.zero{background:var(--border-strong)}
.vrow .wt{font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--fg);text-align:right}
.vrow .pct{font-variant-numeric:tabular-nums;font-size:11.5px;color:var(--fg-faint);text-align:right}
.vrow .ln{font-variant-numeric:tabular-nums;font-size:11.5px;color:var(--fg-faint);text-align:right}
.ovrow{display:flex;gap:10px;align-items:center;padding:7px 8px;border-radius:6px;font-size:13.5px}
.ovrow:hover{background:var(--surface-2)}
.vrow .grip{color:var(--fg-faint);cursor:grab;user-select:none;text-align:center;line-height:1}
.vrow.locked{opacity:.72;background:var(--surface-2)}
.vrow.locked .grip{cursor:not-allowed;opacity:.35}
.vrow.onechar .word::after{content:"單";font-size:10px;margin-left:6px;padding:0 4px;border-radius:3px;
  background:var(--border);color:var(--fg-muted);vertical-align:2px}
.vrow .flags{display:flex;gap:4px;justify-content:flex-end;flex-wrap:nowrap;overflow:hidden}
.badge{white-space:nowrap}
.handle{color:var(--fg-faint);cursor:grab;user-select:none}
tr.dragging{opacity:.4}
tr.drop-top{box-shadow:inset 0 2px 0 0 var(--accent)}
tr.drop-bottom{box-shadow:inset 0 -2px 0 0 var(--accent)}
tr.movable:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
code,.mono{font-family:var(--mono);font-size:12.5px}
.chip{display:inline-block;font-family:var(--mono);font-size:12px;padding:1px 7px;border-radius:999px;
  border:1px solid var(--border-strong);background:var(--surface-2);color:var(--fg-muted)}
.chip.full{border-color:var(--primary);color:var(--primary);background:var(--primary-soft);font-weight:600}
.badge{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:999px;
  background:var(--surface-2);color:var(--fg-muted);border:1px solid var(--border)}
.badge.hp{background:var(--ok-soft);color:var(--ok);border-color:transparent}
.badge.drop{background:var(--danger-soft);color:var(--danger);border-color:transparent}
.pill{display:inline-flex;gap:6px;align-items:center;background:var(--surface-2);border:1px solid var(--border);
  border-radius:999px;padding:2px 10px;font-size:13px}
.empty{color:var(--fg-faint);text-align:center;padding:28px 10px;font-size:13px}
.logwrap{display:grid;grid-template-columns:220px minmax(0,1fr);gap:14px}
.loglist{border:1px solid var(--border);border-radius:var(--r-sm);overflow:hidden}
.loglist .src{padding:8px 11px;border-bottom:1px solid var(--border);cursor:pointer;font-size:13px}
.loglist .src:last-child{border-bottom:0}
.loglist .src:hover{background:var(--surface-2)}
.loglist .src.on{background:var(--primary-soft);color:var(--primary);font-weight:600}
.loglist .src.off{color:var(--fg-faint);cursor:default}
.loglist .src .m{display:block;font-size:11px;color:var(--fg-faint);font-weight:400}
.map{display:grid;grid-template-columns:repeat(auto-fill,minmax(78px,1fr));gap:8px}
.cell{position:relative;aspect-ratio:1/1;border-radius:var(--r-sm);border:1px solid var(--border);
  display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;
  background:var(--surface);transition:transform .1s,box-shadow .1s;user-select:none}
.cell:hover{transform:translateY(-1px);box-shadow:var(--sh-2);border-color:var(--border-strong)}
.cell .L{font-family:var(--mono);font-size:19px;font-weight:700;line-height:1}
.cell .N{font-size:11px;color:var(--fg-muted);font-variant-numeric:tabular-nums;margin-top:3px}
.cell.dark .L,.cell.dark .N{color:#fff}
.cell.dark .N{opacity:.85}
.cell.exact::after{content:"";position:absolute;top:6px;right:6px;width:5px;height:5px;border-radius:50%;
  background:var(--ok)}
.cell.hasov::before{content:"";position:absolute;top:6px;left:6px;width:6px;height:6px;border-radius:50%;
  background:var(--warn);box-shadow:0 0 0 2px color-mix(in srgb,var(--warn) 30%,transparent)}
.badge.ov{background:var(--warn-soft);color:var(--warn);border-color:transparent}
.cell.empty{background:var(--surface-2);border-style:dashed;cursor:default;opacity:.5}
.cell.empty:hover{transform:none;box-shadow:none}
.crumbs{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:12px;font-size:13px}
.crumbs a{color:var(--primary);cursor:pointer;text-decoration:none;font-family:var(--mono)}
.crumbs a:hover{text-decoration:underline}
.diag-tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.diag-tabs button.on{background:var(--primary);color:var(--primary-fg);border-color:var(--primary)}
.stats{display:flex;gap:16px;flex-wrap:wrap;color:var(--fg-muted);font-size:12.5px}
.stats b{color:var(--fg);font-variant-numeric:tabular-nums}
pre.log{background:#0b1220;color:#a7f3d0;padding:12px;border-radius:var(--r-sm);overflow:auto;
  max-height:240px;font-family:var(--mono);font-size:12px;margin:0;line-height:1.5}
#recent>div{padding:6px 0;border-bottom:1px dashed var(--border);font-size:12.5px}
#recent>div:last-child{border-bottom:0}
dialog{border:0;padding:0;border-radius:var(--r-lg);width:min(560px,92vw);
  box-shadow:var(--sh-3);background:var(--surface);color:var(--fg)}
dialog::backdrop{background:rgba(2,6,23,.55);backdrop-filter:blur(2px)}
.dlg-h{display:flex;gap:10px;align-items:center;padding:16px 18px 12px;font-weight:650}
.dlg-h .ico{width:26px;height:26px;border-radius:50%;background:var(--danger-soft);color:var(--danger);
  display:grid;place-items:center;font-size:15px}
.dlg-b{padding:0 18px 8px;color:var(--fg-muted);font-size:13.5px}
.dlg-b ul{margin:8px 0 12px;padding-left:18px}
.dlg-b li{margin:3px 0}
.dlg-f{display:flex;gap:8px;justify-content:flex-end;padding:12px 18px 16px}
#dlginput{margin-top:6px;font-family:var(--mono);letter-spacing:.12em;text-transform:uppercase}
#toasts{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);display:flex;flex-direction:column;
  gap:8px;z-index:60;pointer-events:none}
.toast{pointer-events:auto;min-width:220px;max-width:min(560px,90vw);padding:10px 14px;border-radius:var(--r);
  background:var(--surface);border:1px solid var(--border);box-shadow:var(--sh-2);font-size:13px;
  display:flex;gap:9px;align-items:flex-start;animation:pop .16s ease-out}
@keyframes pop{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.menu{position:fixed;z-index:70;min-width:150px;background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-sm);box-shadow:var(--sh-2);padding:4px;animation:pop .12s ease-out}
.menu button{display:block;width:100%;text-align:left;border:0;background:none;padding:8px 10px;
  border-radius:5px;font-weight:500;color:var(--fg)}
.menu button:hover{background:var(--surface-2)}
.menu button.danger-item{color:var(--danger)}
.menu button.danger-item:hover{background:var(--danger-soft)}
.menu .sep{height:1px;background:var(--border);margin:4px 2px}
td.right{text-align:right}
.toast.ok{border-left:3px solid var(--ok)} .toast.err{border-left:3px solid var(--danger)}
.toast.warn{border-left:3px solid var(--warn)} .toast.info{border-left:3px solid var(--primary)}
.skel{height:12px;border-radius:6px;background:linear-gradient(90deg,var(--surface-2),var(--border),var(--surface-2));
  background-size:200% 100%;animation:sk 1.2s infinite}
@keyframes sk{from{background-position:200% 0}to{background-position:-200% 0}}
kbd{font-family:var(--mono);font-size:11px;border:1px solid var(--border-strong);border-bottom-width:2px;
  border-radius:4px;padding:0 4px;background:var(--surface-2);color:var(--fg-muted)}
@media(max-width:980px){main{grid-template-columns:1fr}.hint{display:none}.search input{width:200px}}
</style></head><body>

<header>
  <button id="back" class="iconbtn" title="返回上一頁（Alt+←）" aria-label="返回">←</button>
  <div class="brand"><span class="dot"></span>詞庫意願台</div>
  <div class="search"><input id="q" placeholder="碼（oooo）或詞（合適）" aria-label="查詢"></div>
  <button id="go" class="primary">查詢</button>
  <span class="hint"><kbd>/</kbd> 聚焦搜尋</span>
  <button id="tab-set" class="iconbtn" title="設置：開關 / 快捷鍵 / 引擎鏈 / 引用校驗" aria-label="設置"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></button>
  <button id="theme" class="iconbtn" title="切換深/淺色" aria-label="切換深淺色">◐</button>
</header>

<nav role="tablist">
  <button id="tab-order" role="tab" aria-selected="true">調序 / 加詞</button>
  <button id="tab-dict" role="tab" aria-selected="false">字典</button>
  <button id="tab-map" role="tab" aria-selected="false">碼圖</button>
  <button id="tab-diag" role="tab" aria-selected="false">診斷</button>
  <button id="tab-task" role="tab" aria-selected="false">任務</button>
  <button id="tab-logs" role="tab" aria-selected="false">日誌</button>
</nav>

<main id="main">
 <section id="pane-order" role="tabpanel">
   <div class="card">
     <h2 id="gtitle">同碼組</h2>
     <p class="sub" id="gsub">輸入碼或詞開始。拖拽 <span class="handle">⠿</span> 或用 <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> 調整次序。</p>
     <div id="gstats" class="stats" style="margin-bottom:10px"></div>
     <div id="rows" class="vlist"><div class="empty">尚未載入</div></div>
     <div class="row" style="margin-top:12px">
       <button id="prev">預覽變更</button>
       <button id="save" class="primary">寫入裁定</button>
     </div>
     <div id="ovs" style="margin-top:14px"></div>
   </div>

   <div class="card">
     <h2>已調序的碼 <span class="muted" id="ovcount" style="font-weight:400"></span></h2>
     <p class="sub">每次寫入裁定都會落一行，這裡是全部記錄（含無效的）。點一行可載入該碼組。</p>
     <div id="ovlist" class="scroll" style="max-height:40vh"><div class="empty">載入中…</div></div>
   </div>

   <div class="card">
     <h2>加詞</h2>
     <p class="sub">與輸入法裏的 <code>/add</code> 共用同一個暫存，攢批後一起入庫。</p>
     <textarea id="newword" placeholder="2–4 個漢字，多個用空格或換行分隔"></textarea>
     <div class="row" style="margin-top:10px">
       <button id="addbtn" class="primary">加入暫存</button>
     </div>
   </div>
 </section>

 <section id="pane-map" role="tabpanel" hidden>
   <div class="card">
     <h2>同碼詞地圖</h2>
     <p class="sub">每個格子是一個碼前綴，<b>顏色越深＝該分支下同碼詞越多</b>。點格子往下鑽，到第 4 層就是一個完整碼組。</p>
     <div class="crumbs" id="crumbs"></div>
     <div class="stats" id="mapstats" style="margin-bottom:12px"></div>
     <div class="map" id="mapgrid"></div>
     <div class="row" style="margin-top:14px" id="mapexact"></div>
   </div>
 </section>

 <section id="pane-diag" role="tabpanel" hidden>
   <div class="card">
     <h2>詞源排序衝突</h2>
     <p class="sub">同一個四碼組裏，十個語源對「誰排前面」意見不一致的配對。衝突越多越靠前——混合分會把爭執平均掉，這些碼位最該由人拍板。</p>
     <div class="diag-tabs" id="diagtabs" hidden></div>
     <div class="stats" id="diagdesc" style="margin-bottom:10px"></div>
     <div class="scroll" style="max-height:60vh">
       <table><thead><tr><th style="width:34%">詞／碼</th><th>細節</th><th style="width:90px"></th></tr></thead>
         <tbody id="diagrows"><tr><td colspan="3"><div class="empty">載入中…</div></td></tr></tbody></table>
     </div>
   </div>
   <div class="card">
     <h2>權重學習（原型）</h2>
     <p class="sub">把「人工調序」當成成對偏好標籤，反推十個語源權重。只讀，不改任何檔案。</p>
     <div class="row"><button id="learnbtn">跑一次擬合</button>
       <span class="muted" style="font-size:12.5px">約 2 秒</span></div>
     <div id="learnout" style="margin-top:12px"></div>
   </div>
 </section>

 <section id="pane-logs" role="tabpanel" hidden>
   <div class="card">
     <h2>日誌中心</h2>
     <p class="sub">所有落盤的記錄。灰掉的表示該檔案目前不存在（多為本輪尚未觸發）。</p>
     <div class="logwrap">
       <div class="loglist" id="loglist"><div class="skel" style="width:80%"></div></div>
       <div>
         <div class="row" style="margin-bottom:10px">
           <input id="logq" placeholder="搜尋…" style="width:200px">
           <select id="loglines"><option value="200">末 200 行</option>
             <option value="500" selected>末 500 行</option>
             <option value="2000">末 2000 行</option></select>
           <label class="row" style="gap:5px;color:var(--fg-muted);font-size:13px">
             <input type="checkbox" id="logerr">只看錯誤</label>
           <select id="logact" style="display:none"></select>
           <button id="logreload" class="sm">重新載入</button>
           <label class="row" style="gap:5px;color:var(--fg-muted);font-size:13px">
             <input type="checkbox" id="logauto">每 3 秒</label>
           <span id="logmeta" class="muted" style="font-size:12.5px"></span>
         </div>
         <pre class="log" id="logout" style="max-height:64vh">—</pre>
       </div>
     </div>
   </div>
 </section>

 <section id="pane-task" role="tabpanel" hidden>
   <div class="card">
     <h2>任務中心</h2>
     <p class="sub">把倉庫的重要腳本接成按鈕。<b>檢查</b>類只讀、直接跑；<b>生成</b>類會改檔案、需確認；
       <b>危險</b>類會部署或對外投遞、需手打 <code>APPLY</code>。所有任務在背景執行，輸出即時回顯。</p>
     <div id="taskgroups"></div>
   </div>
   <div class="card">
     <h2>輸出 <span id="taskname" class="muted" style="font-weight:400;font-size:12.5px"></span></h2>
     <pre class="log" id="taskout" style="max-height:52vh">—</pre>
   </div>
 </section>

 <section id="pane-set" role="tabpanel" hidden>
   <div id="setalert"></div>
   <div class="card">
     <h2>開關</h2>
     <p class="sub">來自 <code>xuma_L.schema.yaml</code> 的 <code>switches</code>。「預設」是每次進入方案時的初值。</p>
     <table><thead><tr><th style="width:22%">名稱</th><th>狀態</th><th style="width:14%">預設</th></tr></thead>
       <tbody id="set-switches"></tbody></table>
   </div>
   <div class="card">
     <h2>快捷鍵</h2>
     <p class="sub">合併 schema 的 <code>key_bindings</code>、<code>build/default.yaml</code> 的 <code>key_binder</code>、與拼寫插件的 switch/cycle 鍵。</p>
     <div class="row" style="margin-bottom:10px">
       <select id="keysrc"><option value="own">只看方案自訂</option>
         <option value="all">全部（含 Rime 內建導航鍵）</option></select>
       <span id="keymeta" class="muted" style="font-size:12.5px"></span>
     </div>
     <table><thead><tr><th style="width:20%">鍵</th><th style="width:26%">動作</th>
       <th style="width:22%">條件</th><th>來源</th></tr></thead><tbody id="set-keys"></tbody></table>
   </div>
   <div class="card">
     <h2>引擎鏈</h2>
     <p class="sub">候選從產生到輸出的順序。<span class="badge" style="color:var(--ok)">lua</span> 標記的條目會檢查對應檔案是否存在。</p>
     <div id="set-chain"></div>
   </div>
   <div class="card">
     <h2>引用校驗</h2>
     <div id="set-refs"></div>
   </div>
 </section>

 <section id="pane-dict" role="tabpanel" hidden>
   <div class="card">
     <h2>字典</h2>
     <p class="sub">同一詞的簡碼與全碼併為一行：<b>全碼實心</b>、簡碼灰底。取消「合併多碼詞」可看逐條目（含行號）。</p>
     <div class="row" style="margin-bottom:10px">
       <input id="dw" placeholder="詞含…" style="width:150px">
       <input id="dc" placeholder="碼前綴…" style="width:110px">
       <select id="dcl" aria-label="碼長">
         <option value="">全部碼</option><option value="short">簡碼 1–3</option>
         <option value="full">全碼 4</option><option value="1">一碼</option>
         <option value="2">二簡</option><option value="3">三碼</option></select>
       <select id="dl" aria-label="字數">
         <option value="">字數不限</option><option value="1">單字</option><option value="2">二字</option>
         <option value="3">三字</option><option value="4">四字</option><option value="other">五字以上</option></select>
       <select id="ds" aria-label="來源">
         <option value="">來源不限</option><option value="hp">手收</option><option value="tag">有標記</option>
         <option value="drop">已標記棄用</option><option value="ov">有裁定的詞</option>
         <option value="plain">無標記</option></select>
       <select id="dso" aria-label="排序">
         <option value="line">按行號</option><option value="code">按編碼</option>
         <option value="weight">按頻分</option><option value="word">按詞形</option></select>
       <label class="row" style="gap:5px;color:var(--fg-muted);font-size:13px">
         <input type="checkbox" id="dgrp" checked> 合併多碼詞</label>
       <button id="dgo" class="primary">查詢</button>
     </div>
     <div class="stats" id="dstats" style="margin-bottom:10px"></div>
     <div class="scroll">
       <table><thead><tr>
         <th style="width:24%">詞</th><th style="width:26%">編碼</th><th class="num">頻分</th>
         <th style="width:7%">字數</th><th style="width:16%">標記</th>
         <th class="num" style="width:8%">行</th><th style="width:76px"></th></tr></thead>
         <tbody id="drows"><tr><td colspan="7"><div class="empty">按「查詢」載入</div></td></tr></tbody></table>
     </div>
     <div class="row" style="margin-top:10px">
       <button id="dfirst" class="sm">« 首頁</button><button id="dprev" class="sm">‹ 上頁</button>
       <span id="dpage" class="num" style="min-width:180px;text-align:center"></span>
       <button id="dnext" class="sm">下頁 ›</button><button id="dlast" class="sm">末頁 »</button>
       <span style="color:var(--fg-faint);font-size:12.5px">點一行跳到該碼組調序</span>
     </div>
   </div>
 </section>

 <aside id="pane-side">
   <div class="card">
     <h2>待應用</h2>
     <div id="pend"><div class="skel" style="width:60%"></div></div>
     <div class="row" style="margin-top:12px">
       <button id="apply" class="danger" disabled>應用（管道＋部署）…</button>
     </div>
     <p class="sub" style="margin:10px 0 0">重跑詞頻管道 → 44 碼閘門 → 重啟引擎。</p>
   </div>
   <div class="card">
     <h2>執行日誌</h2>
     <pre class="log" id="out">—</pre>
   </div>
   <div class="card">
     <h2>最近動態 <a id="tololgs" style="float:right;font-size:12px;font-weight:400;cursor:pointer;color:var(--primary)">全部日誌 →</a></h2>
     <div id="recent"><div class="skel" style="width:70%"></div></div>
   </div>
 </aside>
</main>

<div id="toasts" aria-live="polite"></div>

<dialog id="dlg">
  <div class="dlg-h"><span class="ico">!</span>確認應用？</div>
  <div class="dlg-b">
    <div id="dlgsum"></div>
    <p>詞典會原子重寫；44 碼閘門不過則中止部署；引擎重啟期間輸入法短暫不可用。</p>
    <p style="margin-bottom:0">輸入 <b>APPLY</b> 以確認：</p>
    <input id="dlginput" placeholder="APPLY" autocomplete="off" spellcheck="false">
    <div id="dlgerr" style="color:var(--danger);margin-top:8px"></div>
  </div>
  <div class="dlg-f">
    <button id="dlgcancel">取消</button>
    <button id="dlgok" class="danger" disabled>確認應用</button>
  </div>
</dialog>

<script>
/* 主題：localStorage 記住選擇；首次跟隨系統 */
(function(){
  const saved=localStorage.getItem('wishd-theme');
  const sys=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
  document.documentElement.dataset.theme=saved||sys;
})();

let CODE='', SEQ=[], DRAG=null, TOKEN=null, DPAGE=1, DPAGES=1;
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api=(p,o)=>{o=o||{};o.headers=Object.assign({'X-Wishd':'1'},o.headers||{});
  return fetch(p,o).then(r=>r.json());};

function toast(msg,kind,ms){
  const el=document.createElement('div');
  el.className='toast '+(kind||'info');
  el.innerHTML=esc(msg);
  $('#toasts').appendChild(el);
  setTimeout(()=>{el.style.opacity='0';el.style.transform='translateY(6px)';
    el.style.transition='.2s';setTimeout(()=>el.remove(),220);}, ms||3200);
  return el;
}
function busy(on,label){
  const b=$('#go'); b.disabled=!!on; b.textContent=on?(label||'查詢中…'):'查詢';
}

/* ───── 分頁籤 ───── */
const TABS=['order','dict','map','diag','task','logs'];   // 由常用到少用；設置走齒輪
function tab(which, push){
  CURTAB=which;
  TABS.forEach(k=>{
    $('#tab-'+k).setAttribute('aria-selected', k===which);
    $('#pane-'+k).hidden = k!==which;
  });
  $('#pane-set').hidden = which!=='set';
  $('#tab-set').classList.toggle('on', which==='set');
  $('#pane-side').hidden = which!=='order';
  $('#main').classList.toggle('dictmode', which!=='order');
  if(which==='dict' && !$('#drows').dataset.loaded) dictQuery(1);
  if(which==='map' && !$('#mapgrid').dataset.loaded) mapGo('');
  if(which==='diag' && !$('#diagrows').dataset.loaded) diagLoad();
  if(which==='logs'){ if(!$('#loglist').dataset.loaded){$('#loglist').dataset.loaded='1';logList();logLoad();} }
  if(which!=='logs'){ clearInterval(LAUTO); LAUTO=null; $('#logauto').checked=false; }
  if(which==='set' && !$('#set-switches').dataset.loaded){$('#set-switches').dataset.loaded='1';setLoad();}
  if(which==='task' && !$('#taskgroups').dataset.loaded){$('#taskgroups').dataset.loaded='1';taskLoad();}
  if(push!==false){
    const h='#'+which+((which==='order'&&CODE)?'/'+CODE:(which==='map'&&MPREFIX?'/'+MPREFIX:''));
    if(location.hash!==h) history.pushState({tab:which,code:CODE},'',h);
  }
  $('#back').disabled = history.length<=1;
}

/* 前進/後退：只還原視圖；帶碼的 order、帶前綴的 map 重新載入 */
function applyHash(){
  const raw=location.hash.replace(/^#/,'')||'order';
  const parts=raw.split('/');
  const name=parts[0], arg=parts.slice(1).join('/');
  if(name==='order'){
    tab('order', false);
    if(arg){ $('#q').value=arg; load(arg); }
  } else if(name==='map'){
    tab('map', false);
    mapGo(arg||'');
  } else tab(TABS.includes(name)?name:'order', false);
}
addEventListener('popstate', applyHash);
let CURTAB='order';
$('#tab-order').onclick=()=>tab('order');
$('#tab-dict').onclick=()=>tab('dict');
$('#tab-map').onclick=()=>tab('map');
$('#tab-diag').onclick=()=>tab('diag');
$('#tab-task').onclick=()=>tab('task');
$('#tab-logs').onclick=()=>tab('logs');
$('#tab-set').onclick=()=>tab('set');

/* ───── 同碼組 ───── */
function renderGroup(d){
  CODE=d.code;
  const ws=d.members.map(m=>parseFloat(m.wt)||0);
  const maxW=Math.max(1,...ws);
  const sumW=ws.reduce((a,b)=>a+b,0);
  $('#gtitle').textContent=d.exists?`碼組 ${d.code}`:`碼組 ${d.code}（不存在）`;
  const ovWords=new Set(); (d.overrides||[]).forEach(o=>o.words.forEach(w=>ovWords.add(w)));

  if(d.members.length){
    const top=ws[0]||0, second=ws[1]||0;
    const ratio=(top>0&&second>0)?(top/second):null;
    const share=sumW>0?(top/sumW*100):0;
    $('#gstats').innerHTML=
       `<span><b>${d.members.length}</b> 詞</span>`
      +`<span>總頻分 <b>${sumW.toLocaleString()}</b></span>`
      +`<span>首位占比 <b>${share.toFixed(1)}%</b></span>`
      +(ratio?`<span>首位/次位 <b>${ratio.toFixed(ratio<10?2:0)}×</b></span>`:'')
      +(d.overrides.length?`<span style="color:var(--warn)">● 已調過（裁定 ${d.overrides.length} 條）</span>`
        :`<span style="color:var(--fg-faint)">未調過</span>`)
      +((CODE||'').length<4
         ?`<span style="color:var(--fg-faint)">簡碼組不參與調頻</span>`
         :(()=>{const n=d.members.filter(m=>[...m.w].length===1).length;
                return n?`<span style="color:var(--fg-faint)">${n} 個單字不可調</span>`:'';})());
  } else $('#gstats').innerHTML='';

  const box=$('#rows'); box.innerHTML='';
  SEQ=d.members.map(m=>m.w);
  if(!d.members.length){ box.innerHTML='<div class="empty">此碼組沒有詞</div>'; return; }

  d.members.forEach((m,idx)=>{
    const w=parseFloat(m.wt)||0;
    const pct=sumW>0?(w/sumW*100):0;
    const el=document.createElement('div');
    const onechar=[...m.w].length===1;
    const shortCode=(CODE||'').length<4;      // 一至三碼區逐字節繼承，不參與調頻
    const isSingle=shortCode||onechar;
    el.className='vrow'+(isSingle?' locked':'')+(onechar?' onechar':'');
    el.draggable=!isSingle; el.tabIndex=0; el.dataset.w=m.w;
    // 線性刻度：柱長＝該詞頻分 / 組內最大。對數會把 8 倍差距壓成 1.2 倍，失真。
    const bw=w?Math.max(1.5,(w/maxW)*100):0;
    el.innerHTML=
       '<div class="grip" title="拖拽或 Alt+↑/↓">⋮⋮</div>'
      +`<div class="rank">${idx+1}</div>`
      +`<div class="word">${esc(m.w)}</div>`
      +`<div class="track"><div class="bar${w?'':' zero'}" style="width:${bw.toFixed(2)}%"></div></div>`
      +`<div class="wt">${m.wt?w.toLocaleString():'—'}</div>`
      +`<div class="pct">${w?pct.toFixed(1)+'%':''}</div>`
      +`<div class="ln">${m.line}</div>`
      +`<div class="flags">${m.hp?'<span class="badge hp">手收</span>':''}`
      +`${ovWords.has(m.w)?'<span class="badge" style="color:var(--primary);border-color:var(--primary)">裁定</span>':''}`
      +`</div>`;
    el.title=(shortCode?'簡碼不參與調頻（一至三碼區逐字節繼承）\n'
             :onechar?'單字不參與重排（管道逐字節繼承字碼區）\n':'')
             +(m.tag?m.tag+'\n':'')+'行 '+m.line;
    if(!isSingle){
    el.addEventListener('dragstart',()=>{DRAG=el;el.classList.add('dragging');});
    el.addEventListener('dragend',()=>{el.classList.remove('dragging');DRAG=null;reorder();});
    el.addEventListener('dragover',e=>{e.preventDefault();
      if(!DRAG||DRAG===el)return;
      const r=el.getBoundingClientRect(), after=(e.clientY-r.top)>r.height/2;
      el.classList.toggle('drop-bottom',after); el.classList.toggle('drop-top',!after);});
    el.addEventListener('dragleave',()=>el.classList.remove('drop-top','drop-bottom'));
    el.addEventListener('drop',e=>{e.preventDefault();
      el.classList.remove('drop-top','drop-bottom');
      if(!DRAG||DRAG===el)return;
      const r=el.getBoundingClientRect(), after=(e.clientY-r.top)>r.height/2;
      after?el.after(DRAG):el.before(DRAG); reorder();});
    el.addEventListener('keydown',e=>{
      if(!e.altKey)return;
      if(e.key==='ArrowUp'&&el.previousElementSibling){
        el.previousElementSibling.before(el); e.preventDefault(); reorder(); el.focus();}
      else if(e.key==='ArrowDown'&&el.nextElementSibling){
        el.nextElementSibling.after(el); e.preventDefault(); reorder(); el.focus();}
    });
    }
    box.appendChild(el);
  });
  $('#ovs').innerHTML=d.overrides.length
    ? '<h2 style="margin:14px 0 6px">既有裁定</h2>'+d.overrides.map(o=>
        '<div class="pill" style="margin:3px 4px 0 0"><span class="mono">第'+o.line+'行</span> '
        +esc(o.raw)+'</div>').join('') : '';
}
function reorder(){SEQ=[...$('#rows').children].filter(t=>t.dataset.w).map(t=>t.dataset.w);}

function load(key){
  busy(true);
  const t=/^[a-z]{1,4}$/i.test(key)?'/api/group?code='+key:'/api/search?q='+encodeURIComponent(key);
  api(t).then(d=>{
    busy(false);
    if(d.members) return renderGroup(d);
    if(d.hits&&d.hits.length){
      const c=d.hits[0].codes;
      if(c.length===1) return api('/api/group?code='+c[0]).then(renderGroup);
      toast(`${d.hits.length} 個匹配；${d.hits[0].w} 有多碼 ${c.join(' / ')}，用碼查更精確`,'warn',5000);
    } else toast('查不到：'+key,'err');
  }).catch(()=>{busy(false);toast('請求失敗','err');});
}
$('#go').onclick=()=>{
  const k=$('#q').value.trim(); if(!k)return;
  if(!$('#pane-dict').hidden){$('#dw').value=k;$('#dc').value='';dictQuery(1);}
  else {openGroup(k);}
};
$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')$('#go').click();});

$('#prev').onclick=()=>{
  reorder();
  if(!CODE) return toast('先查一個碼組','warn');
  api('/api/order',{method:'POST',body:JSON.stringify({code:CODE,seq:SEQ})}).then(d=>{
    if(!d.ok) return toast((d.errors||['校驗未過']).join('；'),'err',6000);
    const t=toast('目標：'+d.target.join(' › ')+
      (d.conflicts.length?`\n將取代 ${d.conflicts.length} 條既有裁定`:'')+
      (d.warnings.length?'\n⚠ '+d.warnings[0]:''), d.warnings.length?'warn':'info', 6000);
    t.style.whiteSpace='pre-wrap';
    $('#out').textContent=JSON.stringify({目標:d.target,目前:d.currentFiltered,
      警告:d.warnings,將被取代:d.conflicts},null,1);
  });
};
$('#save').onclick=()=>{
  reorder();
  if(!CODE) return toast('先查一個碼組','warn');
  if(!confirm('寫入次序裁定？\n\n'+SEQ.join('  ›  '))) return;
  api('/api/commit',{method:'POST',body:JSON.stringify({code:CODE,seq:SEQ})}).then(d=>{
    if(!d.ok) return toast((d.errors||['校驗未過']).join('；'),'err',6000);
    toast(`已寫入（取代 ${d.replaced} 條既有裁定）`,'ok'); load(CODE); refreshPending(); loadRecent(); loadOverrides();
  });
};

$('#addbtn').onclick=()=>{
  const words=$('#newword').value.split(/[\s,、]+/).filter(Boolean);
  if(!words.length) return toast('先輸入詞','warn');
  api('/api/add',{method:'POST',body:JSON.stringify({words})}).then(d=>{
    const ok=d.added||[], sk=d.skipped||[];
    if(ok.length) toast('已加入暫存：'+ok.join('、'),'ok');
    sk.forEach(s=>toast(`${s.w}：${s.why}`,'warn',5000));
    if(ok.length) $('#newword').value='';
    refreshPending();
  });
};

/* ───── 字典 ───── */
const chip=(c,full)=>'<span class="chip'+(full?' full':'')+'">'+esc(c)+'</span>';
function dictQuery(page){
  DPAGE=page||1;
  const p=new URLSearchParams({word:$('#dw').value.trim(),code:$('#dc').value.trim(),
    len:$('#dl').value,clen:$('#dcl').value,src:$('#ds').value,sec:'',
    sort:$('#dso').value,group:$('#dgrp').checked?'1':'0',page:DPAGE,size:100});
  const tb=$('#drows'); tb.dataset.loaded='1';
  tb.innerHTML='<tr><td colspan="7"><div class="skel" style="width:50%"></div></td></tr>';
  api('/api/dict?'+p).then(d=>{
    DPAGES=d.pages;
    $('#dpage').textContent=`第 ${d.page} / ${d.pages} 頁 · ${d.total.toLocaleString()} 條`;
    tb.innerHTML=d.rows.length?d.rows.map(r=>
      '<tr class="clickable" data-c="'+esc(r.primary)+'">'
      +'<td><b>'+esc(r.w)+'</b></td>'
      +'<td>'+r.codes.map(c=>chip(c,c===r.primary)).join(' ')+'</td>'
      +'<td class="num">'+(r.wt||'—')+'</td>'
      +'<td class="num">'+[...r.w].length+'</td>'
      +'<td>'+(r.hp?'<span class="badge hp">手收</span> ':'')
        +(r.dropped?'<span class="badge drop">待刪</span> ':'')
        +(r.ov?'<span class="badge ov">裁定</span> ':'')
        +(r.tag?'<span class="badge">'+esc(r.tag.slice(0,14))+'</span>':'')+'</td>'
      +'<td class="num" title="'+(r.lines||[]).join(', ')+'">'
        +(r.lines&&r.lines.length>1?r.lines[0]+'+'+(r.lines.length-1):r.line)+'</td>'
      +'<td class="right">'+(r.dropped?''
        :`<button class="more ghost sm" data-w="${esc(r.w)}" title="更多操作" aria-label="更多操作">⋯</button>`)
      +'</td></tr>').join('')
      :'<tr><td colspan="7"><div class="empty">無匹配</div></td></tr>';
    [...tb.children].forEach(tr=>{
      if(!tr.dataset.c)return;
      tr.onclick=e=>{if(e.target.closest('.more'))return;tab('order');load(tr.dataset.c);};
      const b=tr.querySelector('.more');
      if(b)b.onclick=e=>{e.stopPropagation();openMenu(b,b.dataset.w);};
    });
  }).catch(()=>{tb.innerHTML='<tr><td colspan="7"><div class="empty">請求失敗</div></td></tr>';});
}
let CTXMENU=null;
function closeMenu(){ if(CTXMENU){CTXMENU.remove(); CTXMENU=null;} }
function openMenu(btn, w){
  closeMenu();
  const r=btn.getBoundingClientRect();
  const m=document.createElement('div');
  m.className='menu'; m.setAttribute('role','menu');
  m.innerHTML='<button class="danger-item" role="menuitem">棄用此詞…</button>'
             +'<div class="sep"></div>'
             +'<button role="menuitem" data-act="copy">複製編碼</button>';
  m.style.left=Math.min(r.left-110, innerWidth-170)+'px';
  m.style.top=(r.bottom+4)+'px';
  document.body.appendChild(m); CTXMENU=m;
  const first=m.querySelector('button'); first.focus();
  m.querySelector('.danger-item').onclick=e=>{e.stopPropagation();closeMenu();dropOne(w);};
  m.querySelector('[data-act=copy]').onclick=e=>{
    e.stopPropagation(); closeMenu();
    const row=btn.closest('tr');
    const codes=[...row.querySelectorAll('.chip')].map(x=>x.textContent);
    navigator.clipboard.writeText(codes[codes.length-1]||'').then(()=>toast('已複製編碼','ok',1600));
  };
  m.addEventListener('keydown',e=>{ if(e.key==='Escape'){closeMenu();btn.focus();} });
}
document.addEventListener('click',e=>{ if(CTXMENU && !e.target.closest('.menu')) closeMenu(); });
addEventListener('scroll',()=>closeMenu(),true);

function dropOne(w){
  if(!confirm('棄用「'+w+'」？\n\n• 從詞庫刪除（handpicked 寫 -詞）\n'
    +'• 同時寫入 rejected，防止補詞階段從語料撈回來\n• 需按「應用」才生效；可 git checkout 撤銷')) return;
  api('/api/drop',{method:'POST',body:JSON.stringify({words:[w]})}).then(d=>{
    if(d.skipped&&d.skipped.length)
      return toast(d.skipped.map(s=>s.w+'：'+s.why).join('；'),'warn',6000);
    toast('已標記棄用：'+w+'（待應用）','ok');
    refreshPending(); dictQuery(DPAGE);
  });
}
$('#dgo').onclick=()=>dictQuery(1);
['#dw','#dc'].forEach(s=>$(s).addEventListener('keydown',e=>{if(e.key==='Enter')dictQuery(1);}));
['#dl','#dcl','#ds','#dso','#dgrp'].forEach(s=>$(s).onchange=()=>dictQuery(1));
$('#dfirst').onclick=()=>dictQuery(1);
$('#dprev').onclick=()=>dictQuery(Math.max(1,DPAGE-1));
$('#dnext').onclick=()=>dictQuery(Math.min(DPAGES,DPAGE+1));
$('#dlast').onclick=()=>dictQuery(DPAGES);
api('/api/dict/stats').then(s=>{
  const L=s.by_len||{};
  $('#dstats').innerHTML=`共 <b>${s.total.toLocaleString()}</b> 條`
    +`<span>／ <b>${(s.words||s.total).toLocaleString()}</b> 詞形</span>`
    +`<span>單字 <b>${(L['1']||0).toLocaleString()}</b></span>`
    +`<span>二字 <b>${(L['2']||0).toLocaleString()}</b></span>`
    +`<span>四字 <b>${(L['4']||0).toLocaleString()}</b></span>`
    +`<span>手收 <b>${s.handpicked.toLocaleString()}</b></span>`
    +`<span>有標記 <b>${s.tagged.toLocaleString()}</b></span>`;
});

/* ───── 碼圖 ───── */
let MPREFIX='';
function mapGo(prefix){
  MPREFIX=prefix;
  api('/api/map?prefix='+encodeURIComponent(prefix)).then(d=>{
    if(d.error){toast(d.error,'err');return;}
    $('#mapgrid').dataset.loaded='1';
    // 麵包屑
    let cr='<span style="color:var(--fg-faint)">碼空間</span>';
    for(let i=1;i<=prefix.length;i++){
      const p=prefix.slice(0,i);
      cr+=' <span style="color:var(--fg-faint)">/</span> <a data-p="'+p+'">'+p+'</a>';
    }
    $('#crumbs').innerHTML=cr;
    $('#crumbs').querySelectorAll('a').forEach(a=>a.onclick=()=>mapGo(a.dataset.p));
    const ovtot=d.children.reduce((a,c)=>a+(c.ov||0),0);
    $('#mapstats').innerHTML=`第 ${d.depth+1} 層　<b>${d.total_words.toLocaleString()}</b> 詞`
      +`　<b>${d.total_groups.toLocaleString()}</b> 個碼`
      +(prefix?`　前綴本身是碼：<b>${d.exact_words}</b> 詞`:'')
      +(ovtot?`　<span style="color:var(--warn)">● 本層含 ${ovtot} 個調過的碼位</span>`:'');
    // 格子
    $('#mapgrid').innerHTML=d.children.map(c=>{
      const h=c.heat;                       // 0..1 線性
      const pct=c.words?Math.round(6+h*88):0;
      const dark=h>0.55;
      const empty=c.words? '':' empty';
      const bg=c.words?`background:color-mix(in srgb, var(--primary) ${pct}%, var(--surface))`:'';
      return `<div class="cell${dark?' dark':''}${empty}${c.ov?' hasov':''}" data-l="${c.letter}" data-n="${c.words}"
        data-ov="${c.ov||0}" title="${c.ov?c.ov+' 個碼位有裁定':''}" style="${bg}">
        <div class="L">${c.letter}</div><div class="N">${c.words?c.words.toLocaleString():'·'}</div></div>`;
    }).join('');
    $('#mapgrid').querySelectorAll('.cell').forEach(el=>{
      el.onclick=()=>{
        if(!+el.dataset.n) return;
        const np=prefix+el.dataset.l;
        if(np.length>=4) openGroup(np); else mapGo(np);
      };
    });
    // 前綴本身是一個碼 → 直接可調序
    $('#mapexact').innerHTML = (prefix && d.exact_words)
      ? `<button class="primary" id="mapexactbtn">打開碼組 ${prefix}（${d.exact_words} 詞）→ 調序</button>` : '';
    const b=$('#mapexactbtn'); if(b) b.onclick=()=>openGroup(prefix);
    // 每鑽一層寫一條歷史 —— 瀏覽器後退即「返回上一級」
    const h='#map'+(prefix?'/'+prefix:'');
    if(location.hash!==h) history.pushState({tab:'map',prefix:prefix},'',h);
    $('#back').disabled = history.length<=1;
  });
}
function openGroup(code){ CODE=code; $('#q').value=code; load(code); tab('order'); window.scrollTo(0,0); }

function loadOverrides(){
  api('/api/overrides').then(d=>{
    const c=d.counts||{};
    $('#ovcount').textContent=`（${c.total||0}）`;
    const chips=[['ok','生效',c.ok],['pending','待重跑',c.pending],['invalid','無效',c.invalid]]
      .filter(x=>x[2]).map(x=>`<span class="badge">${x[1]} ${x[2]}</span>`).join(' ');
    $('#ovlist').innerHTML=(chips?`<div class="stats" style="margin-bottom:8px">${chips}</div>`:'')
      +((d.rows||[]).length?d.rows.map(r=>{
        const col=r.status==='ok'?'var(--ok)':r.status==='pending'?'var(--warn)':'var(--err)';
        const tag=r.status==='ok'?'生效':r.status==='pending'?'待重跑':'無效';
        return `<div class="ovrow clickable" data-c="${esc(r.code)}" title="${esc(r.why||'')}">
          <code>${esc(r.code||'?')}</code>
          <span style="flex:1">${esc(r.words.join(' › '))}</span>
          <span style="color:${col};font-size:12px">${tag}${r.why?'·'+esc(r.why):''}</span>
          <span class="mono" style="color:var(--fg-faint);font-size:12px">行 ${r.line}</span></div>`;
      }).join(''):'<div class="empty">還沒調過序</div>');
    [...document.querySelectorAll('#ovlist .ovrow')].forEach(el=>{
      el.onclick=()=>{ if(el.dataset.c) openGroup(el.dataset.c); };
    });
  });
}

/* ───── 診斷 ───── */
let DIAG=null, DCUR='F';
function diagLoad(){
  api('/api/diag').then(d=>{
    DIAG={F:d.F};                     // 此頁只留「詞源排序衝突」，其餘五類已撤
    DCUR='F';
    $('#diagrows').dataset.loaded='1';
    renderDiag();
  });
}
function renderDiag(){
  const v=DIAG[DCUR];
  $('#diagdesc').textContent=v.desc;
  const rows=v.items.map(it=>{
    if(DCUR==='F'){
      const ps=it.pairs.slice(0,2).map(p=>
        `${esc(p.a)} vs ${esc(p.b)}　<span style="color:var(--warn)">${p.ga_names.join('·')}</span>`
        +` ↔ <span style="color:var(--primary)">${p.gb_names.join('·')}</span>`).join('<br>');
      return {main:`<code>${esc(it.code)}</code> 衝突 <b>${it.n}</b> 對／組內 ${it.of} 詞`,
              det:ps, code:it.code};
    }
    if(DCUR==='A'){
      const ws=it.words.join(' / ');
      return {main:`<code>${esc(it.code)}</code> ${esc(ws)}`,
              det:`${it.words.length} 個詞同為 ${(+it.wt).toLocaleString()}`,
              code:it.code};
    }
    if(DCUR==='B'){
      return {main:`<b>${esc(it.w)}</b> <code>${esc(it.code)}</code>`,
              det:`最冷組成字 ${(+it.minchar).toLocaleString()}，本詞無頻分`, code:it.code};
    }
    if(DCUR==='C'){
      return {main:`<code>${esc(it.code)}</code> ${esc(it.hi)} > ${esc(it.lo)}`,
              det:`${esc(it.hi)} ${(+it.hiwt).toLocaleString()} vs ${esc(it.lo)} ${(+it.lowt).toLocaleString()}`,
              code:it.code};
    }
    if(DCUR==='D'){
      return {main:`<b>${esc(it.w)}</b>`,
              det:it.ranks.map(r=>`${r.code}: 第 ${r.rank}/${r.of}`).join('　'),
              code:it.ranks[0].code};
    }
    return {main:`${esc(it.w)} <code>${esc(it.code)}</code>`,
            det:`權重 ${(+it.wt).toLocaleString()}`, code:it.code};
  });
  $('#diagrows').innerHTML=rows.length?rows.map(r=>
    `<tr class="clickable" data-c="${esc(r.code)}"><td>${r.main}</td><td style="color:var(--fg-muted)">${r.det}</td>
     <td><button class="sm ghost">調序</button></td></tr>`).join('')
    :'<tr><td colspan="3"><div class="empty">此類無命中</div></td></tr>';
  [...$('#diagrows').children].forEach(tr=>{
    if(tr.dataset.c) tr.onclick=()=>openGroup(tr.dataset.c);
  });
}

/* ───── 任務中心 ───── */
let TASKS=[], TPOLL=null;
function taskLoad(){
  api('/api/tasks').then(d=>{
    TASKS=d.tasks;
    const order=['檢查','生成','工作流','備份'];
    const by={}; d.tasks.forEach(x=>{(by[x.group]=by[x.group]||[]).push(x);});
    $('#taskgroups').innerHTML=order.filter(g=>by[g]).map(g=>
      `<div style="margin-bottom:16px"><div style="font-size:12.5px;color:var(--fg-muted);margin-bottom:6px">${g}</div>
       <div class="row" style="gap:8px">${by[g].map(x=>{
         const cls=x.level==='danger'?'danger':(x.level==='warn'?'':'primary');
         return `<button class="taskbtn ${cls}" data-k="${x.key}" data-lv="${x.level}" data-n="${esc(x.name)}">${esc(x.name)}</button>`;
       }).join('')}</div></div>`).join('');
    $('#taskgroups').querySelectorAll('.taskbtn').forEach(b=>b.onclick=()=>taskRun(b));
  });
}
function taskRun(btn){
  const key=btn.dataset.k, lv=btn.dataset.lv, name=btn.dataset.n;
  const go=()=>api('/api/run',{method:'POST',body:JSON.stringify({key,confirm:'APPLY'})})
    .then(d=>{
      if(!d.ok){toast(d.error||'啟動失敗','err');return;}
      $('#taskname').textContent='· '+name;
      toast('已開始：'+name,'info',1800);
      startTaskPoll();
    });
  if(lv==='safe') return go();
  if(lv==='warn'){
    if(!confirm('執行「'+name+'」？\n\n這會改動檔案或跑較久，但不會部署。')) return;
    return go();
  }
  // danger：需手打 APPLY
  const inp=prompt('危險操作：'+name+'\n會部署或對外投遞。\n\n輸入 APPLY 以確認：');
  if((inp||'').toUpperCase()!=='APPLY') return toast('已取消','warn');
  go();
}
function startTaskPoll(){
  clearInterval(TPOLL);
  const t0=Date.now();
  TPOLL=setInterval(()=>{
    const sec=Math.round((Date.now()-t0)/1000);
    api('/api/job').then(j=>{
      $('#taskout').textContent=(j.log||'…')+'\n──────── 已 '+sec+'s';
      $('#taskout').scrollTop=$('#taskout').scrollHeight;
      if(j.state!=='running'){
        clearInterval(TPOLL);
        toast(j.state==='done'?('完成（'+(j.elapsed||sec)+'s）'):('失敗（rc='+(j.rc??'?')+'）'),
              j.state==='done'?'ok':'err',5000);
        refreshPending(); loadRecent();
      }
    });
  },900);
}

/* ───── 設置 ───── */
let SETKEYS=[];
function renderKeys(){
  const all=$('#keysrc').value==='all';
  const seen=new Set();
  const rows=SETKEYS.filter(k=>{
    if(!all && k.src==='default') return false;
    const key=k.key+'|'+k.act+'|'+k.when;
    if(seen.has(key))return false; seen.add(key); return true;
  });
  const own=SETKEYS.filter(k=>k.src!=='default').length;
  $('#keymeta').textContent=`方案自訂 ${own} 條 · 內建導航 ${SETKEYS.length-own} 條`;
  $('#set-keys').innerHTML=rows.map(k=>
    `<tr><td><kbd>${esc(k.key)}</kbd></td><td><code>${esc(k.act)}</code></td>
     <td style="color:var(--fg-muted)">${esc(k.when||'always')}</td>
     <td><span class="badge">${esc(k.src)}</span></td></tr>`).join('');
}
$('#keysrc').onchange=renderKeys;
function setLoad(){
  api('/api/settings').then(d=>{
    if(d.yaml_error){$('#setalert').innerHTML='<div class="card" style="border-color:var(--danger)">YAML 解析失敗：'+esc(d.yaml_error)+'</div>';}
    const dep=d.deploy;
    $('#setalert').innerHTML = dep.stale
      ? `<div class="card" style="border-left:3px solid var(--warn)">
           <b style="color:var(--warn)">⚠ 詞典比編譯產物新</b>
           <div class="sub" style="margin:4px 0 0">xuma_L.dict.yaml 的修改時間晚於 build/*.bin
           ${dep.bins} 個編譯檔 —— 改完詞典沒有重新部署。執行 <code>ibus restart</code>（Windows 爲「重新部署」）後此提示消失。</div></div>`
      : `<div class="card" style="border-left:3px solid var(--ok)"><b style="color:var(--ok)">✓ 已部署</b>
           <span class="sub" style="margin-left:8px">詞典與編譯產物同步（${dep.bins} 個 bin）</span></div>`;

    // 開關
    $('#set-switches').innerHTML=d.switches.map(s=>{
      const st=(s.states||[]).map((x,i)=>i===s.reset?`<b>${esc(x)}</b>`:esc(x)).join(' / ');
      return `<tr><td><code>${esc(s.name)}</code>${s.group?' <span class="badge">組</span>':''}</td>
        <td>${st||'—'}</td><td class="num">${s.reset!=null?s.reset:'—'}</td></tr>`;
    }).join('');

    // 快捷鍵（去重；可只看方案自訂）
    SETKEYS = d.keys;
    renderKeys();

    // 引擎鏈
    const byStage={};
    d.chain.forEach(c=>{(byStage[c.stage]=byStage[c.stage]||[]).push(c);});
    $('#set-chain').innerHTML=Object.entries(byStage).map(([st,items])=>
      `<div style="margin-bottom:10px"><div style="font-size:12.5px;color:var(--fg-muted);margin-bottom:4px">${esc(st)}（${items.length}）</div>
       <div class="row" style="gap:6px">${items.map((c,i)=>{
         const nm=c.lua?c.lua.split('/').pop():c.item.replace(/^lua_/,'');
         const bad=c.lua && !d.refs.find(r=>r.module===c.lua&&r.exists);
         return `<span class="pill"${bad?' style="border-color:var(--danger);color:var(--danger)"':''}
           title="${esc(c.item)}">${i?'<span style="color:var(--fg-faint)">›</span> ':''}${esc(nm)}</span>`;
       }).join('')}</div></div>`).join('');

    // 引用校驗
    const bad=d.refs.filter(r=>!r.exists);
    let h=`<div class="stats" style="margin-bottom:8px"><span>lua 模組引用 <b>${d.refs.length}</b></span>
      <span style="color:${bad.length?'var(--danger)':'var(--ok)'}">缺失 <b>${bad.length}</b></span>
      <span style="color:${d.requires.length?'var(--warn)':'var(--ok)'}">缺 require <b>${d.requires.length}</b></span></div>`;
    h+='<table><thead><tr><th>模組</th><th>檔案</th><th style="width:10%">狀態</th></tr></thead><tbody>'
      +d.refs.map(r=>`<tr><td><code>${esc(r.module)}</code> <span class="badge">${esc(r.kind)}</span></td>
         <td style="color:var(--fg-muted)">lua/${esc(r.file)}</td>
         <td>${r.exists?'<span style="color:var(--ok)">✓</span>':'<span style="color:var(--danger)">✗</span>'}</td></tr>`).join('')
      +(d.requires.length?d.requires.map(q=>`<tr><td colspan="3" style="color:var(--warn)">
         ${esc(q.from)}:${q.line} require('${esc(q.need)}') 找不到檔案</td></tr>`).join(''):'')
      +'</tbody></table>';
    $('#set-refs').innerHTML=h;
  });
}

/* ───── 日誌中心 ───── */
let LKEY='events', LAUTO=null;
function fmtSize(s){return s>1048576?(s/1048576).toFixed(1)+'M':s>1024?(s/1024).toFixed(1)+'K':s+'B';}
function logList(){
  api('/api/logs').then(d=>{
    $('#loglist').innerHTML=d.sources.map(s=>
      `<div class="src${s.key===LKEY?' on':''}${s.exists?'':' off'}" data-k="${s.key}" data-ok="${s.exists}">
        ${esc(s.name)}<span class="m">${s.exists?(s.unit==='條'?s.size+' 條':fmtSize(s.size)+' · '+s.mtime):'（無）'}${s.file?' · '+esc(s.file):''}</span>
      </div>`).join('');
    $('#loglist').querySelectorAll('.src').forEach(el=>{
      if(el.dataset.ok!=='true') return;
      el.onclick=()=>{LKEY=el.dataset.k;$('#logact').style.display='none';logList();logLoad();};
    });
  });
}
function logLoad(){
  const p=new URLSearchParams({key:LKEY,lines:$('#loglines').value,
    q:$('#logq').value.trim(),errors:$('#logerr').checked?'1':'0',
    action:$('#logact').value||''});
  api('/api/log?'+p).then(d=>{
    if(d.error){$('#logout').textContent=d.error;return;}
    $('#logout').textContent=d.text||'（無內容）';
    $('#logout').scrollTop=$('#logout').scrollHeight;
    let m=d.name+(d.file?' · '+d.file:'');
    if(d.total!=null) m+=` · ${d.total} 條`;
    if(d.lines!=null) m+=`（檔內共 ${d.lines} 行）`;
    if(d.missing) m+=' · 檔案不存在';
    $('#logmeta').textContent=m;
    // events 專屬：動作篩選
    if(d.actions){
      const sel=$('#logact');
      sel.style.display='';
      sel.innerHTML='<option value="">全部動作</option>'+Object.entries(d.actions)
        .sort((a,b)=>b[1]-a[1]).map(([k,v])=>`<option value="${esc(k)}">${esc(k)} (${v})</option>`).join('');
      if(p.get('action')) sel.value=p.get('action');
    } else $('#logact').style.display='none';
  });
}
$('#logreload').onclick=logLoad;
$('#logq').addEventListener('keydown',e=>{if(e.key==='Enter')logLoad();});
['#loglines','#logerr'].forEach(s=>$(s).onchange=logLoad);
$('#logact').onchange=logLoad;
$('#logauto').onchange=e=>{
  clearInterval(LAUTO); LAUTO=null;
  if(e.target.checked) LAUTO=setInterval(logLoad,3000);
};

/* ───── 權重學習原型 ───── */
$('#learnbtn').onclick=()=>{
  $('#learnbtn').disabled=true; $('#learnout').innerHTML='<div class="skel" style="width:60%"></div>';
  api('/api/learn').then(d=>{
    $('#learnbtn').disabled=false;
    if(d.error){$('#learnout').innerHTML='<div class="err">'+esc(d.error)+'</div>';return;}
    const pct=x=>x==null?'—':x+'%';
    let h='<div class="stats" style="margin-bottom:10px">'
      +`<span>標籤 <b>${d.pairs}</b> 對</span><span>參數 <b>${d.params}</b> 個</span>`
      +`<span>當前滿足 <b>${d.baseline.ok}/${d.baseline.of}</b>（${pct(d.baseline.pct)}）</span>`
      +`<span>擬合滿足 <b>${d.fitted.ok}/${d.fitted.of}</b>（${pct(d.fitted.pct)}）</span>`
      +`<span style="color:var(--warn)">留一驗證 <b>${d.loo.ok}/${d.loo.of}</b>（${pct(d.loo.pct)}）</span></div>`;
    h+='<table><thead><tr><th>語源</th><th class="num">當前</th><th class="num">擬合</th><th class="num">倍率</th></tr></thead><tbody>'
      +d.sources.map(s=>{
        const r=s.current? (s.learned/s.current):0;
        return `<tr><td>${esc(s.name)}</td><td class="num">${s.current.toFixed(3)}</td>`
          +`<td class="num">${s.learned.toFixed(4)}</td>`
          +`<td class="num" style="color:${Math.abs(r-1)>2?'var(--warn)':'inherit'}">×${r.toFixed(2)}</td></tr>`;
      }).join('')+'</tbody></table>';
    if(d.pairs <= 2*d.params)
      h+=`<div style="color:var(--warn);margin-top:10px;font-size:13px">⚠ 標籤 ${d.pairs} 對 &lt; 參數 ${d.params}×2 —— 模型不可辨識，擬合結果僅供觀察，不可採用。需數百對偏好樣本。</div>`;
    $('#learnout').innerHTML=h;
  });
};

/* ───── 待應用 ───── */
function refreshPending(){
  api('/api/pending').then(p=>{
    let h='';
    if(p.spool.length){
      h+='<div style="margin-bottom:6px">暫存詞 '+p.spool.length+'：</div><div class="row" style="gap:6px">'
        +p.spool.map(w=>'<span class="pill"><span class="mono">'+esc(w)+'</span></span>').join('')+'</div>';
    }
    const f=[];
    if(p.overrides_changed)f.push('次序裁定');
    if(p.handpicked_changed)f.push('手收清單');
    if(f.length) h+='<div style="color:var(--warn);margin-top:8px">⚠ '+f.join('、')
        +' 已改動，尚未重跑管道</div>';
    if(!p.spool.length&&!f.length) h+='<div style="color:var(--fg-faint)">沒有待應用的變更</div>';
    $('#pend').innerHTML=h;
    $('#apply').disabled=!p.count;
  });
}

/* ───── 應用（二次確認）───── */
$('#apply').onclick=()=>{
  api('/api/apply-token',{method:'POST',body:'{}'}).then(t=>{
    TOKEN=t.token;
    const p=t.pending, li=[];
    if(p.spool.length) li.push('暫存詞 '+p.spool.length+' 個併入手收清單：<b>'+esc(p.spool.join('、'))+'</b>');
    if(p.overrides_changed) li.push('次序裁定已改動，重跑詞頻管道');
    if(p.handpicked_changed) li.push('手收清單已改動，重跑詞頻管道');
    $('#dlgsum').innerHTML='<ul>'+li.map(x=>'<li>'+x+'</li>').join('')+'</ul>';
    $('#dlginput').value=''; $('#dlgok').disabled=true; $('#dlgerr').textContent='';
    $('#dlg').showModal(); $('#dlginput').focus();
  });
};
$('#dlginput').oninput=e=>{$('#dlgok').disabled=e.target.value.toUpperCase()!=='APPLY';};
$('#dlgcancel').onclick=()=>$('#dlg').close();
$('#dlgok').onclick=()=>{
  $('#dlgok').disabled=true;
  api('/api/apply',{method:'POST',body:JSON.stringify({token:TOKEN,confirm:'APPLY'})}).then(d=>{
    if(!d.ok){$('#dlgerr').textContent=d.error||'失敗';$('#dlgok').disabled=false;return;}
    $('#dlg').close(); toast('開始執行…','info',1500);
    const t0=Date.now();
    const tick=setInterval(()=>{
      const sec=Math.round((Date.now()-t0)/1000);
      api('/api/job').then(j=>{
        $('#out').textContent=(j.log||'…')+'\n──────── 已 '+sec+'s';
        if(j.state!=='running'){
          clearInterval(tick);
          toast(j.state==='done'?`應用完成（${j.elapsed||sec}s）`:'應用失敗', j.state==='done'?'ok':'err',6000);
          refreshPending(); if(CODE)load(CODE);
          if($('#drows').dataset.loaded) dictQuery(DPAGE);
        }
      });
    },900);
  });
};

$('#tololgs').onclick=()=>tab('logs');
function loadRecent(){
  api('/api/recent').then(d=>{
    $('#recent').innerHTML=d.rows.length?d.rows.map(r=>
      '<div><span class="mono" style="color:var(--fg-faint)">'
      +esc((r.ts||'').slice(5,16).replace('T',' '))+'</span> '
      +'<b>'+esc(r.action)+'</b> <code>'+esc(r.path)+'</code>'
      +'<div style="color:var(--fg-muted)">'+esc(r.detail)+'</div></div>').join('')
      :'<div class="empty">無記錄</div>';
  });
}

const THEMEBTN=$('#theme');
function paintTheme(){
  const dark=document.documentElement.dataset.theme==='dark';
  THEMEBTN.textContent=dark?'☾':'☀';
  THEMEBTN.title=dark?'目前深色，點擊切淺色':'目前淺色，點擊切深色';
}
THEMEBTN.onclick=()=>{
  const cur=document.documentElement.dataset.theme==='dark'?'light':'dark';
  document.documentElement.dataset.theme=cur;
  localStorage.setItem('wishd-theme',cur);
  paintTheme();
};
paintTheme();

$('#back').onclick=()=>{ if(history.length>1) history.back(); else tab('order'); };
document.addEventListener('keydown',e=>{
  if(e.altKey && e.key==='ArrowLeft'){ e.preventDefault(); $('#back').click(); return; }
  if(e.altKey && /^[1-6]$/.test(e.key)){
    e.preventDefault(); tab(TABS[+e.key-1]); return;
  }
  if(e.key==='/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)){
    e.preventDefault(); $('#q').focus(); $('#q').select();
  }
  if(e.key==='Escape' && $('#dlg').open) $('#dlg').close();
});
refreshPending(); loadRecent(); loadOverrides();
if(location.hash) applyHash(); else tab('order', false);
</script></body></html>"""


def main():
    groups, _ = wishd.load_dict()
    print(f'詞庫意願台：{sum(len(v) for v in groups.values())} 條 / {len(groups)} 個碼組')
    print(f'   http://{HOST}:{PORT}')
    print('   只綁 127.0.0.1——共享機器勿開。應用需二次確認（token + 明文 APPLY）。Ctrl-C 結束。')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
