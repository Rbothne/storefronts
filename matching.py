#!/usr/bin/env python3
"""Which listings at different stores are the same product, and what each store asks for it.

build.py calls match() once all listings of a run are known: the sale list (docs/data.json) and every store's
catalogue (data/catalog/). A listing that is the same item as a listing at *another* store gets the product's id,
`m`, in data.json and the brand files; docs/matches/<n>.json then holds, per product, every store's current offer
and the stores that sold it before (from the tracking files). The page merges a product's tiles into one and opens
the comparison from there.

Two things say two listings are one item, and only with the same brand:
  - the maker's style code, written into the title, the address, the id or the picture's file name
    (`S57WS0517`, `80D1403080`, `ir8176-002`)
  - the same image file, which boutiques that resell each other's stock (or the brand's own photos) reuse
Titles alone never make a match: on their own they merged black and beige versions of the same shorts and built a
449-listing junk group. A different colour is a different product, so a code shared by several colours (some
makers leave the colour out of it) is split by colour, and a listing that names none is left out of such a code.
A code that is only part of an item (a fabric shared by a dress and a shirt), a store with several different
listings under one key, shoes beside clothes, and full prices three times apart all stop a match too.
Wrong merges are worse than missed ones: anything doubtful is dropped.

Run  py matching.py  to match the files on disk and print the groups for a look (nothing is written).
"""
import collections
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
MATCH_DIR = os.path.join(ROOT, "docs", "matches")
MATCH_FILES = 256          # the page fetches one of these when a comparison opens: bucket() in docs/index.html
BIG = 12                   # a product at more stores than this is far more likely a shared code than one item

# The maker's style code: 6 or more letters and digits, at least 3 of them digits. Not a measure ("100ml"),
# a season ("fw2024", "ss25"), a year range, or a long hex string (a file hash, not a code).
TOKEN = re.compile(r"[a-z0-9]{6,}")
MEASURE = re.compile(r"\d+(ml|cl|l|cm|mm|m|oz|in|inch|g|gr|kg|lb|ct|pcs|pc|pack|mah|w)")
SEASON = re.compile(r"(ss|fw|aw|pf|sp|fa|ho|su|pre|resort|spring|summer|fall|winter|cruise)\d{2,4}|\d{2,4}(ss|fw|aw)|(19|20)\d\d(19|20)\d\d")
HEXISH = re.compile(r"[0-9a-f]{16,}")
DATE = re.compile(r"(19|20)\d\d[01]\d[0-3]\d\d*")
DULL_CODE = re.compile(r"(screenshot|screen|img|dsc|dscf|image|photo|pic|scan|copy|edit|final|untitled)\d")

# Colour words. Only used to keep colours apart, so a missing word costs a match at most; a word that is often not
# a colour ("rose" gold, "stone" wash) is still a colour here, since a false difference only drops a match.
# Patterns and materials (multicolour, leopard, camo, denim) are left out: stores name them instead of a colour.
COLOURS = {c: c for c in """black white blue red green navy grey gray beige brown pink cream ivory ecru khaki olive
    yellow orange purple tan sand stone burgundy bordeaux camel charcoal silver gold taupe mint lilac lavender
    coral teal turquoise indigo mustard rust chocolate espresso mocha nude blush fuchsia magenta maroon wine
    plum aubergine cobalt sky ochre bone chalk off-white offwhite natural tobacco
    cognac caramel petrol sage forest emerald lime aqua anthracite graphite slate pewter bronze copper
    nero bianco blu rosso verde grigio marrone noir blanc bleu rouge vert gris marron rose schwarz weiss
    """.split()}
COLOURS.update({"gray": "grey", "grigio": "grey", "gris": "grey", "nero": "black", "noir": "black", "schwarz": "black",
                "bianco": "white", "blanc": "white", "weiss": "white", "blu": "blue", "bleu": "blue", "rosso": "red",
                "rouge": "red", "verde": "green", "vert": "green", "marrone": "brown", "marron": "brown",
                "offwhite": "ecru", "off-white": "ecru", "ivory": "ecru", "cream": "ecru", "bordeaux": "burgundy"})
