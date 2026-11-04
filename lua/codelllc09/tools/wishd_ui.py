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


PAGE = r"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>詞庫意願台</title>
<style>
 body{font:14px/1.6 -apple-system,"Noto Sans CJK TC",sans-serif;margin:0;background:#f6f7f9;color:#222}
 header{background:#263238;color:#fff;padding:10px 16px;display:flex;gap:12px;align-items:center}
 header input{font:inherit;padding:5px 10px;border:0;border-radius:4px;width:280px}
 nav{display:flex;gap:4px;background:#eceff1;padding:0 16px}
 nav button{border:0;background:transparent;padding:9px 18px;cursor:pointer;font:inherit;border-bottom:3px solid transparent}
 nav button.on{border-bottom-color:#1565c0;color:#1565c0;font-weight:600;background:#fff}
 main{display:grid;grid-template-columns:1fr 400px;gap:16px;padding:16px}
 section.hide,aside.hide{display:none}
 main.dictmode{grid-template-columns:1fr}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:14px;margin-bottom:16px}
 h3{margin:0 0 10px;font-size:14px;color:#37474f}
 table{width:100%;border-collapse:collapse}
 td,th{padding:5px 8px;border-bottom:1px solid #eee;text-align:left;font-size:13px}
 th{background:#fafbfc;position:sticky;top:0;z-index:1}
 tr.drag{opacity:.35} tr.over{outline:2px dashed #1565c0}
 tr.clickable{cursor:pointer} tr.clickable:hover{background:#f1f8ff}
 .w{cursor:grab;user-select:none}
 .wt{color:#888;font-variant-numeric:tabular-nums;text-align:right}
 .tag{color:#00695c;font-size:12px} .hp{background:#e8f5e9}
 code{background:#eceff1;padding:1px 5px;border-radius:4px;font-size:12px}
 button{font:inherit;padding:6px 12px;border:1px solid #cfd8dc;background:#fff;border-radius:6px;cursor:pointer}
 button.primary{background:#1565c0;color:#fff;border-color:#1565c0}
 button.danger{background:#c62828;color:#fff;border-color:#c62828}
 button:disabled{opacity:.5;cursor:not-allowed}
 pre{background:#263238;color:#b2ff59;padding:10px;border-radius:6px;overflow:auto;max-height:260px;font-size:12px}
 textarea,input[type=text]{font:inherit;padding:6px 8px;border:1px solid #cfd8dc;border-radius:6px;box-sizing:border-box}
 textarea{width:100%;resize:vertical}
 .warn{color:#b26a00} .err{color:#c62828} .ok{color:#2e7d32} .muted{color:#90a4ae}
 .bar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
 .spool{display:flex;flex-wrap:wrap;gap:6px}
 .chip{background:#e3f2fd;border-radius:12px;padding:2px 10px;font-size:13px}
 .scroll{max-height:70vh;overflow:auto;border:1px solid #eee;border-radius:6px}
 dialog{border:0;border-radius:10px;padding:0;max-width:520px;width:92vw;box-shadow:0 12px 40px rgba(0,0,0,.3)}
 dialog::backdrop{background:rgba(0,0,0,.45)}
 .dlg-h{background:#c62828;color:#fff;padding:12px 16px;font-weight:600}
 .dlg-b{padding:16px} .dlg-f{padding:0 16px 16px;display:flex;gap:8px;justify-content:flex-end}
 dialog input{font:inherit;padding:8px;border:1px solid #cfd8dc;border-radius:6px;width:100%;box-sizing:border-box}
 ul{margin:6px 0;padding-left:20px}
 .stats{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;color:#546e7a}
 .stats b{color:#263238}
</style>
<header>
  <strong>詞庫意願台</strong>
  <input id="q" placeholder="碼（oooo）或詞（合適）…" autofocus>
  <button id="go" class="primary">查</button>
  <span id="hint" class="muted" style="margin-left:auto">只寫意願通道，永不直接改詞典</span>
</header>
<nav>
  <button id="tab-order" class="on">調序 / 加詞</button>
  <button id="tab-dict">字典</button>
</nav>
<main id="main">
 <section id="pane-order">
   <div class="card">
     <h3 id="gtitle">同碼組</h3>
     <div class="bar">
       <button id="prev">預覽</button>
       <button id="save" class="primary">寫入裁定</button>
       <span id="msg" class="muted"></span>
     </div>
     <table><thead><tr><th style="width:46%">詞（拖拽排序）</th><th class="wt">頻分</th><th>來源</th></tr></thead>
     <tbody id="rows"><tr><td colspan="3" class="muted">先在上方輸入碼或詞</td></tr></tbody></table>
     <div id="ovs" style="margin-top:12px"></div>
   </div>
   <div class="card">
     <h3>加詞（與輸入法裏的 /add 共用同一個暫存，攢批後一起入庫）</h3>
     <textarea id="newword" rows="2" placeholder="輸入 2–4 字漢語詞，多個用空格或換行分隔"></textarea>
     <div class="bar" style="margin-top:8px">
       <button id="addbtn" class="primary">加入暫存</button>
       <span id="addmsg" class="muted"></span>
     </div>
   </div>
 </section>

 <section id="pane-dict" class="hide">
   <div class="card">
     <h3>字典（十九萬六千條，服務端分頁）</h3>
     <div class="bar">
       <input id="dw" type="text" placeholder="詞含…" style="width:170px">
       <input id="dc" type="text" placeholder="碼前綴…" style="width:120px">
       <select id="dcl"><option value="">全部碼</option>
         <option value="short">簡碼（1–3）</option><option value="full">全碼（4）</option>
         <option value="1">一碼</option><option value="2">二簡</option><option value="3">三碼</option></select>
       <select id="dl"><option value="">字數不限</option><option value="1">單字</option>
         <option value="2">二字</option><option value="3">三字</option><option value="4">四字</option>
         <option value="other">五字以上</option></select>
       <select id="ds"><option value="">來源不限</option><option value="hp">手收</option>
         <option value="tag">有標記</option><option value="plain">無標記且非手收</option>
         <option value="drop">已標記棄用</option></select>
       <select id="dsec"><option value="">全部區段</option><option value="主體">主體</option>
         <option value="併入區">併入區</option></select>
       <select id="dso"><option value="line">按行號</option><option value="code">按編碼</option>
         <option value="weight">按頻分</option><option value="word">按詞形</option></select>
       <label class="muted" style="display:flex;gap:4px;align-items:center">
         <input type="checkbox" id="dgrp" checked>合併多碼詞</label>
       <button id="dgo" class="primary">查詢</button>
       <span id="dstats" class="stats"></span>
     </div>
     <div class="scroll">
       <table><thead><tr><th style="width:20%">詞</th><th style="width:30%">編碼（簡碼 · 全碼）</th>
         <th class="wt">頻分</th><th style="width:8%">字數</th><th style="width:14%">標記</th>
         <th class="wt" style="width:8%">行</th><th style="width:10%">操作</th></tr></thead>
         <tbody id="drows"></tbody></table>
     </div>
     <div class="bar" style="margin-top:10px">
       <button id="dfirst">« 首頁</button>
       <button id="dprev">‹ 上頁</button>
       <span id="dpage" class="muted"></span>
       <button id="dnext">下頁 ›</button>
       <button id="dlast">末頁 »</button>
       <span class="muted">點一行可跳到「調序」看該碼組</span>
     </div>
   </div>
 </section>

 <aside id="pane-side">
   <div class="card">
     <h3>待應用</h3>
     <div id="pend" class="muted">載入中…</div>
     <div class="bar" style="margin-top:10px">
       <button id="apply" class="danger" disabled>應用（管道＋部署）…</button>
     </div>
     <div class="muted" style="font-size:12px">應用會重跑詞頻管道、過 44 碼閘門、重啟輸入法引擎。</div>
   </div>
   <div class="card">
     <h3>執行日誌</h3>
     <pre id="out" style="margin:0">—</pre>
   </div>
   <div class="card">
     <h3>最近動態（台賬）</h3>
     <div id="recent" class="muted">載入中…</div>
   </div>
 </aside>
</main>

<dialog id="dlg">
  <div class="dlg-h">確認應用？這會改詞庫並重啟引擎</div>
  <div class="dlg-b">
    <div id="dlgsum"></div>
    <p>確定要執行，請在下面輸入 <b>APPLY</b>（大寫）：</p>
    <input id="dlginput" placeholder="APPLY" autocomplete="off">
    <div id="dlgerr" class="err" style="margin-top:8px"></div>
  </div>
  <div class="dlg-f">
    <button id="dlgcancel">取消</button>
    <button id="dlgok" class="danger" disabled>確認應用</button>
  </div>
</dialog>

<script>
let CODE='', SEQ=[], DRAG=null, TOKEN=null;
let DPAGE=1, DPAGES=1;
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function api(path,opt){opt=opt||{};opt.headers=Object.assign({'X-Wishd':'1'},opt.headers||{});
  return fetch(path,opt).then(r=>r.json());}
function say(t,cls){$('#msg').textContent=t;$('#msg').className=cls||'muted';}
function show(o){$('#out').textContent=typeof o==='string'?o:JSON.stringify(o,null,1);}

/* ── 分頁籤 ── */
function tab(which){
  const order=which==='order';
  $('#tab-order').classList.toggle('on',order);
  $('#tab-dict').classList.toggle('on',!order);
  $('#pane-order').classList.toggle('hide',!order);
  $('#pane-dict').classList.toggle('hide',order);
  $('#pane-side').classList.toggle('hide',!order);
  $('#main').classList.toggle('dictmode',!order);
  $('#hint').textContent=order?'只寫意願通道，永不直接改詞典':'唯讀視圖：字典瀏覽不會改動任何東西';
  if(!order&&!$('#drows').children.length)dictQuery(1);
}
$('#tab-order').onclick=()=>tab('order');
$('#tab-dict').onclick=()=>tab('dict');

/* ── 同碼組 ── */
function renderGroup(d){
  CODE=d.code;
  $('#gtitle').textContent=d.exists?('碼組 '+d.code+'（'+d.members.length+' 詞）'):('碼組 '+d.code+' 不存在');
  const tb=$('#rows');tb.innerHTML='';SEQ=d.members.map(m=>m.w);
  d.members.forEach(m=>{
    const tr=document.createElement('tr');tr.draggable=true;tr.dataset.w=m.w;
    if(m.hp)tr.classList.add('hp');
    tr.innerHTML='<td class="w">'+esc(m.w)+'</td><td class="wt">'+(m.wt||'—')+'</td>'
                +'<td class="tag">'+(m.hp?'手收':'')+' '+esc(m.tag||'')+'</td>';
    tr.addEventListener('dragstart',()=>{DRAG=tr;tr.classList.add('drag');});
    tr.addEventListener('dragend',()=>{tr.classList.remove('drag');DRAG=null;reorder();});
    tr.addEventListener('dragover',e=>{e.preventDefault();tr.classList.add('over');});
    tr.addEventListener('dragleave',()=>tr.classList.remove('over'));
    tr.addEventListener('drop',e=>{e.preventDefault();tr.classList.remove('over');
      if(DRAG&&DRAG!==tr){const r=[...tb.children];
        r.indexOf(DRAG)<r.indexOf(tr)?tr.after(DRAG):tr.before(DRAG);}});
    tb.appendChild(tr);
  });
  $('#ovs').innerHTML=d.overrides.length?('<h3>既有裁定</h3>'+d.overrides.map(o=>
    '<pre style="background:#f5f5f5;color:#333;max-height:none">第'+o.line+'行　'+esc(o.raw)+'</pre>').join('')):'';
  say('');
}
function reorder(){SEQ=[...$('#rows').children].map(t=>t.dataset.w);}
function load(key){
  const t=/^[a-z]{1,4}$/i.test(key)?'/api/group?code='+key:'/api/search?q='+encodeURIComponent(key);
  api(t).then(d=>{
    if(d.members)return renderGroup(d);
    if(d.hits&&d.hits.length){
      const c=d.hits[0].codes;
      if(c.length===1)return api('/api/group?code='+c[0]).then(renderGroup);
      show(d.hits.length+' 個匹配；第一條 '+d.hits[0].w+' 有多碼 '+c.join('/')+'，用碼查更精確');
    } else say('查不到：'+key,'err');
  });
}
$('#go').onclick=()=>{const k=$('#q').value.trim();
  if($('#tab-dict').classList.contains('on')){$('#dw').value=k;$('#dc').value='';dictQuery(1);}
  else{tab('order');load(k);}};
$('#q').addEventListener('keydown',e=>{if(e.key==='Enter')$('#go').click();});

$('#prev').onclick=()=>{reorder();
  api('/api/order',{method:'POST',body:JSON.stringify({code:CODE,seq:SEQ})}).then(d=>{
    if(!d.ok)return show(d);
    show({目標次序:d.target.join('  >  '),目前次序:d.currentFiltered.join('  >  '),
          警告:d.warnings,將被取代:d.conflicts});
    say(d.warnings.length?'有警告':'可以寫入',d.warnings.length?'warn':'ok');});};

$('#save').onclick=()=>{reorder();
  if(!confirm('寫入次序裁定？\n\n'+SEQ.join(' > ')))return;
  api('/api/commit',{method:'POST',body:JSON.stringify({code:CODE,seq:SEQ})}).then(d=>{
    if(!d.ok){show(d);return say('校驗未過','err');}
    say('已寫入（取代 '+d.replaced+' 條）','ok');load(CODE);refreshPending();});};

$('#addbtn').onclick=()=>{
  const words=$('#newword').value.split(/[\s,、]+/).filter(Boolean);
  if(!words.length)return;
  api('/api/add',{method:'POST',body:JSON.stringify({words})}).then(d=>{
    const ok=d.added||[],sk=d.skipped||[];
    $('#addmsg').innerHTML=(ok.length?('<span class="ok">＋'+esc(ok.join('、'))+'</span> '):'')
      +(sk.length?('<span class="warn">−'+sk.map(s=>esc(s.w)+'（'+esc(s.why)+'）').join('；')+'</span>'):'');
    if(ok.length)$('#newword').value='';
    refreshPending();});};

/* ── 字典 ── */
function dictQuery(page){
  DPAGE=page||1;
  const p=new URLSearchParams({word:$('#dw').value.trim(),code:$('#dc').value.trim(),
    len:$('#dl').value,clen:$('#dcl').value,src:$('#ds').value,sec:$('#dsec').value,sort:$('#dso').value,
    group:$('#dgrp').checked?'1':'0',page:DPAGE,size:100});
  api('/api/dict?'+p).then(d=>{
    DPAGES=d.pages;
    $('#dpage').textContent=' 第 '+d.page+' / '+d.pages+' 頁，共 '+d.total.toLocaleString()+' 條 ';
    const cell=r=>r.codes.map(c=>c===r.primary
        ?'<code style="font-weight:600">'+esc(c)+'</code>'
        :'<code style="background:#f5f5f5;color:#78909c">'+esc(c)+'</code>').join(' ');
    $('#drows').innerHTML=d.rows.length?d.rows.map(r=>
      '<tr class="clickable" data-c="'+esc(r.primary)+'" data-w="'+esc(r.w)+'">'
      +'<td>'+esc(r.w)+'</td><td>'+cell(r)+'</td>'
      +'<td class="wt">'+(r.wt||'—')+'</td><td>'+[...r.w].length+'</td>'
      +'<td class="tag">'+(r.hp?'手收 ':'')+esc((r.tag||'').slice(0,14))
        +(r.dropped?'<span class="err"> 已標記棄用</span>':'')+'</td>'
      +'<td class="wt" title="'+(r.lines||[]).join(', ')+'">'+(r.lines&&r.lines.length>1?r.lines[0]+'+'+(r.lines.length-1):r.line)+'</td>'
      +'<td>'+(r.dropped?'<span class="muted">待應用</span>'
        :'<button class="dropbtn" data-w="'+esc(r.w)+'" style="padding:2px 8px;font-size:12px">棄用</button>')
      +'</td></tr>').join('')
      :'<tr><td colspan="7" class="muted">無匹配</td></tr>';
    [...$('#drows').children].forEach(tr=>{
      if(!tr.dataset.c)return;
      tr.onclick=e=>{if(e.target.classList.contains('dropbtn'))return;tab('order');load(tr.dataset.c);};
      const b=tr.querySelector('.dropbtn');
      if(b)b.onclick=e=>{e.stopPropagation();dropOne(b.dataset.w,tr.dataset.c);};});
  });
}
['#dgo'].forEach(s=>$(s).onclick=()=>dictQuery(1));
['#dw','#dc'].forEach(s=>$(s).addEventListener('keydown',e=>{if(e.key==='Enter')dictQuery(1);}));
['#dl','#dcl','#ds','#dsec','#dso','#dgrp'].forEach(s=>$(s).onchange=()=>dictQuery(1));
$('#dfirst').onclick=()=>dictQuery(1);
$('#dprev').onclick=()=>dictQuery(Math.max(1,DPAGE-1));
$('#dnext').onclick=()=>dictQuery(Math.min(DPAGES,DPAGE+1));
$('#dlast').onclick=()=>dictQuery(DPAGES);
api('/api/dict/stats').then(s=>{
  const L=s.by_len;
  $('#dstats').innerHTML='共 <b>'+s.total.toLocaleString()+'</b> 條｜單字 <b>'+(L['1']||0)+
    '</b>｜二字 <b>'+(L['2']||0)+'</b>｜四字 <b>'+(L['4']||0)+'</b>｜手收 <b>'+s.handpicked+
    '</b>｜有標記 <b>'+s.tagged+'</b>';
});

/* ── 就地棄用 ── */
function dropOne(w, code){
  const msg='要棄用「'+w+'」嗎？\n\n'
    +'• 從詞庫刪除（handpicked 寫 -詞）\n'
    +'• 同時寫入 rejected，防止補詞階段從語料撈回來\n'
    +'• 改動要按「應用」才生效；可 git checkout 撤銷';
  if(!confirm(msg))return;
  api('/api/drop',{method:'POST',body:JSON.stringify({words:[w]})}).then(d=>{
    if(d.skipped&&d.skipped.length){alert('未處理：'+d.skipped.map(s=>s.w+'（'+s.why+'）').join('；'));return;}
    refreshPending();dictQuery(DPAGE);
  });
}

/* ── 待應用 ── */
function refreshPending(){
  api('/api/pending').then(p=>{
    let h='<div>暫存詞 '+p.spool.length+'：</div>';
    h+=p.spool.length?('<div class="spool">'+p.spool.map(w=>'<span class="chip">'+esc(w)+'</span>').join('')+'</div>')
      :'<div class="muted">（空）</div>';
    const f=[];
    if(p.overrides_changed)f.push('次序裁定已改動');
    if(p.handpicked_changed)f.push('手收清單已改動');
    if(f.length)h+='<div class="warn" style="margin-top:8px">⚠ '+f.join('、')+'——尚未重跑管道</div>';
    if(!p.spool.length&&!f.length)h+='<div class="muted" style="margin-top:8px">沒有待應用的變更</div>';
    $('#pend').innerHTML=h;
    $('#apply').disabled=!p.count;
  });
}

/* ── 應用：二次確認 ── */
$('#apply').onclick=()=>{
  api('/api/apply-token',{method:'POST',body:'{}'}).then(t=>{
    TOKEN=t.token;
    const p=t.pending, li=[];
    if(p.spool.length)li.push('暫存詞 '+p.spool.length+' 個將併入手收清單：<b>'+esc(p.spool.join('、'))+'</b>');
    if(p.overrides_changed)li.push('次序裁定已改動，將重跑詞頻管道');
    if(p.handpicked_changed)li.push('手收清單已改動，將重跑詞頻管道');
    $('#dlgsum').innerHTML='<ul>'+li.map(x=>'<li>'+x+'</li>').join('')+'</ul>'
      +'<p class="muted">詞典會原子重寫；44 碼閘門不過則中止部署；引擎重啟期間輸入法短暫不可用。</p>';
    $('#dlginput').value='';$('#dlgok').disabled=true;$('#dlgerr').textContent='';
    $('#dlg').showModal();$('#dlginput').focus();
  });
};
$('#dlginput').oninput=e=>{$('#dlgok').disabled=e.target.value!=='APPLY';};
$('#dlgcancel').onclick=()=>$('#dlg').close();
$('#dlgok').onclick=()=>{
  $('#dlgok').disabled=true;
  api('/api/apply',{method:'POST',body:JSON.stringify({token:TOKEN,confirm:'APPLY'})})
   .then(d=>{
     if(!d.ok){$('#dlgerr').textContent=d.error||'失敗';$('#dlgok').disabled=false;return;}
     $('#dlg').close();say('執行中…','muted');
     const tick=setInterval(()=>api('/api/job').then(j=>{
       $('#out').textContent=j.log||'…';
       if(j.state!=='running'){clearInterval(tick);
         say(j.state==='done'?'完成':'失敗',j.state==='done'?'ok':'err');
         refreshPending();if(CODE)load(CODE);if($('#drows').children.length)dictQuery(DPAGE);}
     }),900);
   });
};

function loadRecent(){
  api('/api/recent').then(d=>{
    $('#recent').innerHTML=d.rows.length?d.rows.map(r=>
      '<div style="font-size:12px;line-height:1.5"><span class="muted">'
      +esc((r.ts||'').slice(5,16).replace('T',' '))+'</span> '
      +'<b>'+esc(r.action)+'</b> <code style="font-size:11px">'+esc(r.path)+'</code>'
      +'<div class="muted" style="padding-left:4px">'+esc(r.detail)+'</div></div>').join('')
      :'<span class="muted">（無記錄）</span>';
  });
}
refreshPending();loadRecent();
</script></html>"""


def main():
    groups, _ = wishd.load_dict()
    print(f'詞庫意願台：{sum(len(v) for v in groups.values())} 條 / {len(groups)} 個碼組')
    print(f'   http://{HOST}:{PORT}')
    print('   只綁 127.0.0.1——共享機器勿開。應用需二次確認（token + 明文 APPLY）。Ctrl-C 結束。')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
