#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_pinyin_tone.py — 生成 lua/codelllc09/pinyin_tone.lua（字 → 帶調拼音表）
數據源：freqdata/Unihan_Readings.txt 的 kMandarin 字段（Unicode 官方，普通話規範讀音）
輸出格式：return { ["李"]="li3", ["好"]="hao3", ... }，多音以 "/" 分隔
注意：kMandarin 爲規範讀音，部分多音字只收一讀（如「好」僅 hǎo）；
     聲調過濾是收窄工具，非窮舉保證。
"""
import io, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
RIME = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
PSEUDO = os.path.join(RIME, 'xuma_spelling_pseudo.dict.yaml')
SRC = os.path.join(HERE, 'dict_pipeline', 'corpus', 'Unihan_Readings.txt')
OUT = os.path.join(HERE, '..', 'pinyin_tone.lua')

TONE = {
    'ā': ('a', 1), 'á': ('a', 2), 'ǎ': ('a', 3), 'à': ('a', 4),
    'ē': ('e', 1), 'é': ('e', 2), 'ě': ('e', 3), 'è': ('e', 4),
    'ī': ('i', 1), 'í': ('i', 2), 'ǐ': ('i', 3), 'ì': ('i', 4),
    'ō': ('o', 1), 'ó': ('o', 2), 'ǒ': ('o', 3), 'ò': ('o', 4),
    'ū': ('u', 1), 'ú': ('u', 2), 'ǔ': ('u', 3), 'ù': ('u', 4),
    'ǖ': ('v', 1), 'ǘ': ('v', 2), 'ǚ': ('v', 3), 'ǜ': ('v', 4),
    'ü': ('v', 0),
    'ń': ('n', 2), 'ň': ('n', 3), 'ǹ': ('n', 4),
    'ḿ': ('m', 2),
}

def convert(syl):
    out, tone = [], 0
    for ch in syl:
        if ch in TONE:
            b, t = TONE[ch]
            out.append(b)
            if t: tone = t
        else:
            out.append(ch)
    return ''.join(out) + (str(tone) if tone else '')

def load_pseudo():
    table = {}
    in_data = False
    for line in io.open(PSEUDO, encoding='utf-8'):
        line = line.rstrip('\n')
        if not in_data:
            if line == '...':
                in_data = True
            continue
        m = re.match(r'^(\S)\t\[(.*?)\]', line)
        if not m:
            continue
        segs = m.group(2).split(',')
        if len(segs) < 3:
            continue
        readings = []
        for syl in segs[2].split('_'):
            c = convert(syl.strip())
            if c and c not in readings:
                readings.append(c)
        if readings:
            table[m.group(1)] = readings
    return table

def main():
    table = load_pseudo()
    n_pseudo = len(table)
    for line in io.open(SRC, encoding='utf-8'):
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 3 or parts[1] != 'kMandarin':
            continue
        ch = chr(int(parts[0][2:], 16))
        if ch in table:
            continue
        readings = []
        for syl in parts[2].split(';'):
            syl = syl.strip()
            c = convert(syl)
            if c and c not in readings:
                readings.append(c)
        if readings:
            table[ch] = readings
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write('-- pinyin_tone.lua（自动生成，勿手工编辑）\n')
        f.write('-- 字→带调拼音。主源：xuma_spelling_pseudo（与反查注解同源）；补源：Unihan kMandarin。多音以 / 分隔。\n')
        f.write('return {\n')
        for ch in sorted(table):
            f.write('["%s"]="%s",\n' % (ch, '/'.join(table[ch])))
        f.write('}\n')
    print('pinyin_tone.lua: %d 字（pseudo %d + kMandarin 补 %d）' % (len(table), n_pseudo, len(table) - n_pseudo))

if __name__ == '__main__':
    main()
