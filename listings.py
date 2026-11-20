#!/usr/bin/env python3
"""Reading the catalogue of a store that is not an open Shopify feed.

build.py reads Shopify stores from their products.json. Other shops print their products in their own listing
pages, so a store in stores.json with "browse": true and a "read" setting is read here instead:

    "read": "woocommerce"   the WooCommerce Store API (/wp-json/wc/store/v1/products), whole catalogue as JSON
    "read": "netsuite"      a NetSuite SuiteCommerce shop's items API (/api/items), whole catalogue as JSON
    "read": "listing"       the store's own category pages ("pages"), page after page, products read from:
        "via": "ld"      the JSON-LD Product entries search engines are given
        "via": "json"    the product list the page carries for its own scripts (__NEXT_DATA__ and the like)
        "via": "tiles"   the product tiles of the grid (data-pid="..."; Salesforce Commerce Cloud and others)
        "via": "links"   links to product pages ("link": a pattern of their address) and the prices after them

Paging ("paging"): a template added to each page's address, "?start={start}&sz=48" (with "size": 48) or "?page={n}"
(page 1 is the plain address), or "next" to follow the page's own rel="next" link; none reads one page per address.
A listing stops at the first page that brings no product not seen already.

The size labels a listing prints are read too, and the sold-out ones left out, so an item on the site says what
is still in stock: from a store's own product data (a list of sizes), from the variants of a JSON-LD Product, or
from the size swatches of a tile ("size_class" in stores.json names the store's element for one size when the
usual data-size attributes are not there).

Every address is checked against the robots.txt of its host first (wildcards included), and skipped when it is
disallowed. Several stores allow their category pages but not the faster forms of them their own scripts use
(Search-UpdateGrid, ?format=json, ?sz=300); this keeps the build on the allowed side.

What comes back for each product is a plain dict (title, url, price, was, cur, avail, img, brand, ptype),
which build.py turns into a site item the way it does a Shopify product.
"""
import html as htmllib
import json
import os
import re
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse

# ---------------------------------------------------------------- robots.txt

_ROBOTS = {}


def robots_rules(fetch, origin):
    """The Allow / Disallow lines that apply to us (the "*" group) on one host, read once per run."""
    if origin in _ROBOTS:
        return _ROBOTS[origin]
    status, body = fetch(origin + "/robots.txt")
    rules, groups, agents, in_rules = [], {}, [], False
    if status == 200 and "<html" not in body[:500].lower():
        for line in body.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (s.strip() for s in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                for a in agents:
                    groups.setdefault(a, []).append((key == "allow", value))
        rules = groups.get("*", [])
    _ROBOTS[origin] = rules
    return rules


def _robots_rx(path):
    rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in path.rstrip("$"))
    return re.compile(rx + ("$" if path.endswith("$") else ""))


def allowed(fetch, url):
    """True unless the host's robots.txt disallows this address for every agent ("*"). The longest matching rule
    wins, and Allow wins a tie, as Google reads the file."""
    u = urlparse(url)
    target = (u.path or "/") + ("?" + u.query if u.query else "")
    best = (-1, True)
    for allow, path in robots_rules(fetch, f"{u.scheme}://{u.netloc}"):
        if not path:
            continue                                     # "Disallow:" with nothing after it allows everything
        if _robots_rx(path).match(target) and (len(path), allow) > best:
            best = (len(path), allow)
    return best[1]


# ---------------------------------------------------------------- small helpers

def num(x):
    """A number from 12, "12.50", "$1,234", "¥12,000" or "12,000円"; None otherwise."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        m = re.fullmatch(r"\s*(?:US|C|A)?[$¥￥£€₩]?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(?:円|USD|JPY|KRW)?\s*", x)
        if m:
            return float(m.group(1).replace(",", "") + (m.group(2) or ""))
    return None


def text_of(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def clean_title(t):
    t = re.sub(r"\s+", " ", htmllib.unescape(str(t or ""))).strip()
    return "" if t.lower() in ("null", "undefined", "none") or not re.search(r"[^\W\d_]", t) else t


# Prices as shops print them: $1,234.50, US$ 98, USD 35.00, ¥12,000, 12,000円, 12,500 yen, ₩60,000
MONEY = re.compile(r"(?:(?:US\$|C\$|A\$|\$|¥|￥|£|€|₩|\bUSD\s|\bJPY\s)\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?)(?![\d,])"
                   r"|(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?\s?(?:円|yen\b|USD\b|JPY\b|KRW\b|원)", re.I)
# The struck-through price is the original: <s>, <del>, or an element whose class (or inline style) says so, the
# first price after it
STRUCK = re.compile(r"<(s|del|strike)\b[^>]*>(.*?)</\1>"
                    r"|(?:class=\"[^\"]*(?:strike|line-through|list-price|price__list|price--list|old-price|was-price"
                    r"|price-was|original|regular|standard|compare|before|retail|consumer|teika)[^\"]*\""
                    r"|style=\"[^\"]*line-through[^\"]*\")[^>]*>(.{0,300})",
                    re.I | re.S)


def money(text):
    out = []
    for m in MONEY.finditer(text):
        whole, frac = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        out.append(float(whole.replace(",", "") + (frac or "")))
    return [x for x in out if x > 0]


def page_currency(page):
    """The currency a page names for the prices it prints, for the grids that print the amount alone. The site
    falls back to the store's "currency" when a page names none."""
    for rx in (r'"priceCurrency"\s*:\s*"([A-Z]{3})"',
               r'itemprop="priceCurrency"[^>]*content="([A-Z]{3})"',
               r'content="([A-Z]{3})"[^>]*itemprop="priceCurrency"',
               r'property="(?:og:|product:)price:currency"[^>]*content="([A-Z]{3})"'):
        m = re.search(rx, page)
        if m:
            return m.group(1)
    return ""


# ---------------------------------------------------------------- sizes a listing prints

# a label that is a size, not a colour or a quantity: XS, 10, 32x34, 9.5, 27.5cm, UK 8, One Size, 1 SZ
SIZE_LABEL = re.compile(r"(?i)^(?:(?:[2-5]?x+)?s|m|(?:[2-5]?x+)?l|o/?s|one ?size|1 ?sz|no ?size|uni|free"
                        r"|[0-9]{1,3}(?:[.,][05])?(?:\s?(?:cm|mm|in|us|uk|eu|w|l)\b)?(?:\s?[x/-]\s?[0-9]{1,3})?"
                        r"|[0-9]{1,2}[a-ehjk]{1,3})$")
# a swatch the grid prints struck through or greyed: that size is gone
SIZE_GONE = re.compile(r"(?i)unavailable|out-?of-?stock|sold-?out|soldout|notavailable|not-available|disabled"
                       r"|unselectable|crossed|strike|line-through|\boos\b")
