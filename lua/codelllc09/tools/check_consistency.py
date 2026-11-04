#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_consistency.py — 倉庫一致性機械門禁（2026-11-05，隨「只讀第三方審查」條文入庫）

用法：cd lua/codelllc09/tools && python3 check_consistency.py [--determinism]

機檢電池（任一 FAIL 即非零退出；依賴缺失按 SKIP 通報，不判敗）：
  ① 白名單電池：已跟蹤文件不得被忽略（--no-index 純模式判定，防「靠跟蹤苟活」）；
     .gitignore 逐條 ! 行必須生效（glob 白名單行屬字面傳參，其真實覆蓋由①的跟蹤電池兜底）
  ② 未來日期：跟蹤文件出現晚於今日的日期即敗（「答辯標 06 實爲 05」類漂移的機檢）
  ③ 校驗單對賬：std_tables/SHA256SUMS（跟蹤，必須全過）；
     corpus sources（未跟蹤，全缺＝無語料模式 SKIP；存在則須全過）
  ④ HEAD 煙測：git worktree 檢出 HEAD → check_acceptance＋gen_no_simple --verify
     ——「fresh clone 雙檢必掛」級 P0（如基準文件被 ignore 漏跟蹤）的唯一機檢防線
  ⑤ 雙檢編排：check_acceptance＋gen_no_simple --verify＋check_tendency（語料在則跑）
  ⑥ 留痕靜態電池：凡寫盤腳本必須有 _ledger 調用，或在豁免清單中聲明理由
     ——「所有需要用日誌的行爲都有日誌」的機械保障（未聲明的寫盤者即敗）
  ⑦ 溯源電池：PROVENANCE.json 中每個產物的輸入指紋須與現狀一致（輸入漂移即敗，
     說明產物與輸入的對應關係已斷——要麼重跑生成器，要麼確認變更已入庫）
  ⑧ 台賬衛生：IGNORED_CHANGES.log 四欄完整／時間非降／與 events.jsonl 雙檔對賬
  ⑫ 鉤子有效性：core.hooksPath 已設時，pre-commit／post-commit／pre-push 三件必須
     存在且可執行。「鉤子檔在」不等於「鉤子會跑」——git 對不可執行的鉤子只給一行
     hint 就跳過（2026-11-05 實例：2 個提交因 post-commit 未置可執行而漏了快照 ref）。
     未設 hooksPath（淨室克隆／CI）屬正常，SKIP。
  ⑩ 對象庫完整性：git fsck --connectivity-only 零錯誤（缺對象／壞對象是恢復期的
     致命傷，且發現得越早越好；0.01s，代價可忽略）
  ⑨ 鏡像覆蓋：本地裸鏡像（backup.sh mirror-*）須含當前 HEAD——鏡像是歷史改寫類
     事故（filter-repo／reset --hard／gc --prune=now）唯一不依賴自律的防線；
     未建鏡像時 SKIP（淨室克隆／CI 屬正常）
  ⑪ 確定性（--determinism，默認關；backup.sh drill 會自動觸發）：雙 PYTHONHASHSEED 復跑管道——
     兩次 stdout 與產出文件字節摘要皆須一致（seed 不變性即過）；產出相對已提交
     版本的漂移（隔日日期戳屬預期）僅告警不判敗，實質變化走詞表改動流程複覈後提交

