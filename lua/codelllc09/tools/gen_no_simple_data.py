#!/usr/bin/env python3
# gen_no_simple_data.py — 生成 no_simple_data.lua（非簡模式攔截集）
#
# 規則（功能定義：「繁體標準文本不用之形」，非字源分類）：
#   X 被攔截 ⟺ X ∈ STCharacters 鍵 且 首選 s2t 映射 ≠ 自身
#             且 X 不能編碼進 Big5（繁體標準集代理）
#             且 X ∉ TSCharacters 鍵（交叉保險：不得同時是繁體專用形）
#             且 X ∉ WHITELIST
#
# 說明：攔截集同時包含「新造簡化字」（萧/爱/欢）與「被簡化借用的古字/異體形」
#   （个/号/尔——Big5 繁體標準集皆無此形，出現於輸出即被讀作簡體）。
#   要放行某字（如《易經》「无咎」之 无），把它加進 WHITELIST 後重跑本腳本，
#   再重啟 IBus（systemctl --user restart org.freedesktop.IBus.session.GNOME.service）。
#
# 依賴：同目錄 STCharacters.txt / TSCharacters.txt（OpenCC 源字典，Apache-2.0，
#       來源 https://github.com/BYVoid/OpenCC data/dictionary/）

import io
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 官方字表白名單（本地 std_tables/，來源見該目錄 README.md）：
# 2026-11-05 分組：official/＝現代官方標準表（白名單主源）；classics/＝古籍字書語料（考據源）
#   official/  臺灣常用/次常用標準字體表、大陸規範漢字表附1 繁體/異體字列、
#              GB/Z 40637、臺灣丙丁表、CNS11643 1-2 字面等
#   classics/  爾雅、千字文、說文、廣韻、康熙、中華大字典、玉篇、集韻、
#              俗字譜、龍龕、類篇、五音集韻等古籍字書語料
# 凡被任一表/典收錄 → 放行。
#
# 人工審定補充（逐字記由）：
#   叶 = 協之古異體（叶韻 xié）；「叶子」等簡義詞走詞級黑名單
#   冈 = 網之古文（碼表 mt- 碼族佐證：冈=mti 與 网=mtt 同族，岡=mxmv 另屬）
#   备 = 原之古字（碼表自證：平备=平原、艸备=草原 之古字寫法詞條）
#   韵 = 韻之異體（庫中 韵=ysxn / 韻=yon 各自有碼）
#   无 = 《易經》用字（无咎）
#   电 = 電之本字（象形本義，非簡化新造）
#   国 = 國之古俗體（古已有之，非簡化新造）
#   注：气/网 Big5 已收錄本就放行；厵/艸 無 s2t 映射亦本就放行
#
# 設計原則（2026-09-07 定案）：俗字（含白名單字）放行單字，但不得組詞——
#   word_ranges 全量簡形不豁免 WHITELIST，係有意爲之；含俗字詞條如需輸出，
#   逐字上屏或於 lua 端 word_block/詞表另行處置。
WHITELIST = '叶冈备韵无电国奂厢栀狰飞'   # 2026-09-08 增：飞=《說文》徐注构件「上旁飞者象鸟颈」（康熙原文唯一命中，
    # 用户定案：部件亦有字形意义，放行）   # 2026-09-08 增：奂/厢=古籍省笔俗写有据；
    # 栀=《說文》本字（段注：徐鍇本作栀、玉篇「栀之移切」，桅乃訛形）；狰=俗字（用户考据定案）

# 黑名單：優先級最高，從白名單中強制移除（即使有古籍/官方表出典也攔）
# 簡化偏旁：門/貝/見/風/馬/魚 有出典但作為偏旁影響面太大，全攔（飛已 2026-09-08 移出，見 WHITELIST 註）
BLACKLIST = '门贝见风马鱼'


def load_chars(name):
    p = os.path.join(HERE, 'std_tables', name)
    if os.path.exists(p):
        # 一字一行（新）或連續字符流（舊）均兼容：逐字讀取、濾空白
        return set(c for c in io.open(p, encoding='utf-8').read() if not c.isspace())
    return set()


OFFICIAL = set()
# 2026-11-05 分組：official/＝現代官方標準表（白名單主源）；classics/＝古籍字書語料（考據源）
for f in ('official/tw_jia_yi.txt', 'official/cn_duizhao_fanti.txt', 'official/cn_duizhao_yiti.txt',
          'classics/classics_erya.txt', 'classics/classics_qianziwen.txt', 'classics/classics_shuowen.txt',
          'classics/classics_guangyun.txt', 'classics/classics_kangxi.txt',
          'classics/classics_zhonghuadazidian.txt', 'classics/classics_yupian.txt',
          'classics/classics_jiyun.txt', 'classics/classics_suzipu.txt',
          'classics/classics_longkan.txt', 'classics/classics_leipian.txt',
          'official/gbz_40637.txt', 'official/tw_bing_ding.txt', 'official/cns_plane12.txt',
          'classics/classics_wuyinjiyun.txt'):
    OFFICIAL |= load_chars(f)