# a swatch for something else: grids hang colours on the same attributes (data-attr-id="color")
SIZE_OTHER = re.compile(r"""(?i)\bdata-attr(?:-id|ibute)?=["'](?!size)""")
# attributes a grid hangs a size on
SIZE_ATTR = re.compile(r"""(?i)<(\w+)\b([^>]*\bdata-(?:size|size-value|sizevalue|attr-value|variation-size)"""
                       r"""=["']([^"']{1,16})["'][^>]*)>""")


def size_label(t):
    t = re.sub(r"\s+", " ", htmllib.unescape(str(t or ""))).strip().strip("()")
    return t if 1 <= len(t) <= 16 and SIZE_LABEL.match(t) else ""


def html_sizes(store, chunk):
    """The sizes a tile prints that it does not mark gone. "size_class" in stores.json names the store's element
    for one size; otherwise the data-size attributes grids usually hang a size on are read."""
    out = []
    if store.get("size_class"):
        rx = re.compile(r"""<(\w+)\b([^>]*class=["'](?:[^"']*\s)?%s(?:\s[^"']*)?["'][^>]*)>(.*?)</\1>"""
                        % re.escape(store["size_class"]), re.I | re.S)
        found = [(m.group(2), size_label(text_of(m.group(3)))) for m in rx.finditer(chunk)]
    else:
        found = [(m.group(2), size_label(m.group(3))) for m in SIZE_ATTR.finditer(chunk)]
    for attrs, label in found:
        if label and not SIZE_GONE.search(attrs) and not SIZE_OTHER.search(attrs) and label not in out:
            out.append(label)
    return out


def size_gone(e):
    """True when a size entry in a product's data says that size is not to be had."""
    if not isinstance(e, dict):
        return False
    if e.get("forceOutOfStock") or e.get("soldOut") or e.get("outOfStock"):
        return True
    said = [e.get(n) for n in ("available", "inStock", "isInStock", "orderable", "selectable", "purchasable")
            if isinstance(e.get(n), bool)]
    if said and not any(said):
        return True
    return bool(re.search(r"(?i)out|sold|unavailable", str(e.get("availability") or e.get("stockState") or "")))


def size_list(d, depth=0):
    """Whether a product's data holds a list of sizes at all, empty or not. A listing that keeps every size it has
    says, by keeping none, that a garment has gone; a product sold in one size says nothing by it."""
    for k, v in d.items() if isinstance(d, dict) else []:
        if isinstance(v, (list, dict)) and re.search(r"(?i)sizes?$|sizes?[_A-Z]", str(k)):
            return True
        if isinstance(v, dict) and depth < 2 and size_list(v, depth + 1):
            return True
    return False


def json_sizes(d, depth=0):
    """The sizes in a product's own data: under a key that says sizes, either a list (of labels or of objects
    with one) or an object filed by label. A size the data marks out of stock is left out; a list of colours or
    of whole variants is not read."""
    out = []
    for k, v in d.items() if isinstance(d, dict) else []:
        is_sizes = re.search(r"(?i)sizes?$|sizes?[_A-Z]", str(k))
        if isinstance(v, dict) and is_sizes and len(v) <= 60:
            out += [s for s in (size_label(label) for label, e in v.items() if not size_gone(e)) if s]
        elif isinstance(v, dict) and depth < 2:
            out += json_sizes(v, depth + 1)
        elif isinstance(v, list) and is_sizes and len(v) <= 60:
            for e in v:
                label = size_label(e) if isinstance(e, (str, int, float)) else ""
                if isinstance(e, dict):
                    if size_gone(e):
                        continue
                    label = next((size_label(e[n]) for n in ("size", "sizeLabel", "label", "value", "name",
                                                             "displayValue", "title") if e.get(n)), "")
                out.append(label)
    seen = []
    for s in out:
        if s and s not in seen:
            seen.append(s)
    return seen


def canon(url):
    """An address without its fragment and the query listings add (?source=plp, colour choices), to tell whether
    two listings show the same product. A query that names the product (?pid=, ?product_id=) is kept."""
    u = urlparse(url)
    ids = [f"{k}={v}" for k, v in parse_qsl(u.query) if re.fullmatch(r"(?i)(product_?)?id|pid|item_?id|goods", k)]
    return f"{u.netloc.lower()}{u.path.rstrip('/')}" + ("?" + "&".join(ids) if ids else "")


# ---------------------------------------------------------------- JSON-LD

