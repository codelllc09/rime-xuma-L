#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_provenance.py — 生成物溯源清單（2026-11-05 加固；可複現性的一半）

問題：詞典、攔截集、拼音調都是腳本產物。腳本在 git 裏，產物也在 git 裏，但
「這個產物是由**當時**那版輸入生成的」這件事沒有任何地方記錄。輸入一漂移
（語料重下、裁定檔改動、字表替換），產物與輸入的對應關係就斷了，且無從察覺。

本模塊把「產物 ← 生成器 ← 輸入指紋」固化進 PROVENANCE.json：
    {"schema":1,
     "artifacts":{
       "xuma_L.dict.yaml":{
         "generator":"gen_freq_dict.py",
         "cmd":"python3 gen_freq_dict.py",
         "inputs":[{"kind":"glob","pattern":"...","count":31,"digest":"..."}],
         "output_sha256":"..."}}}

digest 定義：對 glob 命中的文件按相對路徑排序，取 sha256("relpath\\0sha256\\n"...)
的總和摘要——目錄級指紋，避免每件單列。

**不含時間戳**（git 提交即時間），故同一輸入必得同一文件——確定性電池不因此漂移。

用法：
    from _provenance import update, verify
    update('xuma_L.dict.yaml', 'gen_freq_dict.py', 'python3 gen_freq_dict.py',
           ['lua/codelllc09/tools/dict_pipeline/handpicked.txt', ...])
    python3 _provenance.py --verify     # 逐條比對輸入指紋與產物存在性