# Image names that say nothing about the item: a camera's numbering, a placeholder, a bare number or hash.
DULL_IMAGE = re.compile(r"(img|dsc|dscf|dsc_|_mg|photo|image|picture|pic|untitled|screenshot|scan|p)[_-]?\d+(_\d+)*"
                        r"|\d+|[0-9a-f-]{20,}|.*(placeholder|no[-_]?image|default|coming[-_]?soon|logo|blank).*", re.I)


def brand_key(name):
    """The page's brandKey and build.brand_key: letters and digits, without "men's"/"women's"."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(wo)?mens\b", "", (name or "").lower(), flags=re.A))


def bucket(m):
    """The file a product's offers are in (the page's matchFile does the same)."""
    n = 0
    for ch in m:
        n = (n * 31 + ord(ch)) % MATCH_FILES
    return n


def handle(listing_id):
    """The part of an id after the host: a Shopify handle, a path, a shop's own product number."""
    return listing_id.split("/", 1)[1] if "/" in listing_id else listing_id


def colours(text, brand):
    """The colours a title or address names, in one spelling each. The brand's own words don't count:
    Off-White and Stone Island are not colours."""
    skip = set(re.findall(r"[a-z]+", (brand or "").lower()))
    out = set()
    for w in re.findall(r"[a-z]+(?:-white)?", text.lower()):
        if w in COLOURS and w not in skip:
            out.add(COLOURS[w])
    return frozenset(out)


def codes(text, bkey):
    """The style codes in a title, address or id (lower case)."""
    out = set()
    for t in TOKEN.findall(text.lower()):
        digits = sum(ch.isdigit() for ch in t)
        if digits < 3 or t == bkey or t in bkey or MEASURE.fullmatch(t) or SEASON.fullmatch(t) or HEXISH.fullmatch(t):
            continue
        if digits == len(t) and (len(t) < 8 or DATE.fullmatch(t)):   # a price, a count, a date
            continue
        if DULL_CODE.match(t):                       # "screenshot2026", "img20260512": a camera or a desktop, not a maker
            continue
        out.add(t)
    return out


def image_name(url):
    """An image's file name without the folder, query, Shopify's size and copy suffixes, or the extension."""
    s = (url or "").split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1].lower()
    s = re.sub(r"\.(jpe?g|png|webp|avif|gif|tiff?)$", "", s)
    s = re.sub(r"_(\d+x\d*|x\d+|small|medium|large|grande|master|compact|pico|icon|thumb)$", "", s)
    s = re.sub(r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "", s)   # Shopify's "same name again" suffix
    if len(s) < 10 or DULL_IMAGE.fullmatch(s):
        return ""
    return s


def compatible(a, b):
    """Two spellings of one brand: the same, or one inside the other ("acne" / "acnestudios", "margiela" /
    "maisonmargiela"). The evidence is a shared code or picture, so a looser brand test costs little."""
    return a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a))


class Groups:
    """Union-find over listing ids."""
    def __init__(self):
        self.up = {}

    def find(self, x):
        up = self.up
        root = x
        while up.get(root, root) != root:
            root = up[root]
        while x != root:
            up[x], x = root, up.get(x, x)
        return root

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        self.up.setdefault(ra, ra)                     # every joined listing is a key, roots included
        if ra != rb:
            self.up[max(ra, rb)] = min(ra, rb)


def store_of(listing_id):
    return listing_id.split("/", 1)[0]


def colour_parts(ids, info):
    """Split listings by the colours they name. Each colour that no other named colour sits inside ("navy" inside
    "blue navy") starts a part; a listing joins the one part whose colour it names. A listing that names none, or
    whose colours fit two parts ("black white" beside "black" and "white"), can't be placed and is left out.
    Listings that name one colour, or none, stay together."""
    named = {info[x][1] for x in ids if info[x][1]}
    least = [a for a in named if not any(b < a for b in named)]
    if len(least) <= 1:
        return [ids]
    parts = collections.defaultdict(list)
    for x in ids:
        fits = [a for a in least if info[x][1] and a <= info[x][1]]
        if len(fits) == 1:
            parts[fits[0]].append(x)
    return list(parts.values())