def ld_blocks(page):
    for block in re.findall(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        for text in (block.strip(), htmllib.unescape(block.strip())):
            try:
                yield json.loads(text)
                break
            except ValueError:
                continue


def from_ld(page, base, store):
    """Products in the page's JSON-LD (an ItemList of Products, or one Product per tile)."""
    out = []
    for data in ld_blocks(page):
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(reversed(node))
            elif isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "Product" in types or "ProductGroup" in types:
                    it = ld_item(node, base)
                    if it:
                        out.append(it)
                else:
                    stack.extend(reversed([v for v in node.values() if isinstance(v, (dict, list))]))
    return out


def ld_item(node, base):
    name, url = clean_title(node.get("name")), node.get("url") or node.get("@id")
    if not name or not isinstance(url, str) or not url:
        return None
    image = node.get("image")
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        image = image.get("url") or image.get("contentUrl")
    offers = node.get("offers") or [v.get("offers") for v in node.get("hasVariant") or [] if isinstance(v, dict)]
    offers = offers if isinstance(offers, list) else [offers]
    flat = []
    for o in offers:
        if isinstance(o, dict):
            flat.extend(x for x in (o.get("offers") if isinstance(o.get("offers"), list) else [o]) if isinstance(x, dict))
    priced = [(num(o.get("price") if o.get("price") is not None else o.get("lowPrice")), o) for o in flat]
    priced = [(p, o) for p, o in priced if p]
    if not priced:
        return None
    price, offer = min(priced, key=lambda t: t[0])
    states = [str(o.get("availability", "")) for o in flat if o.get("availability")]
    avail = any(s.rsplit("/", 1)[-1].lower() in ("instock", "limitedavailability", "onlineonly", "preorder", "presale",
                                                  "backorder") for s in states) if states else None
    brand = node.get("brand")
    brand = brand.get("name") if isinstance(brand, dict) else brand if isinstance(brand, str) else ""
    # the original price, where an offer gives one: a list price beside the asking price, or the top of a range
    was = [num(o.get(k)) for o in flat for k in ("listPrice", "highPrice")]
    for o in flat:
        spec = o.get("priceSpecification")
        for sp in (spec if isinstance(spec, list) else [spec]):
            if isinstance(sp, dict) and "list" in str(sp.get("priceType", "")).lower():
                was.append(num(sp.get("price")))
    was = max([w for w in was if w], default=None)
    return {"title": name, "url": urljoin(base, url), "img": urljoin(base, image) if isinstance(image, str) else "",
            "price": price, "was": was if was and was > price else None,
            "cur": str(offer.get("priceCurrency") or ""), "avail": avail, "sizes": ld_sizes(node),
            "brand": brand or "", "ptype": str(node.get("category") or "")}


def ld_sizes(node):
    """The sizes of a JSON-LD Product: the "size" of each variant still offered."""
    out = []
    for v in node.get("hasVariant") or []:
        if not isinstance(v, dict):
            continue
        offers = v.get("offers") if isinstance(v.get("offers"), list) else [v.get("offers")]
        states = [str(o.get("availability", "")) for o in offers if isinstance(o, dict) and o.get("availability")]
        if states and not any(s.rsplit("/", 1)[-1].lower().startswith(("instock", "limited", "preorder", "presale",
                                                                       "backorder", "online")) for s in states):
            continue
        size = v.get("size")
        size = size.get("name") if isinstance(size, dict) else size
        label = size_label(size)
        if label and label not in out:
            out.append(label)
    return out


# ---------------------------------------------------------------- the page's own product data

def flight(page):
    """The lists of products in a Next.js app-router payload (the self.__next_f.push([1,"..."]) calls), which
    carries a page's own data as runs of text rather than as one JSON block. The runs are joined back together
    and every list of objects in them is read out: "products":[{...},{...}] and its like."""
    parts = []
    for m in re.finditer(r"__next_f\.push\(\[\s*1\s*,\s*(\".*?\")\s*\]\)", page, re.S):
        try:
            parts.append(json.loads(m.group(1)))
        except ValueError:
            pass
    body, dec = "".join(parts), json.JSONDecoder()
    for m in re.finditer(r"[:,]\s*(?=\[\s*\{)", body):
        try:
            value, _ = dec.raw_decode(body, m.end())
        except ValueError:
            continue
        if isinstance(value, list) and len(value) > 2:
            yield value


def embedded(page):
    """Every sizeable JSON value a page embeds for its scripts: <script type="application/json"> (Next.js's
    __NEXT_DATA__ among them), assignments like window.__PRELOADED_STATE__ = {...}, and the rows of an
    app-router payload."""
    for m in re.finditer(r"<script\b[^>]*type=[\"']application/(?:json|x-json)[\"'][^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            yield json.loads(m.group(1).strip())
        except ValueError:
            pass
    dec = json.JSONDecoder()
    for m in re.finditer(r"[\w$.\]\"']\s*=\s*(?=[{\[])", page):
        try:
            value, end = dec.raw_decode(page, m.end())
        except ValueError:
            continue
        if end - m.end() > 2000 and isinstance(value, (dict, list)):
            yield value
    yield from flight(page)


# the name of the product, most telling first: a grid's data can hold both the product's name and the name of
# the colour of one entry ("name": "Pink Aster", "productName": "Agnes Skirted Shift Romper")
NAME_KEYS = ("productName", "product_name", "styleName", "displayName", "displayname", "storedisplayname2",
             "name", "title")
URL_KEYS = ("url", "productPageUrl", "urlPath", "path", "permalink", "productUrl", "pdpUrl", "href", "link", "slug",
            "handle", "fullUrl", "absUrl")
# a price field's name says which price it is: compare-at, original, list ... is the "was" price
WAS_KEY = re.compile(r"compare|original|list|regular|base|old|retail|standard|strike|was|full|msrp", re.I)
NOW_KEY = re.compile(r"sale|discount|final|current|offer|effective|special|now|promo|selling|customer", re.I)
# a price for something other than the product: a membership rate, an instalment, an extra, a basket total
NOT_PRICE = re.compile(r"member|loyalty|per_?unit|installment|klarna|afterpay|shipping|tax|percent|saving|max"
                       r"|monogram|engrav|total|bundle|add_?on|gift_?wrap|deposit", re.I)
# parts of a product's data that hold other things' prices (its variants, related products)
SKIP_TREE = re.compile(r"^(?:(?:selected)?variants?|variations?|swatch(?:es)?|colou?rs?|sizes?|children|related\w*"
                       r"|recommend\w*|bundles?)$", re.I)


def price_fields(d, path="", depth=0, out=None):
    """(path, number) for each price in a product's data, not looking inside lists (variants, other products)."""
    out = [] if out is None else out
    for k, v in d.items():
        p = f"{path}.{k}" if path else str(k)
        if isinstance(v, dict):
            if depth < 3 and not SKIP_TREE.search(str(k)):
                price_fields(v, p, depth + 1, out)
        elif not isinstance(v, list) and re.search(r"price", p, re.I) and not NOT_PRICE.search(p):
            n = num(v)
            if n and n > 0 and not re.search(r"(currency|code|id|count|type|flag|text)$", p, re.I):
                out.append((p, n))
    return out


IMG_KEYS = ("url", "src", "urlOriginal", "disBaseLink", "link", "imageDefault", "imageUrl", "href", "path",
            "contentUrl", "default")


def picture(v, depth=0):
    """The first photo address in a product's image field, however the shop wraps it: a string, an object with
    an address under one of the usual names, or a list of either."""
    if isinstance(v, str):
        return v if re.search(r"(?i)^(https?:)?//|^/|\.(jpe?g|png|webp|avif)", v) and not BADGE.search(v) else ""
    if depth >= 3:
        return ""
    if isinstance(v, dict):
        return next((p for p in (picture(v.get(k), depth + 1) for k in IMG_KEYS) if p), "")
    if isinstance(v, list):
        return next((p for p in (picture(x, depth + 1) for x in v[:12]) if p), "")
    return ""


def split_prices(fields):
    """(what is asked now, the original price) from the prices found in a product's data: a field whose name says
    compare-at, original, list ... is the original, and the lowest of the rest is what is asked."""
    is_was = lambda p: WAS_KEY.search(p) and not NOW_KEY.search(p.rsplit(".", 1)[-1])
    was, now = [n for p, n in fields if is_was(p)], [n for p, n in fields if not is_was(p)]
    if not now:
        now, was = was, []
    price = min(now)
    return price, (max(was) if was and max(was) > price else None)


def json_by_id(page):
    """What a page's own data says about each product id in it: a grid that prints no price in its tiles can
    still hand its analytics the list of what it shows ("impressions": [{"productID": ..., "price": ...}],
    Johnny Was). Used to price such a tile."""
    out = {}
    for value in embedded(page):
        stack = [value]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            ids = [str(node[k]) for k in ("productID", "productId", "product_id", "id", "pid", "sku", "itemid")
                   if isinstance(node.get(k), (str, int)) and not isinstance(node.get(k), bool)]
            fields = price_fields(node)
            if not ids or not fields:
                continue
            price, was = split_prices(fields)
            it = {"price": price, "was": was,
                  "title": next((clean_title(node[k]) for k in NAME_KEYS
                                 if isinstance(node.get(k), str) and clean_title(node[k])), ""),
                  "cur": next((node[k] for k in ("currency", "currencyCode", "priceCurrency")
                               if isinstance(node.get(k), str)), ""),
                  "ptype": node.get("category") if isinstance(node.get("category"), str) else ""}
            for i in ids:
                out.setdefault(i.lower(), it)
            if it["title"]:
                out.setdefault(slug(it["title"]), it)
    return out


def by_id(table, pid):
    """What `table` holds for this tile's id, by the id itself or by the part of it before the colour
    ("d31326-o-ivoni" is colour "ivoni" of product "d31326-o")."""
    pid = pid.lower()
    if pid in table:
        return table[pid]
    keys = [k for k in table if pid.startswith(k) and len(k) > 3]
    return table[max(keys, key=len)] if keys else None


def slug(name):
    """A product's name as a listing writes it into an address: "Elisa Luxe Gold Short Pendant Necklace" ->
    "elisa-luxe-gold-short-pendant-necklace"."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(name or "").lower())).strip("-")


def by_address(table, url):
    """What `table` holds for the product at this address, matched on the part of the address that spells its
    name or its id. For a grid that keeps the addresses in its links and the prices in its data."""
    for part in reversed([p for p in urlparse(url).path.split("/") if p]):
        key = slug(part.rsplit(".", 1)[0])
        if key in table:
            return table[key]
    return None


def deep_picture(v, depth=0):
    """Any product photo in a product's data, wherever the shop filed it: J.Jill keeps the grid's thumbnail as a
    reference its scripts resolve, but the photo of each colour it sells is there in full. Used only when the
    usual image fields hold nothing."""
    if isinstance(v, str):
        return v if re.search(r"(?i)\.(jpe?g|png|webp|avif)(\?|$)", v) and not BADGE.search(v) else ""
    if depth >= 4:
        return ""
    for x in (list(v.values()) if isinstance(v, dict) else v if isinstance(v, list) else [])[:20]:
        found = deep_picture(x, depth + 1)
        if found:
            return found
    return ""


def json_item(d, base, store):
    if isinstance(d.get("product"), dict) and not any(isinstance(d.get(k), str) for k in NAME_KEYS):
        # a search hit: its prices beside the product they are for (Rolla's)
        d = {**d["product"], **{k: v for k, v in d.items() if k != "product"}}
    name =next((clean_title(d[k]) for k in NAME_KEYS if isinstance(d.get(k), str) and clean_title(d[k])), None)
    if not name:
        # shops that file the words of a product apart from its prices: {"content": {"title": ...}} (Burberry)
        for box in ("content", "fields", "attributes", "details", "info", "text"):
            inner = d.get(box)
            if isinstance(inner, dict):
                name = name or next((clean_title(inner[k]) for k in NAME_KEYS
                                     if isinstance(inner.get(k), str) and clean_title(inner[k])), None)
    url = next((d[k] for k in URL_KEYS if isinstance(d.get(k), str) and d[k].strip()), None)
    if not url and store.get("product_url"):
        # Salesforce's composable storefronts keep a product's id, not its address: "/us/en/product/{id}"
        pid = next((str(d[k]) for k in ("productId", "product_id", "masterId", "id", "sku")
                    if isinstance(d.get(k), (str, int)) and not isinstance(d.get(k), bool)), None)
        url = store["product_url"].format(id=pid) if pid else None
    if not name or not url:
        return None
    fields = price_fields(d)
    if not fields:
        return None
    price, was = split_prices(fields)
    if "://" not in url and not url.startswith("/"):
        url = (store.get("product_path") or "/") + url
    cur = next((d[k] for k in ("currency", "currencyCode", "priceCurrency") if isinstance(d.get(k), str)), "")
    for p, _ in fields:                                  # a currency stored next to the price
        parent = d
        for k in p.split(".")[:-1]:
            parent = parent.get(k, {})
        cur = cur or next((parent[k] for k in ("currency", "currencyCode") if isinstance(parent.get(k), str)), "")
    avail = None
    for k in ("availableForSale", "isInStock", "inStock", "available", "isinstock", "orderable"):
        if isinstance(d.get(k), bool):
            avail = d[k]
    stock = str(d.get("stockState") or d.get("availability") or d.get("inventoryStatus") or "")
    if stock:
        avail = not re.search(r"out|sold|unavailable", stock, re.I)
    inv = d.get("inventory")
    if isinstance(inv, dict) and isinstance(inv.get("isInStock"), bool):
        avail = inv["isInStock"]
    for box in ("types", "flags", "status", "stock"):      # {"types": {"isSoldOut": false}} (Burberry)
        inner = d.get(box)
        if isinstance(inner, dict):
            for k in ("isSoldOut", "soldOut", "outOfStock"):
                if isinstance(inner.get(k), bool):
                    avail = not inner[k]
            for k in ("isInStock", "inStock", "available", "orderable"):
                if isinstance(inner.get(k), bool):
                    avail = inner[k]
    brand = d.get("brand") or d.get("vendor") or ""
    brand = brand.get("name", "") if isinstance(brand, dict) else brand if isinstance(brand, str) else ""
    img = ""
    for k, v in d.items():
        # a photo under any of the names shops give one: a string, an object, or a list of either. The key can
        # carry the query a GraphQL cache asks it with: images({"first":2}) (Figs).
        if not re.match(r"(?i)(image|img|photo|media|thumb|picture)", str(k)) or img:
            continue
        img = picture(v)
    img = img or deep_picture(d)
    if store.get("image"):
        # a store whose product data names its photo by an id or a path: "image" is where (dotted keys),
        # "image_url" the address it goes into ("https://img.abercrombie.com/is/image/anf/{}")
        v = d
        for k in store["image"].split("."):
            v = v.get(k) if isinstance(v, dict) else None
        if isinstance(v, str) and v:
            img = store.get("image_url", "{}").format(v)
    ptype = d.get("productType") or d.get("product_type") or d.get("category") or ""
    return {"title": name, "url": urljoin(base, url), "img": urljoin(base, img) if img else "", "price": price,
            "was": was, "cur": cur, "avail": avail, "sizes": json_sizes(d), "brand": brand,
            "ptype": ptype if isinstance(ptype, str) else ""}


def from_json(page, base, store):
    """Products in the data the page embeds for its scripts: any object with a name, an address and a price."""
    def walk(node, depth=0):
        # products inside an object win over the object itself: a collection page's data has a title, a handle and
        # somewhere a price too, but it is the list of products in it that is wanted
        if depth > 40:
            return []
        if isinstance(node, list):
            return [it for v in node for it in walk(v, depth + 1)]
        if not isinstance(node, dict):
            return []
        inner = [it for v in node.values() if isinstance(v, (dict, list)) for it in walk(v, depth + 1)]
        if inner:
            return inner
        it = json_item(node, base, store)
        return [it] if it else []

    return [it for value in embedded(page) for it in walk(value)]


# ---------------------------------------------------------------- product tiles (data-pid="...")

PID = re.compile(r"data-(?:pid|itemid|product-id|productid)=\"([^\"]{2,60})\"")
HREF = re.compile(r"<a\b[^>]*?\bhref=\"([^\"#][^\"]*)\"", re.I)
IMG = re.compile(r"<(?:img|source)\b[^>]*?\b(?:data-src|src|data-srcset|srcset)=\"([^\" ]+)", re.I)
NAMES = [re.compile(p, re.I | re.S) for p in (
    r"class=\"[^\"]*\b(?:pdp-link|product-name|product-tile__name|tile-name|product-title|product__name|tile__title"
    r"|product-tile-name|link product-link|name-link|product-tile__title)\b[^\"]*\"[^>]*>\s*(?:<[^>]+>\s*)*([^<]{3,140})<",
    r"<a\b[^>]*\b(?:title|aria-label)=\"([^\"]{3,140})\"",
    r"<img\b[^>]*\balt=\"([^\"]{3,140})\"",
)]
# not a product photo
BADGE = re.compile(r"^data:|badge|swatch|[/_.-](?:icons?|logo|sprite)[/_.-]|placeholder|spacer"
                   r"|\.svg|\.gif", re.I)
SKIP_HREF = re.compile(r"wishlist|quickview|quick-view|login|cart|javascript:|mailto:|/account", re.I)


def from_tiles(page, base, store):
    """Products in a grid of tiles, each marked with its product id (data-pid and its cousins)."""
    marks = [(m.start(), m.group(1)) for m in PID.finditer(page)]
    tiles, seen = [], set()
    for n, (at, pid) in enumerate(marks):
        if pid in seen:
            continue
        seen.add(pid)
        end = next((a for a, p in marks[n + 1:] if p != pid), min(len(page), at + 20000))
        tiles.append((pid, page[max(0, at - 300):min(end, at + 20000)]))
    out, table = [], None
    for pid, chunk in tiles:
        url = next((h for h in HREF.findall(chunk) if not SKIP_HREF.search(h)), None)
        if not url:
            continue
        said = None
        if store.get("tile_ids"):
            # a grid that prints no price in its tiles but lists what it shows in the page's own data, by id
            table = json_by_id(page) if table is None else table
            said = by_id(table, pid)
        title = ""
        for rx in NAMES:
            title = next((clean_title(m) for m in rx.findall(chunk) if clean_title(m)), "")
            if title:
                break
        title = title or next((t for t in (bare(text_of(m.group(2))) for m in NAMED.finditer(chunk))
                               if t and len(t) < 160), "")
        attrs = {**tile_attrs(chunk), **(said or {}), **tile_json(store, chunk)}
        from_attrs = {"price": attrs["price"], "was": attrs.get("was")} if attrs.get("price") else None
        printed = price_text(chunk, store)
        prices = ((from_attrs if store.get("tile_json") or store.get("tile_ids") else None)
                  or printed or from_attrs)
        if not prices:
            continue
        for was in (attrs.get("was"), (printed or {}).get("was")):   # an original price from wherever it is to be had
            if prices["was"] is None and was and was > prices["price"]:
                prices["was"] = was
        title = (attrs.get("title") or title) if store.get("tile_json") or store.get("tile_ids") else \
                title or attrs.get("title", "")
        img = first_image(chunk)
        sizes = html_sizes(store, chunk) or attrs.get("sizes") or []
        sized = attrs["sized"] if "sized" in attrs else bool(SIZE_ATTR.search(chunk))
        out.append({"title": title or pid, "url": urljoin(base, htmllib.unescape(url)),
                    "img": urljoin(base, htmllib.unescape(img)) if img else "", **prices,
                    "cur": attrs.get("cur", ""),
                    # a grid that lists every size it has left says, by listing none, that the garment is gone
                    "avail": bool(sizes) if store.get("size_stock") and sized else
                             attrs.get("avail", sold(store, chunk)),
                    "sizes": sizes, "brand": class_text(store, "brand_class", chunk),
                    "ptype": attrs.get("ptype", "")})
    return out


def tile_json(store, chunk):
    """A tile that carries its product's own data as JSON in an attribute: "tile_json" in stores.json names the
    attribute ("product-info" for data-product-info="{...}", Lilly Pulitzer's grid). The name, the two prices and
    the sizes are read from it; the address and the photo still come from the tile itself. The data of a tile with
    several colours holds one entry per colour, and the cheapest is taken, as elsewhere."""
    if not store.get("tile_json"):
        return {}
    m = re.search(r"""\bdata-%s=(?:"([^"]*)"|'([^']*)')""" % re.escape(store["tile_json"]), chunk, re.I)
    if not m:
        return {}
    try:
        data = json.loads(htmllib.unescape(m.group(1) or m.group(2)))
    except ValueError:
        return {}
    found = []

    def walk(node, depth=0):
        if depth > 8:
            return
        if isinstance(node, list):
            for v in node:
                walk(v, depth + 1)
            return
        if not isinstance(node, dict):
            return
        before = len(found)
        for v in node.values():
            walk(v, depth + 1)
        if len(found) == before:
            it = json_item({**node, "url": "/"}, "http://tile/", store)
            if it:
                found.append((it, node))

    walk(data)
    if not found:
        return {}
    best, node = min(found, key=lambda pair: pair[0]["price"])
    got = {k: best[k] for k in ("title", "price", "was", "sizes", "avail") if best[k] not in (None, "", [])}
    # whether this product comes in sizes at all: a bag with none is not a garment that has sold out of them
    got["sized"] = size_list(node)
    return got


def tile_attrs(chunk):
    """What a tile hangs on its own attributes rather than printing: data-product-price, -listprice,
    -availability, -name (Tory Burch's grid and others built the same way). Empty for a tile with none.
    Only used to fill in what the tile does not print."""
    got = {}
    for m in re.finditer(r"""(?i)\bdata-(?:product-)?(listprice|list-price|standard-?price|regular-?price"""
                         r"""|sale-?price|price|availability|name)=["']([^"']{0,140})["']""", chunk):
        got.setdefault(m.group(1).lower().replace("-", ""), m.group(2))
    out = {}
    price = num(got.get("price") or got.get("saleprice"))
    was = num(got.get("listprice") or got.get("standardprice") or got.get("regularprice"))
    if price:
        out["price"] = price
        out["was"] = was if was and was > price else None
    if got.get("availability"):
        out["avail"] = not re.search(r"(?i)out|sold|unavailable|backorder|preorder", got["availability"])
    if got.get("name"):
        out["title"] = clean_title(got["name"])
    return out


def price_text(chunk, store):
    """{"price", "was"} from the prices printed in a piece of a page, or None if it prints none. The struck-through
    one is the original price; the lowest of the others is what is asked now. "was_class" in stores.json names a
    shop's own class for the original price when it is not one of the usual words."""
    struck = [x for m in STRUCK.finditer(chunk) for x in money(text_of(m.group(2) or m.group(3) or ""))[:1]]
    if store.get("was_class"):
        rx = r"class=\"[^\"]*\b(?:%s)\b[^\"]*\"[^>]*>(.{0,300})" % store["was_class"]
        struck += [x for m in re.finditer(rx, chunk, re.I | re.S) for x in money(text_of(m.group(1)))[:1]]
    found = money(text_of(re.sub(r"<(script|style)\b.*?</\1>", " ", chunk, flags=re.S | re.I)))
    if not found:
        return None
    now = [x for x in found if x not in struck] or found
    price = min(now)
    was = max(struck) if struck and max(struck) > price else None
    return {"price": price, "was": was}


def sold(store, chunk):
    """False when a tile carries the store's sold-out mark ("sold" in stores.json, a pattern of its markup), else
    None (not known). There is no default: many grids print a hidden "sold out" label in every tile."""
    return False if store.get("sold") and re.search(store["sold"], chunk, re.I) else None


# ---------------------------------------------------------------- links to product pages

NAMED = re.compile(r"<(\w+)\b[^>]*class=[\"'][^\"']*(?:name|title)[^\"']*[\"'][^>]*>(.*?)</\1>", re.I | re.S)
NOT_NAME = re.compile(r"(?i)^(buy|details?|more|view|shop now|sold ?out|new|sale|add to (cart|bag))$")


def bare(t):
    """A name without the price and "sold out" a tile prints after it."""
    return clean_title(re.sub(r"\s*(?:(?:US\$|\$|¥|￥)\s?[\d,.]+|[\d,.]+\s?円).*$|\s*sold ?out\s*$", "", t, flags=re.I))


def by_class(name):
    """An element with this class (the whole class name, not part of another one), its text in group 2."""
    return re.compile(r"<(\w+)\b[^>]*class=[\"'](?:[^\"']*\s)?%s(?:\s[^\"']*)?[\"'][^>]*>(.*?)</\1>" % re.escape(name),
                      re.I | re.S)


def class_text(store, key, chunk):
    """The text of the element named by store[key] ("brand_class" and the like), or ""."""
    m = by_class(store[key]).search(chunk) if store.get(key) else None
    return clean_title(text_of(m.group(2))) if m else ""


def link_title(store, parts, chunk):
    """A product's name in a plain grid: the text of an element whose class says name or title, else the text of
    its links, else an image's alt text. "name_class" in stores.json picks the element when several qualify."""
    rx = by_class(store["name_class"]) if store.get("name_class") else NAMED
    strip = bare
    for m in rx.finditer(chunk):
        t = strip(text_of(m.group(2)))
        if t and not NOT_NAME.match(t) and len(t) < 160:
            return t
    alt = next((t for t in (clean_title(a) for a in re.findall(r"\balt=[\"']([^\"']{3,200})[\"']", chunk)) if t), "")
    if store.get("name_alt") and alt:       # shops whose link wraps the whole tile, text and all
        return alt
    names = [t for t in (strip(text_of(p.group(4))) for p in parts) if t and not NOT_NAME.match(t)]
    return max(names, key=len) if names else alt


def first_image(chunk):
    """The first product photo in a piece of a page: lazy-loading attributes first, then src, then a CSS
    background-image."""
    for tag in re.findall(r"<(?:img|source)\b[^>]*>", chunk, re.I):
        for attr in ("data-src", "data-original", "data-lazy-src", "data-srcset", "srcset", "src"):
            m = re.search(r"\b%s=[\"']([^\"' ]+)" % attr, tag, re.I)
            if m and not BADGE.search(m.group(1)):
                # the first address of a srcset, without the comma that separates it from the next
                return htmllib.unescape(m.group(1)).rstrip(",")
    m = re.search(r"background-image:\s*url\(['\"]?([^'\")]+)", chunk, re.I)
    return htmllib.unescape(m.group(1)) if m and not BADGE.search(m.group(1)) else ""


def from_links(page, base, store):
    """Products as links to product pages ("link": a pattern of their address) and the prices printed between
    one product's links and the next product's. For small shops' plain HTML grids."""
    rx = re.compile(store["link"])
    body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", lambda m: " " * len(m.group(0)), page,
                  flags=re.S | re.I)
    groups = []
    for m in re.finditer(r"<a\b[^>]*?\bhref=(?:\"([^\"]+)\"|'([^']+)'|([^\s>\"']+))[^>]*>(.*?)</a>", body, re.S | re.I):
        url = urljoin(base, htmllib.unescape(m.group(1) or m.group(2) or m.group(3)))
        if not rx.search(url):
            continue
        if groups and groups[-1]["url"] == url:
            groups[-1]["parts"].append(m)
        else:
            groups.append({"url": url, "parts": [m]})
    out, seen, table = [], set(), None
    for n, g in enumerate(groups):
        start = g["parts"][0].start()
        # up to the next product's first link (tiles can run to 40 KB of markup), or 20 KB after the last one
        end = groups[n + 1]["parts"][0].start() if n + 1 < len(groups) else g["parts"][-1].end() + 20000
        chunk = body[start:min(end, start + 80000)]
        said = None
        if store.get("link_ids"):
            # a grid that keeps its addresses in its links and its prices in the page's own data, matched on the
            # part of the address that spells the product's name or its id (Kendra Scott)
            table = json_by_id(page) if table is None else table
            said = by_address(table, g["url"])
        prices = price_text(chunk, store) or ({"price": said["price"], "was": said["was"]} if said else None)
        if not prices or g["url"] in seen:
            continue
        seen.add(g["url"])
        # where the page's own data names the product, that name beats whatever the tile prints around the link
        title = (said or {}).get("title", "") or link_title(store, g["parts"], chunk)
        img = first_image(chunk)
        sizes = html_sizes(store, chunk)
        out.append({"title": title, "url": g["url"], "img": urljoin(base, img) if img else "", **prices,
                    "cur": (said or {}).get("cur", ""),
                    "avail": bool(sizes) if store.get("size_stock") and SIZE_ATTR.search(chunk)
                             else sold(store, chunk),
                    "sizes": sizes, "brand": class_text(store, "brand_class", chunk),
                    "ptype": (said or {}).get("ptype", "")})
    return out