"""
import glob
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PATH = os.path.join(HERE, 'PROVENANCE.json')

# 產物 → 輸入 glob（相對倉庫根；`**` 遞歸）。這是「重建需要什麼」的權威聲明。
SPEC = {
    'xuma_L.dict.yaml': [
        'lua/codelllc09/tools/dict_pipeline/handpicked.txt',
        'lua/codelllc09/tools/dict_pipeline/manual_overrides.txt',
        'lua/codelllc09/tools/dict_pipeline/archive/rulings/*.txt',
        'lua/codelllc09/tools/dict_pipeline/corpus/sources/**',
        'xuma_L.schema.yaml',
    ],
    'lua/codelllc09/no_simple_data.lua': [
        'lua/codelllc09/tools/std_tables/official/*.txt',
        'lua/codelllc09/tools/std_tables/classics/*.txt',
        'lua/codelllc09/tools/dict_pipeline/corpus/sources/_mapping/*',
    ],
    'lua/codelllc09/pinyin_tone.lua': [
        'lua/codelllc09/tools/dict_pipeline/corpus/sources/_mapping/*',
        'lua/codelllc09/tools/dict_pipeline/corpus/sources/E.subtlex/*',
    ],
}

GENERATOR = {
    'xuma_L.dict.yaml': ('gen_freq_dict.py', 'python3 gen_freq_dict.py'),
    'lua/codelllc09/no_simple_data.lua': ('gen_no_simple_data.py', 'python3 gen_no_simple_data.py'),
    'lua/codelllc09/pinyin_tone.lua': ('gen_pinyin_tone.py', 'python3 gen_pinyin_tone.py'),
}


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _static_prefix(pattern):
    """glob 的靜態前綴（第一個通配符之前的部分）。用於區分「輸入被改動」與「輸入整體缺席」。"""
    parts = []
    for seg in pattern.split('/'):
        if any(c in seg for c in '*?['):
            break
        parts.append(seg)
    return '/'.join(parts)


def _expand(pattern):
    """展開 glob（相對倉庫根），返回排序後的相對路徑列表。"""
    full = os.path.join(ROOT, pattern)
    if any(c in pattern for c in '*?['):
        hits = glob.glob(full, recursive=True)
    else:
        hits = [full] if os.path.exists(full) else []
    rel = [os.path.relpath(h, ROOT).replace(os.sep, '/') for h in hits if os.path.isfile(h)]
    return sorted(set(rel))


def _digest(rel_paths):
    h = hashlib.sha256()
    for r in rel_paths:
        h.update(r.encode('utf-8') + b'\0' + _sha(os.path.join(ROOT, r)).encode() + b'\n')
    return h.hexdigest()


def _ignored_count(rel_paths):
    """這批路徑中有幾件落在 gitignore 區。用於區分「無語料克隆」與「輸入真漂移」：
    記錄時含忽略區數據件、驗證時一件都不含 ⇒ 該輸入整體不在場（淨室克隆），SKIP 而非 FAIL。"""
    if not rel_paths:
        return 0
    try:
        r = subprocess.run(['git', '-C', ROOT, 'check-ignore', '--stdin'],
                           input='\n'.join(rel_paths), capture_output=True, text=True, timeout=30)
        return len([x for x in r.stdout.splitlines() if x.strip()])
    except Exception:
        return 0


def fingerprint(pattern):
    rel = _expand(pattern)
    return {'kind': 'glob', 'pattern': pattern, 'count': len(rel),
            'digest': _digest(rel), 'ignored_count': _ignored_count(rel)}


def load():
    if not os.path.exists(PATH):
        return {'schema': 1,
                'note': '生成物溯源清單：產物←生成器←輸入指紋。由 _provenance.py 維護，'
                        'gen_freq_dict.py / gen_no_simple_data.py / gen_pinyin_tone.py 運行時自動刷新。'
                        '不含時間戳（git 提交即時間），故同一輸入必得同一文件。',
                'artifacts': {}}
    with open(PATH, encoding='utf-8') as f:
        return json.load(f)


def save(data):
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    os.replace(tmp, PATH)


def update(artifact, inputs=None):
    """刷新單個產物的溯源條目。inputs 省略時用 SPEC 中的聲明。"""
    gen, cmd = GENERATOR.get(artifact, ('unknown', ''))
    inputs = inputs or SPEC.get(artifact)
    if not inputs:
        raise ValueError(f'{artifact} 無輸入聲明（先補 SPEC）')
    data = load()
    data['artifacts'][artifact] = {
        'generator': gen,
        'cmd': cmd,
        'inputs': [fingerprint(p) for p in inputs],
        'output_sha256': _sha(os.path.join(ROOT, artifact))
        if os.path.exists(os.path.join(ROOT, artifact)) else None,
    }
    save(data)
    return data['artifacts'][artifact]


def verify(verbose=True):
    """返回 (ok, 問題列表)。逐條比對：輸入指紋是否漂移、產物是否存在。"""
    data = load()
    bad = []
    arts = data.get('artifacts', {})
    missing = [a for a in SPEC if a not in arts]
    if missing:
        bad.append(f'清單缺項（SPEC 有而 JSON 無）：{missing}')
    absent_art = []
    for art, rec in sorted(arts.items()):
        full = os.path.join(ROOT, art)
        if not os.path.exists(full):
            bad.append(f'{art}：產物不存在')
            continue
        n_absent = 0
        for inp in rec.get('inputs', []):
            if not os.path.exists(os.path.join(ROOT, _static_prefix(inp['pattern']))):
                n_absent += 1          # 靜態前綴目錄整個不在＝無語料模式，非漂移
                continue
            now = fingerprint(inp['pattern'])
            # 記錄時含忽略區數據件，而今一件不含 ⇒ 語料不在場（淨室克隆），SKIP 該輸入
            if inp.get('ignored_count', 0) > 0 and now['ignored_count'] == 0:
                n_absent += 1
                continue
            if now['digest'] != inp['digest'] or now['count'] != inp['count']:
                bad.append(f'{art}：輸入漂移 {inp["pattern"]}'
                           f'（記 {inp["count"]} 件/{inp["digest"][:8]}，'
                           f'今 {now["count"]} 件/{now["digest"][:8]}）')
        if rec.get('inputs') and n_absent == len(rec['inputs']):
            absent_art.append(art)
    if verbose and absent_art:
        print(f'  （{len(absent_art)} 個產物輸入整體缺席＝無語料模式，計 SKIP）')
    if absent_art and len(absent_art) == len(arts):
        return None, []            # 全部缺席：SKIP 語義，交由門禁電池標 SKIP
        if verbose:
            out = rec.get('output_sha256')
            cur = _sha(full)
            flag = '一致' if out == cur else '不同（僅告警：產物本身變更屬正常流程）'
            print(f'  {art} ← {rec["generator"]}：輸入 {len(rec["inputs"])} 組，'
                  f'產物摘要{flag}')
    return (not bad), bad


def main():
    a = sys.argv[1:]
    if a and a[0] == '--verify':
        ok, bad = verify()
        if ok is None:
            print('− 溯源對賬：輸入整體缺席（無語料模式），SKIP')
            return 0
        if bad:
            print('✗ 溯源對賬：')
            for b in bad:
                print('   ', b)
            return 1
        print('✓ 溯源對賬：產物與輸入指紋全部一致')
        return 0
    if a and a[0] == '--update':
        targets = a[1:] or sorted(SPEC)
        for t in targets:
            rec = update(t)
            print(f'已刷新 {t}（輸入 {len(rec["inputs"])} 組）')
        return 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
