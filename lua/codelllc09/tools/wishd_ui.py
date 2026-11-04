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
import secrets
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
        if src == 'plain' and (tag or w in hp_add or w in hp_drop):
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


def api_dict_stats():
    import collections
    entries = wishd.load_entries()
    by_len = collections.Counter(len(w) for w, *r in entries)
    by_code_len = collections.Counter(len(c) for _, c, *r in entries)
    tagged = sum(1 for e in entries if e[4])
    hp = wishd.load_handpicked()
    return {'total': len(entries), 'by_len': dict(sorted(by_len.items())),
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
.handle{width:22px;color:var(--fg-faint);cursor:grab;user-select:none;text-align:center}
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
  <div class="brand"><span class="dot"></span>詞庫意願台</div>
  <div class="search"><input id="q" placeholder="碼（oooo）或詞（合適）" aria-label="查詢"></div>
  <button id="go" class="primary">查詢</button>
  <span class="hint"><kbd>/</kbd> 聚焦搜尋</span>
  <button id="theme" class="ghost" title="切換深/淺色" aria-label="切換深淺色">◐</button>
</header>

<nav role="tablist">
  <button id="tab-order" role="tab" aria-selected="true">調序 / 加詞</button>
  <button id="tab-dict" role="tab" aria-selected="false">字典</button>
</nav>

<main id="main">
 <section id="pane-order" role="tabpanel">
   <div class="card">
     <h2 id="gtitle">同碼組</h2>
     <p class="sub" id="gsub">輸入碼或詞開始。拖拽 <span class="handle">⠿</span> 或用 <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> 調整次序。</p>
     <table class="plain"><thead><tr>
       <th style="width:28px"></th><th>詞</th><th class="num" style="width:110px">頻分</th>
       <th style="width:120px">來源</th><th style="width:72px">行</th></tr></thead>
       <tbody id="rows"><tr><td colspan="5"><div class="empty">尚未載入</div></td></tr></tbody></table>
     <div class="row" style="margin-top:12px">
       <button id="prev">預覽變更</button>
       <button id="save" class="primary">寫入裁定</button>
     </div>
     <div id="ovs" style="margin-top:14px"></div>
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

 <section id="pane-dict" role="tabpanel" hidden>
   <div class="card">
     <h2>字典</h2>
     <p class="sub">十八萬七千詞形；同一詞的簡碼與全碼併為一行，<b>全碼</b>高亮、簡碼灰顯。</p>
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
         <option value="drop">已標記棄用</option><option value="plain">無標記</option></select>
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
     <h2>最近動態</h2>
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
function tab(which){
  const order=which==='order';
  $('#tab-order').setAttribute('aria-selected',order);
  $('#tab-dict').setAttribute('aria-selected',!order);
  $('#pane-order').hidden=!order;
  $('#pane-dict').hidden=order;
  $('#pane-side').hidden=!order;
  $('#main').classList.toggle('dictmode',!order);
  if(!order && !$('#drows').dataset.loaded) dictQuery(1);
}
$('#tab-order').onclick=()=>tab('order');
$('#tab-dict').onclick=()=>tab('dict');

/* ───── 同碼組 ───── */
function renderGroup(d){
  CODE=d.code;
  $('#gtitle').textContent=d.exists?`碼組 ${d.code} · ${d.members.length} 詞`:`碼組 ${d.code}（不存在）`;
  const tb=$('#rows'); tb.innerHTML='';
  SEQ=d.members.map(m=>m.w);
  if(!d.members.length){
    tb.innerHTML='<tr><td colspan="5"><div class="empty">此碼組沒有詞</div></td></tr>';
  }
  d.members.forEach((m,idx)=>{
    const tr=document.createElement('tr');
    tr.draggable=true; tr.tabIndex=0; tr.dataset.w=m.w; tr.classList.add('movable');
    tr.innerHTML='<td class="handle" title="拖拽或 Alt+↑/↓">⠿</td>'
      +'<td><b>'+esc(m.w)+'</b></td>'
      +'<td class="num">'+(m.wt||'—')+'</td>'
      +'<td>'+(m.hp?'<span class="badge hp">手收</span> ':'')
      +'<span class="badge">'+esc((m.tag||'').slice(0,16)||'—')+'</span></td>'
      +'<td class="num">'+m.line+'</td>';
    tr.addEventListener('dragstart',()=>{DRAG=tr;tr.classList.add('dragging');});
    tr.addEventListener('dragend',()=>{tr.classList.remove('dragging');DRAG=null;reorder();});
    tr.addEventListener('dragover',e=>{e.preventDefault();
      if(!DRAG||DRAG===tr)return;
      const r=tr.getBoundingClientRect(), after=(e.clientY-r.top)>r.height/2;
      tr.classList.toggle('drop-bottom',after); tr.classList.toggle('drop-top',!after);});
    tr.addEventListener('dragleave',()=>tr.classList.remove('drop-top','drop-bottom'));
    tr.addEventListener('drop',e=>{e.preventDefault();
      tr.classList.remove('drop-top','drop-bottom');
      if(!DRAG||DRAG===tr)return;
      const r=tr.getBoundingClientRect(), after=(e.clientY-r.top)>r.height/2;
      after?tr.after(DRAG):tr.before(DRAG); reorder();});
    tr.addEventListener('keydown',e=>{
      if(!e.altKey)return;
      if(e.key==='ArrowUp'&&tr.previousElementSibling){
        tr.previousElementSibling.before(tr); e.preventDefault(); reorder();
        tr.focus(); toast('已上移：'+m.w,'info',1400);}
      else if(e.key==='ArrowDown'&&tr.nextElementSibling){
        tr.nextElementSibling.after(tr); e.preventDefault(); reorder();
        tr.focus(); toast('已下移：'+m.w,'info',1400);}
    });
    tb.appendChild(tr);
  });
  $('#ovs').innerHTML=d.overrides.length
    ? '<h2 style="margin-bottom:6px">既有裁定</h2>'+d.overrides.map(o=>
        '<div class="pill" style="margin:3px 4px 0 0"><span class="mono">第'+o.line+'行</span>'
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
  else {tab('order'); load(k);}
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
    toast(`已寫入（取代 ${d.replaced} 條既有裁定）`,'ok'); load(CODE); refreshPending();
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
        +'<span class="badge">'+esc((r.tag||'').slice(0,12)||'—')+'</span></td>'
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
    +`<span>單字 <b>${(L['1']||0).toLocaleString()}</b></span>`
    +`<span>二字 <b>${(L['2']||0).toLocaleString()}</b></span>`
    +`<span>四字 <b>${(L['4']||0).toLocaleString()}</b></span>`
    +`<span>手收 <b>${s.handpicked.toLocaleString()}</b></span>`
    +`<span>有標記 <b>${s.tagged.toLocaleString()}</b></span>`;
});

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

document.addEventListener('keydown',e=>{
  if(e.key==='/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)){
    e.preventDefault(); $('#q').focus(); $('#q').select();
  }
  if(e.key==='Escape' && $('#dlg').open) $('#dlg').close();
});
refreshPending(); loadRecent();
</script></body></html>"""


def main():
    groups, _ = wishd.load_dict()
    print(f'詞庫意願台：{sum(len(v) for v in groups.values())} 條 / {len(groups)} 個碼組')
    print(f'   http://{HOST}:{PORT}')
    print('   只綁 127.0.0.1——共享機器勿開。應用需二次確認（token + 明文 APPLY）。Ctrl-C 結束。')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