VIA = {"ld": from_ld, "json": from_json, "tiles": from_tiles, "links": from_links}


def products(page, base, store):
    """The products on one listing page, read the store's way ("via"). A "was" price missing there is taken from
    the tiles when they show the same product at the same price (JSON-LD offers carry no original price).
    "skip" in stores.json is a pattern of titles or addresses to leave out (Leffot's preorders are priced at
    their deposit, which would read as a markdown)."""
    found = VIA[store.get("via", "ld")](page, base, store)
    if store.get("skip"):
        found = [it for it in found if not re.search(store["skip"], it["title"] + " " + it["url"], re.I)]
    if store.get("trim"):
        # a badge the grid prints inside the name ("New Season Valentino Garavani ...")
        for it in found:
            it["title"] = clean_title(re.sub(store["trim"], "", it["title"], flags=re.I))
    if store.get("via", "ld") in ("ld", "json") and any(it["was"] is None or not it["sizes"] for it in found):
        # what the tiles of the same page show about a product the data left out
        tiles = {canon(t["url"]): t for t in from_tiles(page, base, store)}
        for it in found:
            t = tiles.get(canon(it["url"]))
            if not t or abs(t["price"] - it["price"]) > 0.01:
                continue
            if it["was"] is None and t["was"]:
                it["was"] = t["was"]
            it["sizes"] = it["sizes"] or t["sizes"]
    cur = ""
    for it in found:
        if not it["cur"]:
            cur = cur or page_currency(page)
            it["cur"] = cur
    return found


