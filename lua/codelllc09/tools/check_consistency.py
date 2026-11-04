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
  ⑥ --determinism（默認關，僅管道邏輯改動後跑）：雙 PYTHONHASHSEED 復跑管道——
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
    present = os.path.isdir(src) and any(os.scandir(src))
    if not present:
        item('③corpus', None, '語料缺失（無語料模式），SKIP')
        return
    r = run(['sha256sum', '-c', 'SHA256SUMS'], cwd=corpus)
    n_ok = r.stdout.count(': OK')
    item('③corpus', r.returncode == 0,
         f'{n_ok} 條全 OK' if r.returncode == 0 else f'存在但未全過（exit {r.returncode}）')


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


# ── ⑥ 確定性（--determinism，默認關）───────────────────────────
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
        item('⑥確定性', None, '管道未寫盤（無語料模式），SKIP')
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
    item('⑥確定性', same, detail)


def main():
    print(f'check_consistency @ {datetime.date.today().isoformat()}　ROOT={ROOT}')
    battery_ignore()
    battery_dates()
    battery_sums()
    battery_head_smoke()
    battery_checks()
    if '--determinism' in sys.argv:
        battery_determinism()
    fails = [r for r in results if r[1] is False]
    skips = [r for r in results if r[1] is None]
    print(f'\n小計：{len(results)} 項｜FAIL {len(fails)}｜SKIP {len(skips)}'
          + ('' if not skips else '（' + '、'.join(r[0] for r in skips) + '）'))
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