def kind_parts(ids, listings):
    """Shoes apart from everything else: a collaboration's code or campaign picture can be shared by the shoe and
    the shirt. Other kinds are not compared, since stores file the same jacket under outerwear and tops."""
    shoes = [x for x in ids if listings[x].get("kind") == "footwear"]
    if not shoes or len(shoes) == len(ids):
        return [ids]
    return [shoes, [x for x in ids if listings[x].get("kind") not in ("footwear", None, "", "other")]]


def part_code(code, members, mixed):
    """True when a code names only part of an item: two listings share it, but each also carries another code
    the other lacks. Lemaire names its pictures style_fabric_colour ("sh1326_lf1500_br477"), so a dress and a
    shirt in one cloth share "lf1500"; a Margiela code with another fabric number is another item. The listings
    can still be joined by their own style codes."""
    others = [mixed[x] - {code} for x in members if code in mixed.get(x, ())]
    others = [o for o in others if o]
    for n, a in enumerate(others):
        for b in others[n + 1:]:
            if not a & b and not any(x in y or y in x for x in a for y in b):
                return True
    return False


def one_each(ids, info):
    """The listings, less those of any store that has several different ones among them."""
    per_store = collections.defaultdict(set)
    for x in ids:
        per_store[store_of(x)].add(info[x][2])
    return [x for x in ids if len(per_store[store_of(x)]) == 1]


def consistent(members, info, by_colour):
    """Split one key's listings (a code or an image) into the sets that may be one product: by brand, and by
    colour for a code (an image is one colour already). A store with two listings under one key lists two
    things (two colours a title doesn't name, a set and its piece), and can't say which is which: its listings
    are left out, unless they are one listing put up twice (same title, price and picture: shops list each
    colour separately under one name, at one price, so the picture is what tells them apart). Only sets at two
    or more stores are kept."""
    out = []
    by_brand = []                                      # [brand keys, ids] for brands compatible with each other
    for lid in members:
        b = info[lid][0]
        for row in by_brand:
            if any(compatible(b, x) for x in row[0]):
                row[0].add(b)
                row[1].append(lid)
                break
        else:
            by_brand.append([{b}, [lid]])
    for _, ids in by_brand:
        for s in colour_parts(ids, info) if by_colour else [ids]:
            s = one_each(s, info)
            if len(s) > 1:
                out.append(s)
    return out


def full_usd(it):
    """What the item costs at full price, in dollars: its "was" price where there is one."""
    usd, price = it.get("usd"), it.get("price")
    if usd is None or not price:
        return None
    return usd * (it.get("was") or price) / price