# ---------------------------------------------------------------- readers

def page_address(url, paging, n, size):
    """The n-th page (1, 2, ...) of a listing."""
    if n == 1 and "{start}" not in paging:
        return url
    extra = paging.format(n=n, start=(n - 1) * size)
    if extra.startswith("?"):
        u = urlparse(url)
        query = parse_qsl(u.query, keep_blank_values=True) + parse_qsl(extra[1:], keep_blank_values=True)
        keep = {}
        for k, v in query:                                    # the paging's own values replace the address's
            keep[k] = v
        return u._replace(query=urlencode(keep, safe="%[]{}:,/")).geturl()
    return url.rstrip("/") + extra if extra.startswith("_") else urljoin(url, extra)


def read_listing(host, store, fetch, max_pages, run):
    """Yield the products of every listing page in "pages", page after page."""
    errors = run["errors"]
    origin = f"https://{store.get('feed', host)}"
    paging, size = store.get("paging", ""), store.get("size", 0)
    seen = set()
    for entry in store["pages"]:
        # an entry is an address, or [address, what the page lists] ("mens jeans"), which stands in for the product
        # type of products the listing gives none, so they are sorted (and told men's or women's) by their page
        start_url, kind = (entry, "") if isinstance(entry, str) else entry
        url = urljoin(origin + "/", start_url)
        for n in range(1, max_pages + 1):
            if not allowed(fetch, url):
                errors.append(f"robots.txt disallows {url[len(origin):][:60]}")
                break
            status, page = fetch(url)
            if status != 200:
                # the end of a listing can answer 404; a refusal or a failed first page is an error
                if n == 1 or status in (0, 403, 429) or status >= 500:
                    errors.append(f"{url[len(origin):][:60]}: HTTP {status}")
                if status in (403, 429):
                    return                         # refused: ask this store nothing more this run
                break
            fresh = 0
            for it in products(page, url, store):
                if canon(it["url"]) not in seen:
                    seen.add(canon(it["url"]))
                    fresh += 1
                    it["ptype"] = it["ptype"] or kind
                    yield it
            if not fresh or not paging:
                break
            if paging == "next":
                m = re.search(r"<link\b[^>]*rel=[\"']next[\"'][^>]*href=[\"']([^\"']+)", page, re.I) or \
                    re.search(r"<link\b[^>]*href=[\"']([^\"']+)[\"'][^>]*rel=[\"']next[\"']", page, re.I) or \
                    re.search(r"<a\b[^>]*rel=[\"']next[\"'][^>]*href=[\"']([^\"']+)", page, re.I)
                if not m:
                    break
                url = urljoin(url, htmllib.unescape(m.group(1)))
            else:
                url = page_address(urljoin(origin + "/", start_url), paging, n + 1, size)
            if n == max_pages:
                run["cut"] = True                  # the page cap stopped this listing: the read is not the whole range