原則：機械事實走腳本，人審只留語義矛盾（條文見 AGENTS.md「只讀第三方審查」節）。
"""
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PY = sys.executable

results = []   # (名稱, True/False/None, 說明)  None＝SKIP


def run(cmd, cwd=ROOT, env=None, timeout=900, input_text=None):
    return subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout, input=input_text,
                          capture_output=True, text=True)


def item(name, ok, detail):
    results.append((name, ok, detail))
    mark = '✓' if ok is True else ('✗' if ok is False else '−')
    print(f'[{mark}] {name}：{detail}')


# ── ① 白名單電池 ────────────────────────────────────────────────
def battery_ignore():
    ls = run(['git', 'ls-files', '-z'])
    files = [f for f in ls.stdout.split('\0') if f]
    p = run(['git', 'check-ignore', '--no-index', '--stdin', '-z'],
            input_text='\0'.join(files))
    ignored = [f for f in p.stdout.split('\0') if f]
    if ignored:
        item('①跟蹤電池', False, f'{len(ignored)} 個已跟蹤文件仍被忽略：{ignored[:5]}')
    else:
        item('①跟蹤電池', True, f'{len(files)} 個跟蹤文件零命中（--no-index）')

    bad = []
    gi = os.path.join(ROOT, '.gitignore')
    with open(gi, encoding='utf-8') as f:
        # 目錄型 ! 行（尾斜線）跳過探測：目錄不存在時無法驗證目錄語義（探針不友好），
        # 其實際效果由①跟蹤電池對目錄內文件的判定兜底；文件型！行剝尾斜線探測
        wl = [l.rstrip('\n')[1:].strip().rstrip('/') for l in f if l.startswith('!') and not l.rstrip('\n').endswith('/')]
    for path in wl:
        if run(['git', 'check-ignore', '-q', '--no-index', '--', path]).returncode == 0:
            bad.append(path)   # 白名單行寫了但仍被忽略＝壞行（行內尾註/粘行特徵）
    if bad:
        item('①白名單行', False, f'{len(bad)} 條 ! 行不生效：{bad[:5]}')
    else:
        item('①白名單行', True, f'{len(wl)} 條 ! 行全部生效')


# ── ② 未來日期掃描 ──────────────────────────────────────────────
def battery_dates():
    today = datetime.date.today().isoformat()
    r = run(['git', 'grep', '-n', '-o', '-I', '-E',
             r'20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])', '--', '.'])
    future = []
    if r.returncode == 0:
        for ln in r.stdout.splitlines():
            m = re.match(r'^(.*?):(\d+):(20[0-9]{2}-[0-9]{2}-[0-9]{2})$', ln)
            if m and m.group(3) > today:
                future.append(f'{m.group(1)}:{m.group(2)}（{m.group(3)}）')
    item('②未來日期', not future,
         '零命中' if not future else f'{len(future)} 處晚於今日 {today}：{future[:5]}')


# ── ③ 校驗單對賬 ────────────────────────────────────────────────
def battery_sums():
    r = run(['sha256sum', '-c', 'SHA256SUMS'], cwd=os.path.join(HERE, 'std_tables'))
    n_ok = r.stdout.count(': OK')
    item('③std_tables', r.returncode == 0,
         f'{n_ok} 條全 OK' if r.returncode == 0 else f'非 OK 存在（exit {r.returncode}）：{r.stdout[-300:]}')

    # 校驗單路徑前綴爲 sources/，須自 corpus/ 目錄執行（BUILD.md「cd ..」同理）
    corpus = os.path.join(HERE, 'dict_pipeline', 'corpus')
    src = os.path.join(corpus, 'sources')
    # 「語料是否存在」不能看目錄非空——淨室克隆裏 sources/ 有 BUILD.md/SHA256SUMS 等
    # 跟蹤文件，目錄必然非空（2026-11-05 演練實測暴露）。改判校驗單所列數據件是否在場：
    # 全不在＝無語料模式 SKIP；部分在＝按校驗單對賬（缺件的自然 FAIL）。
    listed = []
    sums = os.path.join(corpus, 'SHA256SUMS')
    if os.path.exists(sums):
        for ln in open(sums, encoding='utf-8'):
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            parts = ln.split(None, 1)
            if len(parts) == 2:
                listed.append(parts[1].lstrip('*'))
    have = [x for x in listed if os.path.exists(os.path.join(corpus, x))]
    if not listed:
        item('③corpus', None, '校驗單缺失，SKIP')
        return
    if not have:
        item('③corpus', None, f'語料缺席 {len(listed)} 件全無（無語料模式），SKIP')
        return
    r = run(['sha256sum', '-c', 'SHA256SUMS'], cwd=corpus)
    n_ok = r.stdout.count(': OK')
    item('③corpus', r.returncode == 0,
         f'{n_ok} 條全 OK' if r.returncode == 0
         else f'在場 {len(have)}/{len(listed)} 件，對賬未全過（exit {r.returncode}）')


# ── ④ HEAD 煙測（worktree 檢出，僅用跟蹤文件）──────────────────
def battery_head_smoke():
    run(['git', 'worktree', 'prune'])   # 開場兜底：清 kill -9 等中斷殘留的陳舊註冊
    tmp = tempfile.mkdtemp(prefix='rime-head-smoke-')
    wt = os.path.join(tmp, 'wt')
    try:
        add = run(['git', 'worktree', 'add', '--detach', wt, 'HEAD'])
        if add.returncode != 0:
            item('④HEAD煙測', False, f'worktree 建立失敗：{add.stderr.strip()[:200]}')
            return
        tools = os.path.join(wt, 'lua', 'codelllc09', 'tools')
        a = run([PY, 'check_acceptance.py'], cwd=tools)
        v = run([PY, 'gen_no_simple_data.py', '--verify'], cwd=tools)
        ok = a.returncode == 0 and v.returncode == 0
        detail = (f'check_acceptance exit={a.returncode}；--verify '
                  f'{"零漂移" if v.returncode == 0 else "漂移/失敗 exit=" + str(v.returncode)}')
        item('④HEAD煙測', ok, detail)
    finally:
        run(['git', 'worktree', 'remove', '--force', wt])
        run(['git', 'worktree', 'prune'])   # 收尾再清：本次中斷（目錄已被系統回收時）的殘留
        shutil.rmtree(tmp, ignore_errors=True)


# ── ⑤ 雙檢編排 ──────────────────────────────────────────────────
def battery_checks():
    a = run([PY, 'check_acceptance.py'], cwd=HERE)
    v = run([PY, 'gen_no_simple_data.py', '--verify'], cwd=HERE)
    ok, det = a.returncode == 0 and v.returncode == 0, f'acceptance={a.returncode} verify={v.returncode}'
    mapping = os.path.join(HERE, 'dict_pipeline', 'corpus', 'sources', '_mapping', 'STPhrases.txt')
    if os.path.exists(mapping):
        t = run([PY, 'check_tendency.py'], cwd=HERE)
        ok = ok and t.returncode == 0
        det += f' tendency={t.returncode}'
    else:
        det += ' tendency=SKIP（無語料）'
    item('⑤雙檢', ok, det)


# ── ⑥ 留痕靜態電池 ────────────────────────────────────────────
# 原則：任何寫盤腳本，要麼接 _ledger 留痕，要麼在此明說「爲什麼不用記」。
# 未聲明的寫盤者即敗——防「新寫盤點悄悄長出來、日誌面卻沒跟上」。
WRITER_EXEMPT = {
    'check_consistency.py': '門禁自身：只寫臨時目錄（worktree/tempfile），不改任何數據',
    'gen_no_simple_data.py': '產物 no_simple_data.lua 已入庫（git 提交即歷史）；另有 --verify 零漂移閉環',
    'gen_pinyin_tone.py': '產物 pinyin_tone.lua 已入庫（git 提交即歷史）；輸入漂移由⑦溯源電池兜底',
    'mdx_keys.py': '一次性取鍵工具：讀外部 .mdx、輸出臨時鍵表，不入庫亦不參與管道',
    '_ledger.py': '台賬寫入器本身',
    '_provenance.py': '溯源清單寫入器本身',
}
WRITE_RE = re.compile(r"open\([^)]*['\"](?:w|a|wb|ab)['\"]|io\.open\([^)]*['\"](?:w|a|wb|ab)['\"]"
                      r"|open\([^)]*,\s*['\"]w['\"]|shutil\.copy")


def battery_ledger_static():
    ls = run(['git', 'ls-files']).stdout.split()
    scripts = [f for f in ls if f.endswith(('.py', '.sh'))]
    undeclared, tally = [], 0
    for f in scripts:
        p = os.path.join(ROOT, f)
        try:
            txt = open(p, encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        if not (WRITE_RE.search(txt) or ('>>' in txt and f.endswith('.sh'))):
            continue
        tally += 1
        base = os.path.basename(f)
        if '_ledger' in txt or base in WRITER_EXEMPT:
            continue
        undeclared.append(f)
    if undeclared:
        item('⑥留痕靜態', False,
             f'{len(undeclared)} 個寫盤腳本既無 _ledger 亦無豁免聲明：{undeclared[:5]}')
    else:
        item('⑥留痕靜態', True,
             f'{tally} 個寫盤腳本全部有留痕或已聲明豁免（{len(WRITER_EXEMPT)} 條豁免）')


# ── ⑦ 溯源電池 ──────────────────────────────────────────────────
def battery_provenance():
    sys.path.insert(0, HERE)
    import _provenance
    ok, bad = _provenance.verify(verbose=False)
    if ok is None:
        item('⑦溯源', None, '輸入整體缺席（無語料模式：如淨室克隆），SKIP')
    elif ok:
        n = len(_provenance.load().get('artifacts', {}))
        item('⑦溯源', True, f'{n} 個產物的輸入指紋與現狀一致')
    else:
        item('⑦溯源', False, f'{len(bad)} 處漂移：{bad[:3]}')


# ── ⑧ 台賬衛生 ──────────────────────────────────────────────────
def battery_ledger_health():
    sys.path.insert(0, HERE)
    import _ledger
    rows, js, bad = _ledger.selfcheck()
    if bad:
        item('⑧台賬衛生', False, f'{len(bad)} 處問題：{bad[:3]}')
    else:
        item('⑧台賬衛生', True, f'TSV {len(rows)} 行／JSONL {len(js)} 行，格式與雙檔對賬通過')


# ── ⑨ 鏡像覆蓋 ──────────────────────────────────────────────────
def battery_mirror():
    m = os.environ.get('RIME_MIRROR') or os.path.expanduser('~/Backups/rime/mirror.git')
    if not os.path.isdir(m):
        item('⑨鏡像', None, f'未建鏡像（{m}）——建議 backup.sh mirror-init')
        return
    head = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
    if not head:
        item('⑨鏡像', None, '倉庫無提交，SKIP')
        return
    hit = run(['git', '-C', m, 'cat-file', '-e', f'{head}^{{commit}}']).returncode == 0
    n = len([x for x in run(['git', '-C', m, 'for-each-ref', 'refs/snapshots']).stdout.splitlines() if x])
    # tag 也要在鏡像裏：tag 是「已知良好點」，出事後最想要的東西。
    # 2026-11-05 實例：mirror-sync 只推 HEAD，5 個 tag 一個都沒進鏡像。
    lt = [x for x in run(['git', 'tag', '-l']).stdout.splitlines() if x]
    mt = [x for x in run(['git', '-C', m, 'for-each-ref', '--format=%(refname:short)',
                          'refs/tags']).stdout.splitlines() if x]
    lack = [x for x in lt if x not in mt]
    if not hit:
        item('⑨鏡像', False,
             f'鏡像未覆蓋 HEAD {head[:9]}（快照 ref {n} 個）——立即 backup.sh mirror-sync')
    elif lack:
        item('⑨鏡像', False,
             f'{len(lack)} 個 tag 不在鏡像中（如 {lack[:3]}）——backup.sh mirror-sync')
    else:
        item('⑨鏡像', True, f'HEAD {head[:9]} 在鏡像中；快照 ref {n} 個；tag {len(mt)}/{len(lt)}')


# ── ⑫ 鉤子有效性 ────────────────────────────────────────────────
def battery_hooks():
    hp = run(['git', 'config', 'core.hooksPath']).stdout.strip()
    if not hp:
        item('⑫鉤子', None, 'core.hooksPath 未設（淨室克隆／CI 屬正常）——新克隆請跑 tools/setup.sh')
        return
    base = hp if os.path.isabs(hp) else os.path.join(ROOT, hp)
    want = ('pre-commit', 'post-commit', 'pre-push')
    missing = [h for h in want if not os.path.exists(os.path.join(base, h))]
    noexec = [h for h in want
              if os.path.exists(os.path.join(base, h))
              and not os.access(os.path.join(base, h), os.X_OK)]
    if missing or noexec:
        det = []
        if missing: det.append(f'缺檔 {missing}')
        if noexec: det.append(f'不可執行 {noexec}（git 會靜默跳過）')
        item('⑫鉤子', False, '；'.join(det))
    else:
        item('⑫鉤子', True, f'hooksPath={hp}，三件鉤子齊備且可執行')


# ── ⑩ 對象庫完整性 ──────────────────────────────────────────────
def battery_fsck():
    r = run(['git', 'fsck', '--connectivity-only', '--no-progress'], timeout=180)
    noise = [l for l in (r.stdout + r.stderr).splitlines()
             if l.startswith(('error', 'fatal', 'missing'))]
    if r.returncode != 0 or noise:
        item('⑩對象庫', False, f'fsck exit={r.returncode}；{noise[:3]}')
    else:
        item('⑩對象庫', True, 'git fsck --connectivity-only 零錯誤')


# ── ⑪ 確定性（--determinism，默認關）───────────────────────────
def battery_determinism():
    outs_paths = ['xuma_L.dict.yaml',
                  'lua/codelllc09/tools/dict_pipeline/archive/reports/TO_BE_REVIEWED.txt',
                  'lua/codelllc09/tools/dict_pipeline/archive/reports/addon_review.txt',
                  'lua/codelllc09/tools/dict_pipeline/archive/reports/freq_report.md',
                  'xuma.addon.dict.yaml']

    def digest():
        h = hashlib.sha256()
        for f in outs_paths:
            p = os.path.join(ROOT, f)
            if os.path.exists(p):
                h.update(open(p, 'rb').read())
        return h.hexdigest()

    before = run(['git', 'status', '--porcelain']).stdout
    outs, digs, after = [], [], []
    for seed in ('1', '42'):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        r = run([PY, 'gen_freq_dict.py'], cwd=HERE, env=env, timeout=1800)
        outs.append(r.stdout)
        digs.append(digest())
        after.append(run(['git', 'status', '--porcelain']).stdout)
    if '寫出' not in outs[0]:
        item('⑪確定性', None, '管道未寫盤（無語料模式），SKIP')
        return
    # 判據＝seed 不變性：兩次 stdout 與產出字節摘要皆一致；
    # 相對跑前的漂移（隔日日期戳屬預期）只告警——那是詞表改動流程的命題，不是確定性的
    same = outs[0] == outs[1] and digs[0] == digs[1]
    detail = ('雙 seed stdout＋產出字節摘要一致' if same else
              f'seed 間產出不一致（stdout 同={outs[0] == outs[1]}／'
              f'摘要同={digs[0] == digs[1]}）——行序漂移')
    if after[0] != before or after[1] != before:
        new = sorted(set(after[0].splitlines()) - set(before.splitlines()))
        detail += ('；⚠ 產出較已提交版本有漂移（隔日日期戳屬預期；實質變化請走'
                   f'diff_report 複覈後提交，或 git checkout -- 還原）：{new[:3]}')
    item('⑪確定性', same, detail)


def main():
    print(f'check_consistency @ {datetime.date.today().isoformat()}　ROOT={ROOT}')
    battery_ignore()
    battery_dates()
    battery_sums()
    battery_head_smoke()
    battery_checks()
    battery_ledger_static()
    battery_provenance()
    battery_ledger_health()
    battery_mirror()
    battery_fsck()
    battery_hooks()
    if '--determinism' in sys.argv:
        battery_determinism()
    fails = [r for r in results if r[1] is False]
    skips = [r for r in results if r[1] is None]
    print(f'\n小計：{len(results)} 項｜FAIL {len(fails)}｜SKIP {len(skips)}'
          + ('' if not skips else '（' + '、'.join(r[0] for r in skips) + '）'))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