def group(listings):
    """listings: id -> item (title, brand, url, img, store...). Returns a list of products, each a list of ids at
    two or more stores, and the codes that identify each one (for finding its past sellers)."""
    info, by_code, by_image, mixed = {}, collections.defaultdict(list), collections.defaultdict(list), {}
    for lid, it in listings.items():
        b = brand_key(it.get("brand") or it.get("store_name"))
        if not b:
            continue
        path = re.sub(r"^https?://[^/]+", "", (it.get("url") or "").split("?")[0])
        name = image_name(it.get("img"))
        # the picture's file name often carries the maker's code with its colour ("ir8176-002"), where the title
        # has only the model name
        text = " ".join([it.get("title") or "", path, handle(lid), name])
        # what makes two listings at one store the same listing put up twice: title, price and picture
        same = (re.sub(r"[^a-z0-9]", "", (it.get("title") or "").lower()), it.get("price"), image_name(it.get("img")) or it.get("img"))
        info[lid] = (b, colours(" ".join([it.get("title") or "", path]), it.get("brand")), same)
        found = codes(text, b)
        mixed[lid] = {c for c in found if not c.isdigit()}   # bare numbers are often the shop's own ids
        for c in found:
            by_code[c].append(lid)
        if name:
            by_image[name].append(lid)

    g = Groups()
    keys_of = collections.defaultdict(set)             # listing -> the codes it was joined by
    for index, tag in ((by_code, "c"), (by_image, "i")):
        for key, members in index.items():
            if len(members) < 2 or len({store_of(x) for x in members}) < 2:
                continue
            if tag == "c" and part_code(key, members, mixed):
                continue
            for s in consistent(members, info, tag == "c"):
                for x in s[1:]:
                    g.join(s[0], x)
                if tag == "c":
                    for x in s:
                        keys_of[x].add(key)

    comps = collections.defaultdict(list)
    for lid in g.up:
        comps[g.find(lid)].append(lid)
    products = []
    for ids in comps.values():
        # Joining codes and pictures can chain two colours together through a listing that names neither:
        # split again by colour, as for a single code.
        for p in (q for part in colour_parts(ids, info) for q in kind_parts(part, listings)):
            p = one_each(p, info)                      # a code and a picture can each bring in a different one
            stores = {store_of(x) for x in p}
            if len(stores) < 2 or len(stores) > BIG:
                continue
            # one item costs much the same everywhere at full price; three times as much is another item
            # (a set and a single piece, a kid's and an adult's) under a shared code
            full = sorted(v for v in (full_usd(listings[x]) for x in p) if v)
            if full and full[-1] > 3 * full[0]:
                continue
            products.append((sorted(p), set().union(*(keys_of[x] for x in p)), info[p[0]][0],
                             frozenset().union(*(info[x][1] for x in p))))
    return products


def product_id(ids):
    """A short id that stays the same while the product's first listing does."""
    return hashlib.sha1(ids[0].encode()).hexdigest()[:7]


def offer(it, rec):
    """One store's listing of a product, for the comparison panel. rec: its tracking record, for the lowest price
    the store has asked for it ("low": [price, day]), when that was well under today's."""
    o = {k: it[k] for k in ("id", "store", "store_name", "title", "price", "cur", "usd", "was", "off", "url", "img")
         if it.get(k) not in (None, "", 0)}
    o["price"] = it.get("price")
    if it.get("sizes"):
        o["sizes"] = it["sizes"]
    if it.get("sz"):
        o["sz"] = it["sz"]
    if it.get("avail") is False:
        o["avail"] = False
    stocked = [p for p in (rec or {}).get("h", []) if p[3] and p[1]]
    if stocked and it.get("price"):
        low = min(stocked, key=lambda p: p[1])
        if low[1] < it["price"] * 0.97:                # 3%: exchange-rate drift at stores quoting converted prices
            o["low"] = [low[1], low[0][:10]]
    return o


def past_offers(state, products, current, rates):
    """Stores that sold a product before and don't now: from the tracking files, whose records keep a listing's id
    (and so its handle, which usually carries the style code) and brand. -> {product index: [past offer, ...]}"""
    by_code = collections.defaultdict(list)            # code -> [(product index, brand key, colours)]
    for n, (ids, keys, bkey, named) in enumerate(products):
        for c in keys:
            by_code[c].append((n, bkey, named))
    out = collections.defaultdict(list)
    for host, st in state.items():
        cur = st.get("cur") or "USD"
        for lid, rec in st.get("products", {}).items():
            if lid in current or not rec.get("h"):
                continue
            b = brand_key(rec.get("b"))
            if not b:
                continue
            hits = {n for c in codes(handle(lid), b) for n, pb, named in by_code.get(c, ())
                    if compatible(b, pb) and (not named or not colours(handle(lid), rec.get("b")) or colours(handle(lid), rec.get("b")) <= named)}
            if len(hits) != 1:                         # no product, or a code several products share: not placeable
                continue
            h = rec["h"]
            stocked = [p for p in h if p[3]]
            if not stocked:
                continue
            last = stocked[-1]
            # when it was last there: the day it went (out of stock, or out of the catalogue), else its last reading
            after = [p for p in h if p[0] > last[0] and not p[3]]
            seen = (after[0][0] if after else rec.get("x") or last[0])[:10]
            rate = rates.get(cur)
            out[hits.pop()].append({"store": host, "price": last[1], "cur": cur, "off": last[2] or 0,
                                    "usd": round(last[1] / rate, 2) if rate else None, "seen": seen})
    return out