def read_woocommerce(host, store, fetch, max_pages, run):
    """The WooCommerce Store API: 100 products a page, prices in minor units (cents)."""
    errors = run["errors"]
    origin = f"https://{store.get('feed', host)}"
    for n in range(1, max_pages + 1):
        url = f"{origin}/wp-json/wc/store/v1/products?per_page=100&page={n}"
        if not allowed(fetch, url):
            errors.append("robots.txt disallows the Store API")
            return
        status, body = fetch(url)
        try:
            batch = json.loads(body) if status == 200 else None
        except ValueError:
            batch = None
        if not isinstance(batch, list):
            if n == 1 or status != 400:                    # past the last page the API answers 400
                errors.append(f"Store API page {n}: HTTP {status}")
            return
        for p in batch:
            pr = p.get("prices") or {}
            div = 10 ** int(pr.get("currency_minor_unit") or 0)
            price, regular = num(pr.get("price")), num(pr.get("regular_price"))
            if not price:
                continue
            yield {"title": clean_title(p.get("name")) or str(p.get("id")), "url": p.get("permalink", ""),
                   "img": ((p.get("images") or [{}])[0]).get("src", ""), "price": price / div,
                   "was": regular / div if regular and regular > price else None, "cur": pr.get("currency_code", ""),
                   "avail": bool(p.get("is_in_stock")), "brand": "",
                   "ptype": ", ".join(c.get("name", "") for c in p.get("categories") or [])}
        if len(batch) < 100:
            return
    run["cut"] = True


