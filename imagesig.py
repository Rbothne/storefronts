#!/usr/bin/env python3
"""What a product photo looks like, in a few numbers, for telling colourways apart (matching.py).

Titles are a poor guide to colour: one store calls a hoodie "dark beige" and the next "verde", many name a colour
no word list holds ("Oatmeal / Umeboshi", "Potters Clay"), and some name none. The photo does not lie, so the build
reads each photo of a listing that matching needs it for, once, as a 32-pixel PNG from Shopify's image CDN (about
1.6 KB; `?width=32&height=32&crop=center&format=png`), and keeps:
  - a shape hash (dHash: 8 rows of whether each of 9 columns of grey is brighter than the next), 16 hex digits,
    alike for two photos of one product shot the same way;
  - the item's colour: the median of the pixels that are not the backdrop (the colour of the photo's border),
    in CIE Lab, so that a distance between two colours means much the same everywhere;
  - how much of the photo the item fills: a belt or a ring on a white page is mostly backdrop, and its "colour"
    and shape are then noise (matching.py trusts neither below a fifth).
Standard library only: PNG is zlib plus a few filters, so it can be decoded here. Photos on other hosts are not
read (86% of listings are on Shopify's CDN).

data/state/images.tsv keeps every photo read: image name, shape hash, L, a, b, item share, the day last needed. It travels
with the tracking files on the data branch, so a photo is fetched once, not every run.
"""
import concurrent.futures
import datetime
import math
import os
import struct
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "data", "state", "images.tsv")
KEEP_DAYS = 60            # a photo no listing has needed for this long is forgotten
UA = "Mozilla/5.0 (compatible; listings-image-check)"


# ---------------------------------------------------------------- PNG

def decode_png(data):
    """(width, height, rows of (r, g, b)) for an 8-bit PNG without interlacing; None for anything else."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, palette = 8, [], None
    width = height = depth = ctype = None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or interlace or ctype not in (0, 2, 3, 4, 6):
                return None
        elif kind == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    if not width or not idat:
        return None
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw, stride = zlib.decompress(b"".join(idat)), width * bpp
    rows, prev, at = [], bytearray(stride), 0
    for _ in range(height):
        f, line = raw[at], bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        for i in range(stride):                        # undo the row filter
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        px = []
        for x in range(width):
            v = line[x * bpp:(x + 1) * bpp]
            if ctype == 0:
                px.append((v[0],) * 3)
            elif ctype == 4:                           # grey and alpha: transparent is white, as a page shows it
                g = v[0] * v[1] // 255 + 255 - v[1]
                px.append((g,) * 3)
            elif ctype == 3:
                px.append(palette[v[0]] if palette and v[0] < len(palette) else (255, 255, 255))
            elif ctype == 6:
                al = v[3]
                px.append(tuple(ch * al // 255 + 255 - al for ch in v[:3]))
            else:
                px.append(tuple(v))
        rows.append(px)
    return width, height, rows


# ---------------------------------------------------------------- the two signals

def lab(rgb):
    """sRGB (0-255) to CIE Lab (D65)."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    x, y, z = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b, \
              (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def signature(png):
    """(shape hash as 16 hex digits, (L, a, b) of the item, share of the photo it fills) for a small PNG, or None."""
    img = decode_png(png)
    if not img:
        return None
    w, h, rows = img
    grey = [[0.299 * r + 0.587 * g + 0.114 * b for r, g, b in row] for row in rows]
    # dHash on a 9 x 8 grid of cell averages
    cells = [[sum(grey[y][x] for y in range(h * j // 8, max(h * (j + 1) // 8, h * j // 8 + 1))
                  for x in range(w * i // 9, max(w * (i + 1) // 9, w * i // 9 + 1)))
              for i in range(9)] for j in range(8)]
    bits = 0
    for row in cells:
        for i in range(8):
            bits = bits << 1 | (row[i] > row[i + 1])
    # the backdrop: the commonest colour of the border, roughly; the item: every pixel well away from it
    border = [rows[0][x] for x in range(w)] + [rows[h - 1][x] for x in range(w)] + \
             [rows[y][0] for y in range(h)] + [rows[y][w - 1] for y in range(h)]
    bg = tuple(sorted(p[k] for p in border)[len(border) // 2] for k in range(3))
    item = [p for row in rows for p in row if sum(abs(p[k] - bg[k]) for k in range(3)) > 40]
    share = round(len(item) / (w * h), 2)
    if len(item) < w * h // 20:                        # the item is the backdrop's colour (white on white)
        item = [p for row in rows for p in row]
    median = tuple(sorted(p[k] for p in item)[len(item) // 2] for k in range(3))
    return f"{bits:016x}", tuple(round(v, 1) for v in lab(median)), share


def shape_apart(a, b):
    """How many of the 64 shape bits differ."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def colour_apart(a, b):
    """The distance between two Lab colours (CIE76: about 2 is barely seen, 10 is plainly another shade)."""
    return math.dist(a, b)


# ---------------------------------------------------------------- reading photos, with the cache

def photo_key(url):
    """What identifies a photo: its address without the query (Shopify's own address for it)."""
    return (url or "").split("?")[0].replace("http://", "https://")


def readable(url):
    return bool(url) and ("cdn.shopify.com" in url or "/cdn/shop/" in url)


def load_cache():
    out = {}
    try:
        for line in open(CACHE, encoding="utf-8"):
            row = line.rstrip("\n").split("\t")
            if len(row) == 7:                          # older rows, without the item's share, are read again
                key, shape, l, a, b, share, day = row
                out[key] = [shape, (float(l), float(a), float(b)), float(share), day]
    except FileNotFoundError:
        pass
    return out


def save_cache(cache):
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8", newline="\n") as f:
        for key, (shape, (l, a, b), share, day) in sorted(cache.items()):
            if day >= cutoff:
                f.write(f"{key}\t{shape}\t{l}\t{a}\t{b}\t{share}\t{day}\n")


def fetch(url):
    small = photo_key(url) + "?width=32&height=32&crop=center&format=png"
    try:
        req = urllib.request.Request(small, headers={"User-Agent": UA, "Accept": "image/png"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return signature(r.read())
    except Exception:
        return None


def signatures(urls, cache, limit=20000, workers=8):
    """{photo key: (shape, lab, share)} for these photos: from the cache, else read now (at most `limit` new ones a run,
    from Shopify's CDN, eight at a time). Marks every one needed today, so the cache keeps it."""
    today = datetime.date.today().isoformat()
    keys = {photo_key(u): u for u in urls if readable(u)}
    new = [k for k in keys if k not in cache][:limit]
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for k, sig in zip(new, pool.map(fetch, [keys[k] for k in new])):
            if sig:
                cache[k] = [sig[0], sig[1], sig[2], today]
    out = {}
    for k in keys:
        if k in cache:
            cache[k][3] = today
            out[k] = tuple(cache[k][:3])
    return out, len(new)