def match(listings, state, rates, names):
    """listings: id -> item, everything listed or in a catalogue this run. Writes docs/matches/ and returns
    {listing id: [product id, stores carrying it]}, which build.py puts on the listings as `m` and `mn` (the
    page's "N stores" label needs the count before the panel's file is fetched)."""
    products = group(listings)
    past = past_offers(state, products, listings, rates)
    files, marks = [{} for _ in range(MATCH_FILES)], {}
    for n, (ids, _, _, _) in enumerate(products):
        m = product_id(ids)
        offers = {}
        for lid in ids:                                # one offer per store: its cheapest listing of it
            it = listings[lid]
            o = offers.get(it["store"])
            if o is None or (it.get("usd") or it.get("price") or 1e12) < (o.get("usd") or o.get("price") or 1e12):
                offers[it["store"]] = offer(it, state.get(it["store"], {}).get("products", {}).get(lid))
        for lid in ids:
            marks[lid] = [m, len(offers)]
        rows = sorted(offers.values(), key=lambda o: o.get("usd") or o.get("price") or 1e12)
        hist, before = [], set()
        for p in sorted(past.get(n, []), key=lambda p: p["seen"], reverse=True):
            if p["store"] not in offers and p["store"] not in before:   # the latest time at each store gone
                before.add(p["store"])
                p["store_name"] = names.get(p["store"], p["store"])
                hist.append(p)
        files[bucket(m)][m] = {"offers": rows, "past": hist[:12]}
    os.makedirs(MATCH_DIR, exist_ok=True)
    for n, found in enumerate(files):
        with open(os.path.join(MATCH_DIR, f"{n}.json"), "w", encoding="utf-8", newline="\n") as f:
            f.write("{\n" + ",\n".join(json.dumps(k) + ":" + json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                                       for k, v in sorted(found.items())) + "\n}\n")
    return marks


def load_listings(data_path, catalog_dir):
    """Every listing on disk: the sale list, then each store's catalogue (as the page fills it in: fill())."""
    listings, data = {}, {"items": []}
    if data_path:
        data = json.load(open(data_path, encoding="utf-8"))
    for it in data["items"]:
        if it.get("price") is not None:
            listings[it["id"]] = it
    for file in sorted(os.listdir(catalog_dir)) if os.path.isdir(catalog_dir) else []:
        for it in json.load(open(os.path.join(catalog_dir, file), encoding="utf-8")):
            if it["id"] in listings:
                continue
            it.setdefault("url", "https://" + it["id"].replace("/", "/products/", 1))
            if it.get("usd") is None and it.get("cur") == "USD":
                it["usd"] = it.get("price")
            it.setdefault("off", 0)
            listings[it["id"]] = it
    return data, listings


if __name__ == "__main__":
    # py matching.py [data.json] [catalog dir]: group what is on disk and print a sample for judging the matches
    import random
    data, listings = load_listings(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "data.json"),
                                   sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "data", "catalog"))
    import time
    t = time.time()
    products = group(listings)
    print(f"{len(listings)} listings; {len(products)} products at 2+ stores, {sum(len(p[0]) for p in products)} listings, "
          f"{time.time() - t:.1f} s")
    sizes = collections.Counter(len({store_of(x) for x in p[0]}) for p in products)
    print("stores per product:", dict(sorted(sizes.items())))
    show = lambda p: [f"{listings[x]['store_name']}: {listings[x]['title'][:60]} ({listings[x].get('cur')} {listings[x].get('price')})" for x in p[0]]
    random.seed(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    for p in random.sample(products, min(15, len(products))):
        print("\n  " + "\n  ".join(show(p)))
    print("\nLARGEST")
    for p in sorted(products, key=lambda p: -len(p[0]))[:5]:
        print("\n  " + "\n  ".join(show(p)))
