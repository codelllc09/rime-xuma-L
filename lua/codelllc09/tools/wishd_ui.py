#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wishd_ui.py — 詞庫意願台（Web，2026-11-05）

本機單頁界面，用來直觀調同碼組的候選次序。寫入一律走 wishd.py 的校驗，
不另開一套邏輯——避免繞過護欄。

啟動：
    cd lua/codelllc09/tools
    python3 wishd_ui.py            # 瀏覽器開 http://127.0.0.1:8721

安全：
  · 只綁 127.0.0.1（**共享機器勿開**：這是一個能改詞庫並重跑管道的服務）
  · POST 必須帶 `X-Wishd: 1` 標頭——跨站請求帶不上自訂標頭且本服務不發 CORS 標頭，
    故可擋 CSRF；GET 全部只讀
  · 不提供「直接編輯 xuma_L.dict.yaml」——生成物，只寫意願通道
"""
import json
import os
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

JOB = {'state': 'idle', 'log': '', 'started': 0.0}
JOB_LOCK = threading.Lock()


# ── 管線任務（背景執行，前端輪詢）─────────────────────────────
def run_apply(no_deploy=False):
    def work():
        t0 = time.time()
        with JOB_LOCK:
            JOB.update(state='running', log='', started=t0)
        def log(s):
            with JOB_LOCK:
                JOB['log'] += s
        try:
            log('① 重跑詞頻管道……\n')
            r = subprocess.run([sys.executable, os.path.join(HERE, 'gen_freq_dict.py')],
                               cwd=HERE, capture_output=True, text=True)
            tail = '\n'.join([l for l in r.stdout.splitlines()
                              if l.startswith('[overrides') or '寫出' in l][-20:])
            log(tail + '\n')
            if r.returncode != 0:
                log('✗ 管道失敗\n' + r.stderr[-1500:])
                with JOB_LOCK:
                    JOB['state'] = 'failed'
                return
            log('② 部署前閘門：44 碼首選回歸……\n')
            a = subprocess.run([sys.executable, os.path.join(HERE, 'check_acceptance.py')],
                               cwd=HERE, capture_output=True, text=True)
            if a.returncode != 0:
                log('✗ 首選回歸未過，**中止部署**（git checkout 可回退詞庫）\n' + a.stdout[-800:])
                with JOB_LOCK:
                    JOB['state'] = 'failed'
                return
            log('✓ 閘門通過\n')
            if not no_deploy:
                log('③ 部署（ibus restart）……\n')
                subprocess.run(['ibus', 'restart'], check=False)
                log('完成。等幾秒讓引擎起來。\n')
            with JOB_LOCK:
                JOB['state'] = 'done'
                JOB['elapsed'] = round(time.time() - t0, 1)
        except Exception as e:  # 不讓異常吞掉狀態
            with JOB_LOCK:
                JOB['state'] = 'failed'
                JOB['log'] += f'✗ 例外：{e}'
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
            ovs.append((ln, ws, raw))
    return {
        'code': code,
        'exists': bool(rows),
        'members': [{'w': w, 'wt': wt, 'line': ln,
                     'hp': w in hp, 'tag': (t or '').lstrip('# ')} for w, wt, ln, t in rows],
        'overrides': [{'line': ln, 'words': ws, 'raw': raw.strip()} for ln, ws, raw in ovs],
    }


def api_search(q):
    _, wc = wishd.load_dict()
    hits = [(w, sorted(c)) for w, c in wc.items() if q and q in w]
    hits.sort(key=lambda x: (len(x[0]), x[0]))
    return {'hits': [{'w': w, 'codes': c} for w, c in hits[:60]], 'total': len(hits)}


def api_suspicious():
    groups, _ = wishd.load_dict()
    hp = wishd.load_handpicked()
    rows = wishd.suspicious(groups, hp, limit=80)
    return {'rows': [{'code': c, 'kind': k, 'wt': wt, 'words': ws} for c, k, ws, wt in rows]}


def api_order(payload):
    code = (payload.get('code') or '').lower()
    seq = payload.get('seq') or []
    groups, wc = wishd.load_dict()
    ok, err, warn = wishd.check_order(code, seq, groups, wc)
    conf = [{'line': ln, 'raw': raw.strip()} for ln, raw in wishd.existing_conflicts(seq)] if ok else []
    # 目標次序 vs 現狀（現狀＝詞典行序，已是歷史裁定生效後的結果）
    cur = [w for w, *_ in groups.get(code, [])]
    cur_f = [w for w in cur if w in seq]
    return {'ok': ok, 'errors': err, 'warnings': warn, 'conflicts': conf,
            'current': cur, 'currentFiltered': cur_f, 'target': seq}


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
    server_version = 'wishd/1.0'

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):
        pass   # 靜默

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path in ('/', '/index.html'):
            return self._send(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8')
        if u.path == '/api/group':
            return self._send(200, json.dumps(api_group(q.get('code', [''])[0]), ensure_ascii=False))
        if u.path == '/api/search':
            return self._send(200, json.dumps(api_search(q.get('q', [''])[0]), ensure_ascii=False))
        if u.path == '/api/suspicious':
            return self._send(200, json.dumps(api_suspicious(), ensure_ascii=False))
        if u.path == '/api/job':
            with JOB_LOCK:
                return self._send(200, json.dumps(dict(JOB), ensure_ascii=False))
        return self._send(404, json.dumps({'error': 'not found'}))

    def do_POST(self):
        # CSRF：跨站表單帶不上自訂標頭；本服務不發 CORS 標頭，瀏覽器不放行跨源自訂標頭
        if self.headers.get('X-Wishd') != '1':
            return self._send(403, json.dumps({'error': 'missing X-Wishd header'}))
        n = int(self.headers.get('Content-Length') or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b'{}')
        except json.JSONDecodeError:
            return self._send(400, json.dumps({'error': 'bad json'}))
        try:
            return self._dispatch_post(payload)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return self._send(500, json.dumps({'error': str(e)}, ensure_ascii=False))

    def _dispatch_post(self, payload):
        if self.path == '/api/order':
            return self._send(200, json.dumps(api_order(payload), ensure_ascii=False))
        if self.path == '/api/commit':
            return self._send(200, json.dumps(api_commit(payload), ensure_ascii=False))
        if self.path == '/api/apply':
            with JOB_LOCK:
                if JOB['state'] == 'running':
                    return self._send(409, json.dumps({'error': 'already running'}))
            run_apply(no_deploy=bool(payload.get('no_deploy')))
            return self._send(200, json.dumps({'ok': True}))
        return self._send(404, json.dumps({'error': 'not found'}))


PAGE = r"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>詞庫意願台</title>
<style>
 body{font:14px/1.6 -apple-system,"Noto Sans CJK TC",sans-serif;margin:0;background:#f6f7f9;color:#222}
 header{background:#263238;color:#fff;padding:10px 16px;display:flex;gap:12px;align-items:center}
 header input{font:inherit;padding:5px 10px;border:0;border-radius:4px;width:280px}
 main{display:grid;grid-template-columns:1fr 380px;gap:16px;padding:16px}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:14px}
 h3{margin:0 0 10px;font-size:14px;color:#37474f}
 table{width:100%;border-collapse:collapse}
 td,th{padding:5px 8px;border-bottom:1px solid #eee;text-align:left;font-size:13px}
 tr.drag{opacity:.35}
 tr.over{outline:2px dashed #1565c0}
 .w{cursor:grab;user-select:none}
 .wt{color:#888;font-variant-numeric:tabular-nums;text-align:right}
 .tag{color:#00695c;font-size:12px}
 .hp{background:#e8f5e9}
 button{font:inherit;padding:6px 12px;border:1px solid #cfd8dc;background:#fff;border-radius:6px;cursor:pointer}
 button.primary{background:#1565c0;color:#fff;border-color:#1565c0}
 button:disabled{opacity:.5;cursor:not-allowed}
 pre{background:#263238;color:#b2ff59;padding:10px;border-radius:6px;overflow:auto;max-height:280px;font-size:12px}
 .warn{color:#b26a00}.err{color:#c62828}.ok{color:#2e7d32}
 .muted{color:#90a4ae}
 #bar{display:flex;gap:8px;align-items:center;margin-bottom:10px}
</style>
<header>
  <strong>詞庫意願台</strong>
  <input id="q" placeholder="輸入碼（如 oooo）或詞（如 合適）…" autofocus>
  <button id="go" class="primary">查</button>
  <span class="muted" style="margin-left:auto">只寫意願通道，永不直接改詞典</span>
</header>
<main>
 <section class="card">
   <h3 id="gtitle">同碼組</h3>
   <div id="bar">
     <button id="prev">預覽</button>
     <button id="save" class="primary">寫入裁定</button>
     <button id="apply">應用（跑管道＋部署）</button>
     <span id="msg" class="muted"></span>
   </div>
   <table><thead><tr><th style="width:46%">詞（拖拽排序）</th><th class="wt">頻分</th><th>來源</th></tr></thead>
   <tbody id="rows"><tr><td colspan="3" class="muted">先在上方輸入碼或詞</td></tr></tbody></table>
   <div id="ovs" style="margin-top:12px"></div>
 </section>
 <aside>
   <div class="card" style="margin-bottom:16px">
     <h3>預覽 / 結果</h3>
     <pre id="out" style="margin:0">—</pre>
   </div>
   <div class="card">
     <h3>可疑組（同分並列＝可能是異體重複）</h3>
     <div id="susp" class="muted">載入中…</div>
   </div>
 </aside>
</main>
<script>
let CODE = '', SEQ = [], DRAG = null;
const $ = s => document.querySelector(s);

function api(path, opt) {
  opt = opt || {};
  opt.headers = Object.assign({'X-Wishd': '1'}, opt.headers || {});
  return fetch(path, opt).then(r => r.json());
}
function say(t, cls) { $('#msg').textContent = t; $('#msg').className = cls || 'muted'; }

function renderGroup(d) {
  CODE = d.code;
  $('#gtitle').textContent = d.exists ? `碼組 ${d.code}（${d.members.length} 詞）` : `碼組 ${d.code} 不存在`;
  SEQ = d.members.map(m => m.w);
  const tb = $('#rows'); tb.innerHTML = '';
  d.members.forEach(m => {
    const tr = document.createElement('tr');
    tr.draggable = true; tr.dataset.w = m.w;
    if (m.hp) tr.classList.add('hp');
    tr.innerHTML = `<td class="w">${m.w}</td><td class="wt">${m.wt || '—'}</td>
                    <td class="tag">${m.hp ? '手收' : ''} ${m.tag || ''}</td>`;
    tr.addEventListener('dragstart', e => { DRAG = tr; tr.classList.add('drag'); });
    tr.addEventListener('dragend', () => { tr.classList.remove('drag'); DRAG = null; reorder(); });
    tr.addEventListener('dragover', e => { e.preventDefault(); tr.classList.add('over'); });
    tr.addEventListener('dragleave', () => tr.classList.remove('over'));
    tr.addEventListener('drop', e => {
      e.preventDefault(); tr.classList.remove('over');
      if (DRAG && DRAG !== tr) {
        const rows = [...tb.children];
        rows.indexOf(DRAG) < rows.indexOf(tr) ? tr.after(DRAG) : tr.before(DRAG);
      }
    });
    tb.appendChild(tr);
  });
  $('#ovs').innerHTML = d.overrides.length
    ? '<h3>既有裁定</h3>' + d.overrides.map(o =>
        `<pre style="background:#f5f5f5;color:#333;max-height:none">第${o.line}行　${o.raw}</pre>`).join('')
    : '';
  say('');
}
function reorder() { SEQ = [...$('#rows').children].map(tr => tr.dataset.w); }

function show(obj) { $('#out').textContent = JSON.stringify(obj, null, 1); }

function load(key) {
  const t = /^[a-z]{1,4}$/i.test(key) ? '/api/group?code=' + key
                                      : '/api/search?q=' + encodeURIComponent(key);
  api(t).then(d => {
    if (d.members) return renderGroup(d);
    if (d.hits && d.hits.length) {
      const codes = d.hits[0].codes;
      if (codes.length === 1) return api('/api/group?code=' + codes[0]).then(renderGroup);
      show(d.hits.length + ' 個匹配，第一條有多碼：' + d.hits[0].w + ' → ' + codes.join('/') + '（用碼查更精確）');
    } else { say('查不到：' + key, 'err'); }
  });
}

$('#go').onclick = () => load($('#q').value.trim());
$('#q').addEventListener('keydown', e => { if (e.key === 'Enter') $('#go').click(); });

$('#prev').onclick = () => {
  reorder();
  api('/api/order', {method: 'POST', body: JSON.stringify({code: CODE, seq: SEQ})})
    .then(d => {
      if (!d.ok) return show(d);
      const tgt = d.target.join('  >  ');
      show({目標次序: tgt, 目前次序: d.currentFiltered.join('  >  '),
            警告: d.warnings, 將被取代: d.conflicts});
      say(d.warnings.length ? '有警告' : '可以寫入', d.warnings.length ? 'warn' : 'ok');
    });
};

$('#save').onclick = () => {
  reorder();
  if (!confirm('寫入次序裁定？\n\n' + SEQ.join(' > '))) return;
  api('/api/commit', {method: 'POST', body: JSON.stringify({code: CODE, seq: SEQ})})
    .then(d => {
      if (!d.ok) { show(d); return say('校驗未過', 'err'); }
      say('已寫入（取代 ' + d.replaced + ' 條）', 'ok');
      load(CODE);
    });
};

$('#apply').onclick = () => {
  if (!confirm('跑詞頻管道並部署？約 10–60 秒。')) return;
  api('/api/apply', {method: 'POST', body: '{}'}).then(() => {
    say('執行中…', 'muted');
    const tick = setInterval(() => {
      api('/api/job').then(j => {
        $('#out').textContent = j.log || '…';
        if (j.state !== 'running') {
          clearInterval(tick);
          say(j.state === 'done' ? '完成' : '失敗', j.state === 'done' ? 'ok' : 'err');
          if (j.state === 'done') load(CODE);
        }
      });
    }, 900);
  });
};

api('/api/suspicious').then(d => {
  $('#susp').innerHTML = d.rows.length
    ? d.rows.map(r => `<div style="cursor:pointer" onclick="load('${r.code}')">
        <code>${r.code}</code> ${r.words.join(' / ')} <span class="muted">=${r.wt}</span></div>`).join('')
    : '（無）';
});
</script></html>"""


def main():
    groups, _ = wishd.load_dict()
    print(f'詞庫意願台：{sum(len(v) for v in groups.values())} 條 / {len(groups)} 個碼組')
    print(f'   http://{HOST}:{PORT}')
    print('   只綁 127.0.0.1——共享機器勿開。Ctrl-C 結束。')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