def read_netsuite(host, store, fetch, max_pages, run):
    """A NetSuite SuiteCommerce shop's items API (/api/items), the list its own pages are built from."""
    errors = run["errors"]
    origin = f"https://{store.get('feed', host)}"
    limit, offset = 100, 0
    for _ in range(max_pages):
        url = f"{origin}/api/items?fieldset=search&limit={limit}&offset={offset}"
        if not allowed(fetch, url):
            errors.append("robots.txt disallows the items API")
            return
        status, body = fetch(url)
        try:
            data = json.loads(body) if status == 200 else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            errors.append(f"items API at {offset}: HTTP {status}")
            return
        for p in data.get("items") or []:
            price = num(p.get("onlinecustomerprice"))
            detail = p.get("onlinecustomerprice_detail") or {}
            was = num(detail.get("compareprice")) if isinstance(detail, dict) else None
            if not price:
                continue
            img = first_url(p.get("itemimages_detail"))
            yield {"title": clean_title(p.get("storedisplayname2") or p.get("displayname")) or p.get("itemid", ""),
                   "url": f"{origin}/{p.get('urlcomponent') or p.get('itemid')}", "img": img, "price": price,
                   "was": was if was and was > price else None, "cur": "", "avail": bool(p.get("isinstock")),
                   "brand": "", "ptype": str(p.get("custitemitem_category") or "")}
        offset += limit
        if offset >= int(data.get("total") or 0):
            return
    run["cut"] = True


