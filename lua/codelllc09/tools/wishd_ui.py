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
        if u.path == '/api/suspicious':
            return self._send(200, json.dumps(api_suspicious(), ensure_ascii=False))
        if u.path == '/api/job':
            with JOB_LOCK:
                return self._send(200, json.dumps(dict(JOB), ensure_ascii=False))
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
 main{display:grid;grid-template-columns:1fr 400px;gap:16px;padding:16px}
 .card{background:#fff;border:1px solid #e3e6ea;border-radius:8px;padding:14px;margin-bottom:16px}
 h3{margin:0 0 10px;font-size:14px;color:#37474f}
 table{width:100%;border-collapse:collapse}
 td,th{padding:5px 8px;border-bottom:1px solid #eee;text-align:left;font-size:13px}
 tr.drag{opacity:.35} tr.over{outline:2px dashed #1565c0}
 .w{cursor:grab;user-select:none}
 .wt{color:#888;font-variant-numeric:tabular-nums;text-align:right}
 .tag{color:#00695c;font-size:12px} .hp{background:#e8f5e9}
 button{font:inherit;padding:6px 12px;border:1px solid #cfd8dc;background:#fff;border-radius:6px;cursor:pointer}
 button.primary{background:#1565c0;color:#fff;border-color:#1565c0}
 button.danger{background:#c62828;color:#fff;border-color:#c62828}
 button:disabled{opacity:.5;cursor:not-allowed}
 pre{background:#263238;color:#b2ff59;padding:10px;border-radius:6px;overflow:auto;max-height:260px;font-size:12px}
 textarea{font:inherit;width:100%;box-sizing:border-box;padding:6px;border:1px solid #cfd8dc;border-radius:6px;resize:vertical}
 .warn{color:#b26a00} .err{color:#c62828} .ok{color:#2e7d32} .muted{color:#90a4ae}
 .bar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
 .spool{display:flex;flex-wrap:wrap;gap:6px}
 .chip{background:#e3f2fd;border-radius:12px;padding:2px 10px;font-size:13px}
 dialog{border:0;border-radius:10px;padding:0;max-width:520px;width:92vw;box-shadow:0 12px 40px rgba(0,0,0,.3)}
 dialog::backdrop{background:rgba(0,0,0,.45)}
 .dlg-h{background:#c62828;color:#fff;padding:12px 16px;font-weight:600}
 .dlg-b{padding:16px} .dlg-f{padding:0 16px 16px;display:flex;gap:8px;justify-content:flex-end}
 dialog input{font:inherit;padding:8px;border:1px solid #cfd8dc;border-radius:6px;width:100%;box-sizing:border-box}
 ul{margin:6px 0;padding-left:20px}
</style>
<header>
  <strong>詞庫意願台</strong>
  <input id="q" placeholder="輸入碼（如 oooo）或詞（如 合適）…" autofocus>
  <button id="go" class="primary">查</button>
  <span class="muted" style="margin-left:auto">只寫意願通道，永不直接改詞典</span>
</header>
<main>
 <section>
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
 <aside>
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
     <h3>可疑組（同分並列＝可能是異體重複）</h3>
     <div id="susp" class="muted">載入中…</div>
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
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function api(path,opt){opt=opt||{};opt.headers=Object.assign({'X-Wishd':'1'},opt.headers||{});
  return fetch(path,opt).then(r=>r.json());}
function say(t,cls){$('#msg').textContent=t;$('#msg').className=cls||'muted';}
function show(o){$('#out').textContent=typeof o==='string'?o:JSON.stringify(o,null,1);}

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
$('#go').onclick=()=>load($('#q').value.trim());
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
         refreshPending();if(CODE)load(CODE);}
     }),900);
   });
};

api('/api/suspicious').then(d=>{
  $('#susp').innerHTML=d.rows.length?d.rows.map(r=>
    '<div style="cursor:pointer" onclick="load(\''+r.code+'\')"><code>'+r.code+'</code> '
    +esc(r.words.join(' / '))+' <span class="muted">='+r.wt+'</span></div>').join(''):'（無）';
});
refreshPending();
</script></html>"""


def main():
    groups, _ = wishd.load_dict()
    print(f'詞庫意願台：{sum(len(v) for v in groups.values())} 條 / {len(groups)} 個碼組')
    print(f'   http://{HOST}:{PORT}')
    print('   只綁 127.0.0.1——共享機器勿開。應用需二次確認（token + 明文 APPLY）。Ctrl-C 結束。')
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
