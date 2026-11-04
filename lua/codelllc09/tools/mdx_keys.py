#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdx_keys.py — 极简 MDict MDX 解析器：只提取全部词条 key（不解析正文）。

用法：python3 mdx_keys.py 文件.mdx [输出.txt]
格式参考 mdict-analysis（readmdict）公开实现，v1.2/v2.0 兼容。
"""
import struct, sys, zlib


def read_num(f):
    b = f.read(1)[0]
    if b < 0x80:
        return b
    if b < 0xC0:
        return ((b - 0x80) << 8) | f.read(1)[0]
    if b < 0xE0:
        b1, b2 = f.read(2)
        return ((b - 0xC0) << 16) | (b1 << 8) | b2
    if b < 0xF0:
        b1, b2, b3 = f.read(3)
        return ((b - 0xE0) << 24) | (b1 << 16) | (b2 << 8) | b3
    b1, b2, b3, b4 = f.read(4)
    return (b1 << 24) | (b2 << 16) | (b3 << 8) | b4


def parse_mdx_keys(path):
    keys = []
    with open(path, 'rb') as f:
        # ---- header ----
        hlen = struct.unpack('>I', f.read(4))[0]
        hraw = f.read(hlen)
        if hraw.endswith(b'\x00\x00'):
            hraw = hraw[:-2]
        hxml = hraw.decode('utf-16-le', errors='replace')
        import re
        enc = (re.search(r'Encoding="([^"]+)"', hxml) or [None, 'utf-8'])[1] if re.search(r'Encoding="([^"]+)"', hxml) else 'utf-8'
        if enc.lower().replace('-', '') not in ('utf8',):
            enc = 'utf-16-le' if '16' in enc else 'utf-8'
        encrypted = int((re.search(r'Encrypted="(\d)"', hxml) or ['0', '0'])[1])
        f.read(2)          # \x00\x00 终止
        # ---- length 区 ----
        A = read_num(f)    # 广义数 A（含 width 标记）
        f.read(1) if False else None
        B = read_num(f)    # encrypted 旗标所在数
        read_num(f)        # C
        # 判断 v1.2 / v2.0：读 8 字节试 wide
        f.read(1)          # D（v1.x 的第4数高位）
        ver = (re.search(r'GeneratedByEngineVersion="([^"]+)"', hxml) or ['0', '2'])[1]
        is_v2 = not ver.startswith('1')
        if is_v2:
            read_num(f)    # E
            f.read(4)      # F..I 由 read_num 变长——简化：按 mdict 格式逐字段
        # 上面的逐字段手工对齐极易错，改用标准序列：
    # —— 重新以标准序列实现（见下）——
    return keys


def parse(path):
    """标准实现：header → lengths → key index（zlib）→ keys。"""
    import re
    f = open(path, 'rb')
    hlen = struct.unpack('>I', f.read(4))[0]
    hraw = f.read(hlen)
    hxml = hraw.rstrip(b'\x00\x00').decode('utf-16-le', errors='replace')
    enc_s = re.search(r'Encoding="([^"]+)"', hxml)
    enc = 'utf-16-le' if enc_s and '16' in enc_s.group(1) else 'utf-8'
    f.read(2)
    A = read_num(f)   # N1
    B = read_num(f)   # N2（encrypted 信息通常位此区，v2 为 4 字节宽）
    read_num(f)       # N3
    read_num(f)       # N4
    ver = (re.search(r'GeneratedByEngineVersion="([^"]+)"', hxml) or [None, '1'])[1]
    is_v2 = not ver.startswith('1')
    if is_v2:
        read_num(f)   # E
        read_num(f)   # F
        read_num(f)   # G
        read_num(f)   # H
        read_num(f)   # I
        read_num(f)   # J
        read_num(f)   # K
    # num_entries …
    num_entries = read_num(f)
    key_idx_size = struct.unpack('>I', f.read(4))[0]
    struct.unpack('>I', f.read(4))      # key index adler32
    if is_v2:
        rbi_size = struct.unpack('>I', f.read(4))[0]
        struct.unpack('>I', f.read(4))
        rb_size = struct.unpack('>I', f.read(4))
        struct.unpack('>I', f.read(4))
    # ---- key index ----
    comp_type = struct.unpack('>I', f.read(4))[0]
    adler = struct.unpack('>I', f.read(4))[0]
    data = f.read(key_idx_size)
    if comp_type == 2:
        data = zlib.decompress(data)
    elif comp_type == 1:
        raise SystemExit('LZO 压缩未支持')
    g = {b: 0 for b in ()}
    import io as _io
    s = _io.BytesIO(data)
    n_blocks = read_num(s)
    widths = []
    for _ in range(n_blocks):
        usz = read_num(s)
        csz = read_num(s)
        widths.append((usz, csz))
    keys = []
    for usz, csz in widths:
        block = s.read(csz)
        if csz != usz:                        # 块级 zlib（v2 常见：块内还有一层压缩）
            block = zlib.decompress(block)
        t = _io.BytesIO(block)
        n = read_num(t)
        for _ in range(n):
            klen = read_num(t)
            kraw = t.read(klen)
            read_num(t)          # record offset
            keys.append(kraw.decode(enc, errors='replace'))
    return keys


if __name__ == '__main__':
    mdx = sys.argv[1]
    ks = parse(mdx)
    out = sys.argv[2] if len(sys.argv) > 2 else None
    print(f'词条 {len(ks)}，样例: {ks[:5]}')
    if out:
        open(out, 'w', encoding='utf-8').write('\n'.join(ks))