def dotted(value, path):
    """The value at a dotted path in a hit: "model.categories.micro"."""
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def facet_value(v):
    """One readable value out of what a search index holds: Algolia files a field as a list of the levels of its
    hierarchy, each with the shop's own code on the end ("PALOMA BARCELO-830 > PALOMITAS by ...-21402"). The top
    level without its code is wanted, and a name the index shouts is given back in words."""
    if isinstance(v, list):
        v = v[0] if v else ""
    if not isinstance(v, str):
        return "" if v is None else str(v)
    v = re.sub(r"\s*-\s*[0-9]+$", "", v.split(" > ")[0]).strip()
    return v.title() if v and v == v.upper() and re.search(r"[A-Z]{3}", v) else v


def algolia_item(hit, store):
    """One product out of a search index, described by "fields" in stores.json: a dotted path per field of the
    site's own item ("price": "modelBestOfferByPrice.currentPrice"). "title" is a template over the same paths,
    "product_url" and "image_url" templates over the product's id."""
    fields = store.get("fields") or {}
    got = {k: dotted(hit, p) for k, p in fields.items()}
    price, was = num(got.get("price")), num(got.get("was"))
    pid = str(got.get("id") or hit.get("objectID") or "")
    if not price or not pid:
        return None
    title = re.sub(r"\s+", " ", re.sub(r"\{([\w.]+)\}", lambda m: facet_value(dotted(hit, m.group(1))) or "",
                                       store.get("title", "") or "")).strip()
    sizes = [s for s in (size_label(s) for s in (got.get("sizes") or [])) if s] if \
        isinstance(got.get("sizes"), list) else []
    return {"title": title or facet_value(got.get("ptype")) or pid,
            "url": store["product_url"].format(id=pid),
            "img": store.get("image_url", "").format(id=pid, head=pid[:2].lower()),
            "price": price, "was": was if was and was > price else None,
            "cur": str(got.get("cur") or ""),
            "avail": got.get("avail") if isinstance(got.get("avail"), bool) else None,
            "sizes": sizes, "brand": facet_value(got.get("brand")),
            "ptype": facet_value(got.get("ptype"))}


def read_harvest(host, store, fetch, max_pages, run):
    """A store that no plain request reaches at all: its pages are opened in a browser by tools/harvest.py on a
    schedule of its own, and what that found is left in data/harvest/<host>.json for the build to read from disk.
    Nothing is fetched here. A harvest older than "stale_days" (a week by default) is still used -- a stale price
    is better than an empty store -- but it is reported, so a run says when a browser last got through."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "harvest", host + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError) as e:
        run["errors"].append("no harvest to read: %s" % str(e)[:60])
        return
    when, items = saved.get("fetched", ""), saved.get("items") or []
    days = (time.time() - saved.get("at", 0)) / 86400 if saved.get("at") else 999
    if days > store.get("stale_days", 7):
        run["errors"].append("the harvest is %s, from %s" % (
            "%.0f days old" % days if days < 999 else "undated", when[:16] or "?"))
    for d in items:
        if isinstance(d, dict) and d.get("price") and d.get("url"):
            yield {"title": "", "img": "", "was": None, "cur": "", "avail": None, "sizes": [], "brand": "",
                   "ptype": "", **d}


def read_algolia(host, store, fetch, max_pages, run):
    """A shop whose listing pages are built in the visitor's browser by an Algolia search, from the search key
    the page hands out. The index answers a plain GET, 1000 products at a time, so a whole sale department comes
    back in a handful of requests. "algolia" in stores.json holds the application, the key and the index, and
    "queries" the filters to ask for ([filter, what it lists])."""
    errors, cfg = run["errors"], store["algolia"]
    origin = "https://%s-dsn.algolia.net" % cfg["app"].lower()
    size = store.get("size", 1000)
    seen = set()
    for entry in store["queries"]:
        filters, kind = (entry, "") if isinstance(entry, str) else entry
        for n in range(max_pages):
            url = "%s/1/indexes/%s?%s" % (origin, quote(cfg["index"]), urlencode(
                {"x-algolia-api-key": cfg["key"], "x-algolia-application-id": cfg["app"],
                 "filters": filters, "hitsPerPage": size, "page": n}))
            if not allowed(fetch, url):
                errors.append("robots.txt disallows the search index")
                return
            status, body = fetch(url)
            try:
                data = json.loads(body) if status == 200 else None
            except ValueError:
                data = None
            if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
                errors.append(f"search for {filters[:40]}: HTTP {status}")
                break
            fresh = 0
            for h in data["hits"]:
                it = algolia_item(h, store)
                if it and it["url"] not in seen:
                    seen.add(it["url"])
                    fresh += 1
                    it["ptype"] = (kind + " " + it["ptype"]).strip()
                    yield it
            if not fresh or n + 1 >= int(data.get("nbPages") or 0):
                break
            if n + 1 == max_pages:
                run["cut"] = True


def first_url(images):
    """The first image address in NetSuite's images, which are filed by colour: {"KHAKI": {"urls": [{"url": ...}]}}."""
    if isinstance(images, dict):
        if isinstance(images.get("url"), str):
            return quote(images["url"], safe=":/?=&%")
        images = list(images.values())
    for v in images if isinstance(images, list) else []:
        found = first_url(v)
        if found:
            return found
    return ""


READERS = {"listing": read_listing, "woocommerce": read_woocommerce, "netsuite": read_netsuite,
           "algolia": read_algolia, "harvest": read_harvest}


def read(host, store, fetch, max_pages, run):
    """Yield every product of a store read here ("read" in stores.json). run["errors"] collects what went wrong;
    run["cut"] is set when the page cap stopped the read short of the store's whole range."""
    yield from READERS[store["read"]](host, store, fetch, max_pages, run)
