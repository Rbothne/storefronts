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

Every address is checked against the robots.txt of its host first (wildcards included), and skipped when it is
disallowed. Several stores allow their category pages but not the faster forms of them their own scripts use
(Search-UpdateGrid, ?format=json, ?sz=300); this keeps the build on the allowed side.

What comes back for each product is a plain dict (title, url, price, was, cur, avail, img, brand, ptype),
which build.py turns into a site item the way it does a Shopify product.
"""
import html as htmllib
import json
import re
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
    return {"title": name, "url": urljoin(base, url), "img": urljoin(base, image) if isinstance(image, str) else "",
            "price": price, "was": None, "cur": str(offer.get("priceCurrency") or ""), "avail": avail,
            "brand": brand or "", "ptype": str(node.get("category") or "")}


# ---------------------------------------------------------------- the page's own product data

def embedded(page):
    """Every sizeable JSON value a page embeds for its scripts: <script type="application/json"> (Next.js's
    __NEXT_DATA__ among them) and assignments like window.__PRELOADED_STATE__ = {...}."""
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


NAME_KEYS = ("name", "title", "productName", "displayName", "product_name", "styleName", "displayname",
             "storedisplayname2")
URL_KEYS = ("url", "productPageUrl", "urlPath", "path", "permalink", "productUrl", "pdpUrl", "href", "link", "slug",
            "handle", "fullUrl", "absUrl")
# a price field's name says which price it is: compare-at, original, list ... is the "was" price
WAS_KEY = re.compile(r"compare|original|list|regular|base|old|retail|standard|strike|was|full|msrp", re.I)
NOW_KEY = re.compile(r"sale|discount|final|current|offer|effective|special|now|promo|selling|customer", re.I)
NOT_PRICE = re.compile(r"member|loyalty|per_?unit|installment|klarna|afterpay|shipping|tax|percent|saving|max", re.I)
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


def json_item(d, base, store):
    if isinstance(d.get("product"), dict) and not any(isinstance(d.get(k), str) for k in NAME_KEYS):
        # a search hit: its prices beside the product they are for (Rolla's)
        d = {**d["product"], **{k: v for k, v in d.items() if k != "product"}}
    name =next((clean_title(d[k]) for k in NAME_KEYS if isinstance(d.get(k), str) and clean_title(d[k])), None)
    url = next((d[k] for k in URL_KEYS if isinstance(d.get(k), str) and d[k].strip()), None)
    if not url and store.get("product_url") and isinstance(d.get("productId"), str):
        # Salesforce's composable storefronts keep a product's id, not its address: "/us/en/product/{id}"
        url = store["product_url"].format(id=d["productId"])
    if not name or not url:
        return None
    fields = price_fields(d)
    if not fields:
        return None
    is_was = lambda p: WAS_KEY.search(p) and not NOW_KEY.search(p.rsplit(".", 1)[-1])
    was, now = [n for p, n in fields if is_was(p)], [n for p, n in fields if not is_was(p)]
    if not now:
        now, was = was, []
    price = min(now)
    was = max(was) if was and max(was) > price else None
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
    brand = d.get("brand") or d.get("vendor") or ""
    brand = brand.get("name", "") if isinstance(brand, dict) else brand if isinstance(brand, str) else ""
    img = ""
    for k in ("image", "featuredImage", "defaultImage", "img", "imageUrl", "thumbnail", "featuredMedia"):
        v = d.get(k)
        if isinstance(v, dict):
            v = v.get("url") or v.get("src") or v.get("urlOriginal") or v.get("disBaseLink") or v.get("link")
        if isinstance(v, str) and v:
            img = v
            break
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
            "was": was, "cur": cur, "avail": avail, "brand": brand, "ptype": ptype if isinstance(ptype, str) else ""}


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
BADGE = re.compile(r"^data:|badge|[/_.-](?:icons?|logo|sprite)[/_.-]|placeholder|spacer|\.svg|\.gif", re.I)
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
    out = []
    for pid, chunk in tiles:
        url = next((h for h in HREF.findall(chunk) if not SKIP_HREF.search(h)), None)
        if not url:
            continue
        title = ""
        for rx in NAMES:
            title = next((clean_title(m) for m in rx.findall(chunk) if clean_title(m)), "")
            if title:
                break
        title = title or next((t for t in (bare(text_of(m.group(2))) for m in NAMED.finditer(chunk))
                               if t and len(t) < 160), "")
        prices = price_text(chunk, store)
        if not prices:
            continue
        img = first_image(chunk)
        out.append({"title": title or pid, "url": urljoin(base, htmllib.unescape(url)),
                    "img": urljoin(base, htmllib.unescape(img)) if img else "", **prices, "cur": "",
                    "avail": sold(store, chunk), "brand": "", "ptype": ""})
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
                return htmllib.unescape(m.group(1))
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
    out, seen = [], set()
    for n, g in enumerate(groups):
        start = g["parts"][0].start()
        # up to the next product's first link (tiles can run to 40 KB of markup), or 20 KB after the last one
        end = groups[n + 1]["parts"][0].start() if n + 1 < len(groups) else g["parts"][-1].end() + 20000
        chunk = body[start:min(end, start + 80000)]
        prices = price_text(chunk, store)
        if not prices or g["url"] in seen:
            continue
        seen.add(g["url"])
        title = link_title(store, g["parts"], chunk)
        img = first_image(chunk)
        out.append({"title": title, "url": g["url"], "img": urljoin(base, img) if img else "", **prices, "cur": "",
                    "avail": sold(store, chunk), "brand": class_text(store, "brand_class", chunk), "ptype": ""})
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
    if store.get("via", "ld") in ("ld", "json") and any(it["was"] is None for it in found):
        struck = {canon(t["url"]): t for t in from_tiles(page, base, store) if t["was"]}
        for it in found:
            t = struck.get(canon(it["url"]))
            if it["was"] is None and t and abs(t["price"] - it["price"]) < 0.01:
                it["was"] = t["was"]
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


READERS = {"listing": read_listing, "woocommerce": read_woocommerce, "netsuite": read_netsuite}


def read(host, store, fetch, max_pages, run):
    """Yield every product of a store read here ("read" in stores.json). run["errors"] collects what went wrong;
    run["cut"] is set when the page cap stopped the read short of the store's whole range."""
    yield from READERS[store["read"]](host, store, fetch, max_pages, run)