# 黑名單優先：即使有出典也強制攔截
OFFICIAL -= set(BLACKLIST)


def load_keys_first(fn):
    m = {}
    for line in io.open(os.path.join(HERE, fn), encoding='utf-8'):
        line = line.rstrip('\n')
        if not line or line.startswith('#'):
            continue
        p = line.split('\t')
        if len(p) == 2:
            m[p[0]] = p[1].split(' ')[0]
    return m


def big5ok(ch):
    try:
        ch.encode('big5')
        return True
    except UnicodeEncodeError:
        return False


st = load_keys_first('STCharacters.txt')
ts = load_keys_first('TSCharacters.txt')

blocked = sorted({ord(k) for k, v in st.items()
                  if v != k and not big5ok(k)
                  and k not in ts and k not in OFFICIAL and k not in WHITELIST
                  or k in BLACKLIST})

# 全量簡體形（組詞攔截用）：不管白名單，所有 s2t 可轉 + 非 Big5
full = sorted({ord(k) for k, v in st.items() if v != k and not big5ok(k) and k not in ts})

pairs = []
for cp in blocked:
    if pairs and cp == pairs[-1][1] + 1:
        pairs[-1][1] = cp
    else:
        pairs.append([cp, cp])
flat = [x for p in pairs for x in p]

lines = []
for i in range(0, len(flat), 12):
    lines.append('  ' + ', '.join(str(x) for x in flat[i:i + 12]) + ',')

pairs2 = []
for cp in full:
    if pairs2 and cp == pairs2[-1][1] + 1: pairs2[-1][1] = cp
    else: pairs2.append([cp, cp])
flat2 = [x for p in pairs2 for x in p]
lines2 = []
for i in range(0, len(flat2), 12):
    lines2.append('  ' + ', '.join(str(x) for x in flat2[i:i + 12]) + ',')

out = os.path.join(HERE, '..', 'no_simple_data.lua')
# 缺表禁寫守衛：關鍵出典表缺失時白名單退化、生成攔截過嚴的壞表，禁止覆蓋現役 lua
_required = ('official/tw_jia_yi.txt', 'official/cn_duizhao_fanti.txt', 'official/cn_duizhao_yiti.txt',
             'classics/classics_shuowen.txt', 'classics/classics_guangyun.txt', 'classics/classics_kangxi.txt',
             'classics/classics_zhonghuadazidian.txt', 'official/tw_bing_ding.txt', 'official/cns_plane12.txt')
_missing = [f for f in _required if not os.path.exists(os.path.join(HERE, 'std_tables', f))]
if _missing:
    print(f'⚠ std_tables 缺表 {_missing}，白名单将退化，已禁止写盘；'
          f'恢复途径见 std_tables/README.md（或 git 检出该目录）')
    import sys; sys.exit(1)

if '--verify' in sys.argv:   # 規範化閉環：重生成→與現役對比，零漂移即綠
    import sys
    import tempfile, filecmp
    out = tempfile.mktemp(suffix='.lua')
with io.open(out, 'w', encoding='utf-8') as f:
    f.write('''-- no_simple_data.lua（自动生成）
-- 主攔截 {n} 字；全量簡體形 {n2} 字（組詞攔）

local M = {{}}
M.ranges = {{
{body}
}}

M.word_ranges = {{
{body2}
}}

return M
'''.format(n=len(blocked), n2=len(full), body='\n'.join(lines), body2='\n'.join(lines2)))

print('blocked chars:', len(blocked), '/ ranges:', len(pairs))


def status(ch):
    return 'BLOCK' if ord(ch) in set(blocked) else 'keep '


if '--verify' not in sys.argv:
    # 溯源刷新（2026-11-05 加固）：僅在真寫盤時記錄；--verify 走臨時文件不動清單
    import sys as _s
    _s.path.insert(0, HERE)
    from _provenance import update as _pv
    _pv('lua/codelllc09/no_simple_data.lua')

if '--verify' in sys.argv:
    same = filecmp.cmp(out, os.path.join(HERE, '..', 'no_simple_data.lua'), shallow=False)
    os.unlink(out)
    print('verify:', '零漂移 ✓' if same else '漂移！現役生成物與字表不一致 ✗')
    sys.exit(0 if same else 1)

for ch in '萧爱欢个号尔宫迹灾却无干后于台里只周秋系面万丑蕭愛龍中華':
    print(f'  {ch} -> {status(ch)}')
