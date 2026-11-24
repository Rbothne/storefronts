#!/usr/bin/env python3
"""Build docs/data.json for the wishlist / sale page.

Reads   stores.json   (which stores, how to read them)
        wishlist.txt  (one URL per line: product pages to follow)
Writes  docs/data.json     (what the page shows)
        data/state/        (one file per store: price, discount and stock changes of every product; see history.py)
        docs/metrics.json  (indicators for the metrics page, worked out from data/state/)
        docs/brands/, docs/matches/  (each brand's whole range; every store's offer of an item sold at several)

Shopify stores are read here; stores that are not open Shopify feeds are read by listings.py.
Standard library only.  Run:  python3 build.py
"""
import concurrent.futures
import datetime
import gzip
import html as htmllib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import unquote, urlparse

import history
import imagesig
import listings
import matching

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
DELAY = 1.0
COME_BACK = 180       # the come-back pass over rate-limited stores starts at least this long after the run did
COME_BACK_FOR = 600   # ... and waits on refusals for this long at most; after that a refused store keeps its old items
PATIENT = False       # True during the come-back pass: wait and retry on 429 instead of moving on
PATIENT_UNTIL = 0     # when that patience runs out (a time.time() value)
COUNT = {"requests": 0, "refused": 0}   # this run's requests, and how many were answered 429 (too many requests)
LANE = threading.local()  # per store being read: .delay, a longer pause it asks for (Crawl-delay); .gzip (see get)


# ---------------------------------------------------------------- fetching

def get(url, timeout=25, tries=3):
    """Return (status, text). Never raises.

    A server error is retried after a wait. A 429 (too many requests) is not, on the first pass: the store is
    left for later and the run moves on to other stores (see main()). On the come-back pass (PATIENT) a 429 is
    retried after the server's Retry-After when it gives one (capped at a minute), otherwise 15 s and then 45 s.
    That patience has a time limit (COME_BACK_FOR): a store refusing page after page would otherwise hold the
    run for an hour.
    """
    for attempt in range(tries):
        COUNT["requests"] += 1
        headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                   "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"}
        if getattr(LANE, "gzip", False):
            # Listing pages run to 1-4 MB of HTML; compressed they are a fifth of that. Asked for only there:
            # some servers stall on it (Visvim's NetSuite API never answers a request that offers gzip).
            headers["Accept-Encoding"] = "gzip"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # generous cap: one 250-product feed page can run to 16 MB (All Volleyball)
                raw = r.read(40_000_000)
                if r.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                body = raw.decode(charset(r.headers.get("Content-Type", ""), raw), "replace")
                time.sleep(getattr(LANE, "delay", None) or DELAY)
                return r.status, body
        except urllib.error.HTTPError as e:
            status = e.code
            COUNT["refused"] += status == 429
            patient = PATIENT and time.time() < PATIENT_UNTIL
            if status in (429, 500, 502, 503, 504) and attempt + 1 < tries and (status != 429 or patient):
                wait = (15, 45)[min(attempt, 1)]
                try:
                    wait = min(60, max(wait, int(e.headers.get("Retry-After", ""))))
                except ValueError:
                    pass
                time.sleep(wait)
                continue
            time.sleep(getattr(LANE, "delay", None) or DELAY)
            return status, ""
        except Exception as e:  # DNS, TLS, timeout
            if attempt + 1 < tries:
                time.sleep(5)
                continue
            return 0, str(e)
    return 0, ""


def charset(content_type, raw):
    """The text encoding of a response: its Content-Type's, else the page's own <meta charset>, else UTF-8.
    Japanese shops still serve Shift_JIS and EUC-JP."""
    m = re.search(r"charset=[\"']?([\w-]+)", content_type, re.I) or \
        re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4000], re.I)
    name = m.group(1) if m else "utf-8"
    name = name.decode("ascii", "replace") if isinstance(name, bytes) else name
    try:
        "".encode(name)
        return {"shift_jis": "cp932", "sjis": "cp932", "x-sjis": "cp932", "euc-jp": "euc_jis_2004"}.get(name.lower(), name)
    except LookupError:
        return "utf-8"


class Gone(Exception):
    """The store answered 404: the listing has been taken down."""


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------- shopify

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# Things a shop sells that are not products: delivery insurance, gift cards, a donation, a sample of swatches.
NOT_A_PRODUCT = re.compile(r"\b(shipping|package|order|delivery) (protection|insurance)\b|\broute (package )?protection\b"
                           r"|\be-?gift ?cards?\b|\bgift ?cards?\b|\bgift (voucher|certificate)\b|\bdonation\b|\bswatch(es)? sample", re.I)


def shopify_item(host, p, cents=False, locale=""):
    """Normalise one product from products.json (dollars) or /products/x.js (cents). locale: the path the store
    serves its English pages under ("/en"), for the product's address."""
    ptype = (p.get("product_type") or p.get("type") or "").lower().replace("_", " ")
    if NOT_A_PRODUCT.search(p.get("title") or "") or ptype in ("gift card", "giftcard", "insurance"):
        return None
    # a page of a runway or lookbook, not a product: The Row's ":: Fall 2025 :: Look 12" ("Product Look", at a
    # placeholder $99,999), Isabel Marant's "Look 28 [S27]", Noah's "Lookbook - SS20"
    if ptype in ("look", "product look", "lookbook") or re.match(r"\W*look ?book\b", p.get("title") or "", re.I):
        return None
    div = 100.0 if cents else 1.0
    variants = p.get("variants") or []
    live = [v for v in variants if v.get("available")] or variants
    priced = [(num(v.get("price")), num(v.get("compare_at_price"))) for v in live]
    priced = [(a / div, (b / div if b else None)) for a, b in priced if a is not None]
    if not priced:
        return None
    price, was = min(priced, key=lambda t: t[0])
    off = round((was - price) / was * 100) if was and was > price else 0

    img = ""
    images = p.get("images") or []
    if images:
        first = images[0]
        img = first.get("src", "") if isinstance(first, dict) else str(first)
    if img.startswith("//"):
        img = "https:" + img

    sizes, sizes_all = size_labels(p)
    tags = p.get("tags") or []
    tags = tags.split(",") if isinstance(tags, str) else tags
    return {
        "g": gender(" ".join([p.get("title") or "", p.get("vendor") or "", *map(str, tags)]),
                    p.get("product_type") or p.get("type") or ""),
        "id": f"{host}/{p.get('handle')}",
        "store": host,
        "title": p.get("title", ""),
        "brand": p.get("vendor", ""),
        "url": f"https://{host}{locale}/products/{p.get('handle')}",
        "img": img,
        "price": round(price, 2),
        "was": round(was, 2) if off else None,
        "off": off,
        "avail": any(v.get("available") for v in variants),
        "sizes": sizes[:14],
        "sizes_all": sizes_all,  # used to work out the size scale, then dropped
        "tags": [],
        "raw_tags": tags,        # read for the store's own category (category_tags), then dropped
        # products.json calls it product_type, /products/x.js calls it type
        "ptype": p.get("product_type") or p.get("type") or "",
        "published": str(p.get("published_at") or "")[:10],   # used for tracking, then dropped
    }


def gender(text, ptype):
    """"w", "m" or "k" (kids) when the listing says so; None for unisex or unlabelled."""
    text, ptype = text.lower(), ptype.lower()
    both = text + " " + ptype
    if re.search(r"\b(kids|kid['’]s|youth|toddlers?|infants?)\b", both) or re.search(r"\b(boys|girls)\b", ptype):
        return "k"
    # "female" and "male" are how some stores (Everlane) tag every product, and the only place they say it
    w = re.search(r"\b(wom[ae]n['’]?s?|wmns|ladies|female|donna|skirts?|gonn[ae]|blouses?|dress(es)?(?! (shirt|pant|shoe|sock|trouser|boot)))\b", both)
    m = re.search(r"\b(men['’]?s?|male|uomo)\b", both) or re.search(r"\bman\b", ptype)
    return "w" if w and not m else "m" if m and not w else None


def size_labels(p):
    """Return (in-stock size labels, all size labels) for a Shopify product.

    Variant titles mix options ("Black / M"), so read the option that holds the
    size when the product names one; otherwise fall back to the full title.
    """
    names = [(o.get("name") if isinstance(o, dict) else o) or "" for o in p.get("options") or []]
    idx = next((i for i, n in enumerate(names) if re.search(r"size|サイズ|waist", str(n), re.I)), None)
    if idx is None and len(names) == 1:
        idx = 0
    key = f"option{idx + 1}" if idx is not None else "title"
    live, every = [], []
    for v in p.get("variants") or []:
        label = str(v.get(key) or "").strip()
        if not label or label == "Default Title":
            continue
        if label not in every:
            every.append(label)
        if v.get("available") and label not in live:
            live.append(label)
    return live, every


def shopify_feed(host, path, max_pages):
    """Yield raw products from a paginated Shopify feed. Raises on first-page failure."""
    for page in range(1, max_pages + 1):
        status, body = get(f"https://{host}{path}/products.json?limit=250&page={page}")
        try:
            products = json.loads(body).get("products", []) if status == 200 else None
        except Exception:
            products = None
        if products is None:
            # a refusal partway through would otherwise leave a silently partial catalogue: report it, so the
            # store is fetched again on the come-back pass
            if page == 1 or status == 429:
                raise RuntimeError(f"feed returned HTTP {status}" + (f" on page {page}" if page > 1 else ""))
            return
        if not products:
            return
        # Not "fewer than 250 means the last page": some feeds leave out a few products a page (The Outnet serves
        # 245-249), so only an empty page ends the feed. That costs one request per store read.
        yield from products


def served_currency(host):
    """The currency this store is quoting to us, or None if it will not say.

    Shopify stores localise prices by the visitor's country, so a shop based in
    Japan may send US dollar prices to a US connection. Its cart reports which.
    """
    status, body = get(f"https://{host}/cart.js", tries=1)
    try:
        return json.loads(body).get("currency") or None
    except Exception:
        return None


def shopify_single(host, handle, feed=None):
    """One product. feed is the host that serves the store's Shopify data when the shop's own address doesn't."""
    url = f"https://{host}/products/{handle}"
    status, body = get(f"https://{feed or host}/products/{handle}.js")
    if status == 404:
        raise Gone()
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError:
        # Custom storefronts on top of Shopify (Cav Empt) answer .js with the
        # ordinary HTML product page, so read that instead. A removed product
        # still answers 200 with only the site name as title; the URL slug
        # makes a better card title than that.
        item = page_item(host, url)
        return item if item["price"] is not None else link_item(host, url, "Price not readable on this page")
    return shopify_item(host, data, cents=True)


# ---------------------------------------------------------------- generic pages

def meta(page, key):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % re.escape(key), page, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(key), page, re.I)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def jsonld_product(page):
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                types = node.get("@type")
                types = types if isinstance(types, list) else [types]
                if "Product" in types or "ProductGroup" in types:
                    return node
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
    return None


SYMBOLS = {"¥": "JPY", "￥": "JPY", "£": "GBP", "€": "EUR", "$": ""}  # "$" = the store's own dollar


def page_item(host, url):
    """Read title / image / price from a product page.

    Pages with a title but no readable price come back as a link card.
    """
    status, page = get(url)
    if status == 404:
        raise Gone()
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    title, img, price, cur, avail = meta(page, "og:title"), meta(page, "og:image"), None, "", None
    node = jsonld_product(page)
    if node:
        title = str(node.get("name") or title)
        image = node.get("image")
        if isinstance(image, list) and image:
            image = image[0]
        if isinstance(image, dict):
            image = image.get("url")
        img = str(image or img)
        offers = node.get("offers") or {}
        offers = offers if isinstance(offers, list) else [offers]
        flat = []
        for o in offers:
            if isinstance(o, dict):
                flat.extend(o.get("offers") if isinstance(o.get("offers"), list) else [o])
        best = None
        for o in flat:
            p = num(o.get("price") if o.get("price") is not None else o.get("lowPrice"))
            if p is not None and (best is None or p < best[0]):
                best = (p, o)
        if best:
            price, cur = best[0], str(best[1].get("priceCurrency", ""))
        states = [str(o.get("availability", "")) for o in flat if o.get("availability")]
        if states:
            avail = any(s.rsplit("/", 1)[-1].lower() in
                        ("instock", "limitedavailability", "onlineonly", "preorder", "presale", "backorder")
                        for s in states)
    if price is None:
        price = num(meta(page, "product:price:amount") or meta(page, "og:price:amount"))
        cur = meta(page, "product:price:currency") or meta(page, "og:price:currency")
    if price is None:
        # Last resort: a visible price, e.g. <div class="price">$225</div>
        m = re.search(r'class=["\'][^"\']*\bprice\b[^"\']*["\'][^>]*>\s*([$¥￥£€])\s*([\d,]+(?:\.\d+)?)', page, re.I)
        if m:
            price, cur = num(m.group(2).replace(",", "")), SYMBOLS[m.group(1)]
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        title = htmllib.unescape(m.group(1)).strip() if m else ""
    if img.startswith("//"):
        img = "https:" + img
    if not title:
        raise RuntimeError("no title or price found in page")
    item = {"id": url, "store": host, "title": title, "brand": "", "url": url, "img": img,
            "price": price, "was": None, "off": 0, "avail": avail, "sizes": [],
            "cur": cur or None, "tags": []}
    if price is None:
        item.update(link_only=True, note="Price not readable on this page")
    return item


def link_item(host, url, note=""):
    slug = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    slug = re.sub(r"\.html?$|\.php$", "", slug)
    title = re.sub(r"[-_]+", " ", slug).strip().title() or host
    return {"id": url, "store": host, "title": title, "brand": "", "url": url, "img": "",
            "price": None, "was": None, "off": 0, "avail": None, "sizes": [],
            "tags": [], "link_only": True, "note": note}


def listing_item(host, store, d):
    """Normalise one product read by listings.py (a store's listing pages or shop API) to what shopify_item gives.
    Its id is its address without the query, which listings add and drop (?source=plp, colour choices)."""
    price, was, url = d.get("price"), d.get("was"), d.get("url") or ""
    if not price or not url.startswith("http"):
        return None
    off = round((was - price) / was * 100) if was and was > price else 0
    sizes = [s for s in d.get("sizes") or [] if s]
    return {
        "g": gender(" ".join([d["title"], d.get("brand") or "", urlparse(url).path.replace("-", " ")]),
                    d.get("ptype") or ""),
        "id": listings.canon(url),
        "store": host,
        "title": d["title"],
        "brand": d.get("brand") or "",
        "url": url,
        "img": d.get("img") or "",
        "price": round(price, 2),
        "was": round(was, 2) if off else None,
        "off": off,
        "avail": d.get("avail"),         # None when the listing does not say
        "sizes": sizes[:14],
        "sizes_all": sizes,
        "tags": [],
        "ptype": d.get("ptype") or "",
        "published": "",
        "cur": d.get("cur") or store.get("currency", "USD"),
    }


# ---------------------------------------------------------------- categories

# A store's "group" in stores.json is only a default: clothing stores also sell
# skate decks, planners and blankets. These rules sort each item by its product
# type, then by its title. First match wins, so order matters: "Knit Cap" is a
# hat, "Wallet Pants" are pants, "Deck Jacket" is a jacket.
RULES = [(re.compile(r"\b(%s)\b" % rx, re.I), cat) for rx, cat in [
    (r"hats?|caps?|beanies?|berets?|balaclavas?|headwear|socks?|gloves?|mitt(s|ens)?|scar(f|ves)|leg warmers"
     r"|cappell[oi]|sciarp[ae]|calz(e|ini)|guanti", "clothing/accessories"),
    # "flats" and "heels" only in the plural: a "Flat Front Pant" is not a shoe
    (r"shoes?|sneakers?|boots?|sandals?|loafers?|footwear|slippers?|mules?|clogs?|flats|heels|(low|mid|high)[- ]tops?"
     r"|scarp[ae]|stival(i|etti)|sandali|mocassini|d[eé]collet[eé]s?|pumps|ballerin[ae]|espadrilles?", "clothing/footwear"),
    (r"pants?|sweatpants?|trackpants?|\w*shorts|jeans?|trousers?|tights|leggings?|joggers?|skirts?|bottoms?|full-length"
     r"|pantalon[ei]|gonn[ae]|bermuda", "clothing/bottoms"),
    (r"jackets?|jkt|coats?|blazers?|bombers?|outerwear|outer|parkas?|blousons?|vests?|anoraks?|windbreakers?"
     r"|puffers?|down-insulated|shell|giacc(a|he)|giubb(ino|ini|otto|otti)|cappott[oi]|piumin[oi]|gilet|trench|ponchos?",
     "clothing/outerwear"),
    # before tops, so a "sweater dress" is a dress; "dress shirt", "dress pants" and the like are not
    (r"dress(es)?(?![- ](shirts?|pants?|shoes?|socks?|trousers?|boots?))|jumpsuits?|rompers?|playsuits?|unitards?|onesies?"
     r"|abit[oi]|vestit[oi]", "clothing/dresses"),
    (r"\w*shirts?|shirting|tees?|(?<!['’])t|polos?|hood(s|y|ie|ies)?|hooded|sweats?|sweaters?|knit(s|wear)?|cardigans?"
     r"|crew ?necks?|pullovers?|long ?sleeves?|button[- ]downs?|thermals?|tops?|tracktops?|jersey|tanks?|henley|rugby"
     r"|crops?|(sports )?bras?(?! bags?)|bralettes?|singlets?|(half|quarter|1/4|1/2)[- ]zips?"
     r"|fleece|magli(a|e|one|oni|eria)|camici[ae]|felp[ae]|girocollo|blouses?|bodysuits?", "clothing/tops"),
    (r"bags?|totes?|backpacks?|pouch(es)?|wallets?|belts?|ties?|sunglasses|eyewear|jewel(le)?ry|necklaces?"
     r"|bracelets?|rings?|watch(es)?|keychains?|bandanas?|leather goods|clutch(es)?|card ?holders?|earrings?"
     r"|bors[ae]|cintur[ae]|portafogli[o]?|occhiali|collan[ae]|braccial[ei]|anell[oi]|orecchini|gioielli", "clothing/accessories"),
    (r"skateboards?|decks?|wheels?|trucks?|bearings?|grip ?tape|bushings?|hardware|hard goods|tools?|completes?"
     r"|snowboards?", "skate"),
    (r"blankets?|throws?|towels?|pillows?|drinkware|tableware|candles?|apothecary|publications?|books?|magazines?"
     r"|planners?|calendars?|notebooks?|notepads?|paper|writing|pens?|pencils?|desktop|storage|stickers?|cards?"
     r"|toys?|figures?|home|lifestyle|personal care|incense|mugs?|cups?|fabric|soft goods|stationery", "home"),
    (r"accessor(y|ies)", "clothing/accessories"),
    (r"denim", "clothing/bottoms"),
]]


# Second pass, for an item none of the RULES placed: looser words, then the RULES again on loosened text, then
# the store's own category tags. Because it only ever sees items that would otherwise land in "Other", it cannot
# move anything the rules above already sort -- which is what lets it carry words too loose for the first pass
# ("short" there would take every "Short Sleeve Shirt" for shorts; here the shirt is long gone).
FALLBACK = [(re.compile(r"\b(%s)\b" % rx, re.I), cat) for rx, cat in [
    (r"short ?sleeves?d?|long ?sleeves?d?|ss tees?|halfzips?|quarterzips?|jumpers?|turtlenecks?|mock ?necks?|crews?"
     r"|corsets?|camis?|camisoles?|cardis?|kurt(a|i)s?|tunics?|button[- ]ups?|wovens|cut ?(&|and)? ?sew(n)?|pinnies"
     r"|base ?layers?", "clothing/tops"),
    (r"kaftans?|caftans?|abayas?|gowns?|overalls?|dungarees?", "clothing/dresses"),
    (r"\w+coats?|shackets?|cabans?|capes?|bodywarmers?|body ?warmers?|pellicc(ia|e)", "clothing/outerwear"),
    (r"trainers?|derb(y|ies)|brogues?|moccasins?|mocs?|slides?|chukkas?|mary ?janes?|slingbacks?|wingtips?"
     r"|slip[- ]ons?|air (jordan|max|force)|jordan \d+|dunks?|yeezy|samba|gazelle|spezial|chuck (taylor|70)"
     r"|old skool|gel-\w+", "clothing/footwear"),
    # before "short": a "Short Snake Chain" is a necklace
    (r"\w+bags?|wristlets?|pochettes?|crossbody|hobos?|visors?|headbands?|hair ?clips?|claw clips?|scrunchies?"
     r"|pendants?|charms?|anklets?|brooch(es)?|cufflinks?|chains?|cuffs?|pins?|pocket squares?|bow ?ties?|neckties?"
     r"|stoles?|shawls?|wraps?|umbrellas?|lanyards?|key ?rings?|card ?cases?|purses?|cravatt[ae]|portachiav[ei]"
     r"|foulards?|zain[oi]|earmuffs?|satchels?|duffe?ls?|key ?fobs?", "clothing/accessories"),
    # "short" and "tight" singular, now that nothing is left here that says "short sleeve" or "skin-tight top"
    (r"short|jorts|skorts?|tight|panties|thongs?|hipsters?|boy ?shorts?|briefs?|boxers?|trunks?|underwear|intimates"
     r"|chinos?|slacks|culottes?|capris?|cutoffs|kilts?|bootcut|minigonn[ae]|ciclist[ae]|jogging|long pants",
     "clothing/bottoms"),
    # what clothing shops sell besides clothes; "Home" already takes apothecary and personal care
    (r"eau de (parfum|toilette|cologne)|edp|edt|parfums?|perfumes?|fragrances?|colognes?|diffusers?|room spray"
     r"|air fresh\w*|skin ?care|serums?|moisturi[sz]ers?|lotions?|balms?|cleansers?|shampoos?|conditioners?|soaps?"
     r"|deodorants?|sunscreens?|spf|nail polish|lipsticks?|lip (balm|oil|gloss|tint)|mascara|make-?up|cosmetics"
     r"|beauty products?|(hand|face|body|eye) (cream|wash|oil|mask)|(hand|body|face|hair|skin|nail) ?care|\d+ ?ml|homewares?|ornaments?|tumblers?|water bottles?"
     r"|plates?|bowls?|teapots?|vases?|lamps?|clocks?|chairs?|rugs?|vinyl|lp|piatt[oi]|vassoi[o]?|posate|candel[ae]"
     r"|tazz[ae]|libr[oi]|tovagli\w*|bicchier[ei]|profum[oi]|fragranz[ae]|insalatiera|fruttiera|svuotatasche",
     "home"),
]]
# A store whose titles are in French ("lang": "fr"). Only for those stores: "robe" is a dress in French and a
# dressing gown in English, "pull" a sweater in one and a verb in the other.
FRENCH = [(re.compile(r"\b(%s)\b" % rx, re.I), cat) for rx, cat in [
    (r"casquettes?|bonnets?|chapeaux?|chaussettes?|gants|[ée]charpes?|foulards?|cravates?|porte-?cl[ée]s",
     "clothing/accessories"),
    (r"surchemises?|vareuses?|macs?", "clothing/outerwear"),
    (r"chaussures?|baskets|bottines?|bottes|escarpins|sandales|mocassins|ballerines|derbies|mules", "clothing/footwear"),
    (r"jupes?|minijupes?|pantalons?|jeans?", "clothing/bottoms"),
    (r"manteaux?|vestes?|blousons?|parkas?|doudounes?|trench", "clothing/outerwear"),
    (r"robes?|combinaisons?", "clothing/dresses"),
    (r"pulls?|chemises?|chemisiers?|d[ée]bardeurs?|mailles?|sweats?|cardigans?|tops?|polos?", "clothing/tops"),
    (r"sacs?|pochettes?|trousses?|porte-?cartes?|portefeuilles?|ceintures?|lunettes|colliers?"
     r"|bagues?|bracelets?", "clothing/accessories"),
    (r"boucles d['’]oreilles?", "clothing/accessories"),
]]
# Tags that name the store's own category: "Class: Bottoms", "cat_coats:Coats", "category::swim",
# "Category - L0: Shirts & Button Downs", "storefront:category:Womens Shoes". The whole tag is matched.
CATEGORY_TAG = re.compile(r"^\W*(storefront:)?(categor(y|ies)|cat|class)(?![a-z])", re.I)


def loosen(text):
    """Words a store has run together: "knit_sweater", "GOCap", "FSTTight" -> "knit sweater", "GO Cap", "FST Tight"."""
    text = (text or "").replace("_", " ")
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)


def category_tags(store, tags):
    """The tags that say what the item is: the usual category tags, and any starting with one of the store's
    own "category_tags" prefixes (Vuori's "badge::", Frame's "collection::"), or every tag for a store whose
    tags are plain category words ("category_tags": "*": Machine-A's "Coats", "Outerwear", "RTW")."""
    own = store.get("category_tags") or ()
    if own == "*":
        return [str(t) for t in tags or ()]
    own = tuple(p.lower() for p in own)
    return [t for t in map(str, tags or ()) if CATEGORY_TAG.match(t) or (own and t.strip().lower().startswith(own))]


def categorize(*texts):
    """Return "group" or "group/kind" for the first text any rule matches, else None."""
    for text in texts:
        for rx, cat in RULES:
            if rx.search(text or ""):
                return cat
    return None


def categorize_more(store, ptype, title, tags):
    """The second pass (see FALLBACK) for an item categorize() left alone."""
    # "ptypes": a store's own product-type codes, as {"F": "clothing/footwear"}; matched on the type's first word
    codes = store.get("ptypes") or {}
    if (ptype or "").split()[:1] and ptype.split()[0] in codes:
        return codes[ptype.split()[0]]
    # "terms": a word that means something particular at this store, as {"suit": "clothing/dresses"}
    for word, cat in (store.get("terms") or {}).items():
        if re.search(r"\b%s\b" % re.escape(word), f"{ptype} {title}", re.I):
            return cat
    rules = FALLBACK + (FRENCH if store.get("lang") == "fr" else []) + RULES
    for text in [ptype, title]:
        text = loosen(text)
        for rx, cat in rules:
            if rx.search(text):
                return cat
    # The category tags only when they all point one way: "Coats", "Outerwear" is outerwear, but "Dresses &
    # Skirts", or "FOOTWEAR" beside "SOCKS", says nothing about which this one is.
    cats = {cat for tag in category_tags(store, tags) for rx, cat in rules if rx.search(loosen(tag))}
    return cats.pop() if len(cats) == 1 else None


# Within a kind, the finer type of a clothing item ("jeans" among bottoms). Matched against the product type and
# the title together; first match wins, so order matters: "Denim Shorts" are shorts, "Crew Neck Tee" is a
# T-shirt, a "Sport Coat" is a blazer. Anything unmatched has no type (the page lists it as "Other").
TYPES = {kind: [(re.compile(r"\b(%s)\b" % rx, re.I), name) for name, rx in rules] for kind, rules in {
    "tops": [
        ("tanks", r"tanks?|singlets?|camis?|camisoles?|(sports )?bras?|bralettes?|bodysuits?"),
        ("tshirts", r"t-?shirts?|tees?|(?<!['’])t"),
        ("polos", r"polos?|rugby"),
        ("knitwear", r"sweaters?|knit(s|wear|ted)?|cardigans?|jumpers?|turtlenecks?|magli(a|e|one|oni|eria)|girocollo"),
        ("sweatshirts", r"hood(s|y|ie|ies)?|hooded|sweatshirts?|sweats?|crew ?necks?|pullovers?"
                        r"|(half|quarter|1/4|1/2)[- ]zips?|halfzips?|fleece|felp[ae]"),
        ("shirts", r"\w*shirts?|shirting|button[- ](downs?|ups?)|blouses?|blus[ae]|camici[ae]|wovens|kurt(a|i)s?|tunics?"),
    ],
    "bottoms": [
        ("shorts", r"\w*shorts?|bermudas?|jorts|swim ?trunks?(?! trousers)|ciclist[ae]"),
        ("skirts", r"skirts?|skorts?|gonn[ae]|minigonn[ae]"),
        ("jeans", r"jeans?|denim|selvedge|selvage|boot ?cut|\d+(\.\d+)? ?oz"),
        ("sweatpants", r"sweatpants?|joggers?|jogging|track ?pants?|sweats"),
        ("leggings", r"leggings?|tights?"),
        ("underwear", r"boxers?|briefs?|underwear|trunks|panties|thongs?|hipsters?"),
        ("trousers", r"pants?|trousers?|chinos?|slacks|cargos?|pantalon[ei]|fatigues?|capris?"),
    ],
    "outerwear": [
        ("blazers", r"blazers?|sport ?coats?|suit jackets?"),
        ("vests", r"vests?|gilets?|waistcoats?|body ?warmers?"),
        ("coats", r"\w*coats?|parkas?|trench|cappott[oi]|ponchos?"),
        ("jackets", r"jackets?|jkt|bombers?|blousons?|anoraks?|windbreakers?|puffers?|shell|overshirts?|outershirts?"
                    r"|giacc(a|he)|giubb(ino|ini|otto|otti)|piumin[oi]|shackets?"),
    ],
    "dresses": [
        ("jumpsuits", r"jumpsuits?|rompers?|playsuits?|unitards?|onesies?|overalls?|dungarees?"),
        ("dresses", r"dress(es)?|gowns?|abit[oi]|vestit[oi]|kaftans?|caftans?|abayas?"),
    ],
    "footwear": [
        ("boots", r"boots?|stival(i|etti)"),
        ("sandals", r"sandals?|slides?|flip[- ]flops?|sandali|espadrilles?"),
        ("loafers", r"loafers?|derb(y|ies)|oxfords?|brogues?|mocassini|moccasins?|monk ?straps?|dress shoes?"
                    r"|wingtips?"),
        ("heels", r"heels?|pumps|flats?|ballerin[ae]s?|d[eé]collet[eé]s?|slingbacks?"),
        ("slippers", r"slippers?|clogs?|mules?"),
        ("sneakers", r"sneakers?|trainers?|runners?|running|(low|mid|high)[- ]tops?|slip[- ]ons?"),
    ],
    "accessories": [
        ("bags", r"\w*bags?|totes?|backpacks?|pouch(es)?|clutch(es)?|duffe?ls?|bors[ae]|wristlets?|pochettes?"
                 r"|hobos?|zain[oi]"),
        ("hats", r"hats?|caps?|beanies?|berets?|balaclavas?|headwear|cappell[oi]|visors?"),
        ("socks", r"socks?|calz(e|ini)"),
        ("scarves", r"scar(f|ves)|gloves?|mitt(s|ens)?|bandanas?|sciarp[ae]|guanti|stoles?|shawls?|foulards?"),
        ("eyewear", r"sunglasses|eyewear|glasses|occhiali"),
        ("jewelry", r"jewel(le)?ry|necklaces?|bracelets?|rings?|earrings?|pendants?|anklets?|brooch(es)?"
                    r"|cufflinks?|collan[ae]|braccial[ei]|anell[oi]|orecchini|gioielli"),
        ("belts", r"belts?|cintur[ae]"),
        ("wallets", r"wallets?|card ?holders?|portafogli[o]?"),
        ("watches", r"watch(es)?"),
    ],
}.items()}
# Shoes that say neither "sneaker" nor anything else ("Dunk Low", "1954R") from the makers of sneakers.
SNEAKER_MAKERS = re.compile(r"\b(nike|jordan|adidas|new balance|asics|converse|vans|salomon|hoka|saucony|puma|reebok"
                            r"|mizuno|norda|karhu|diadora|veja|autry)\b", re.I)


def item_type(kind, ptype, title, brand=""):
    """The finer type of a clothing item within its kind ("jeans"), or None."""
    # As a product type, "Knit Tops" is a store's shelf for its jersey tees and sweatshirts (Everlane), not for
    # sweaters, so only the title decides. A title that says "Knit Top" still counts as knitwear.
    if re.fullmatch(r"\s*knit tops?\s*", ptype, re.I):
        ptype = ""
    text = f"{ptype} {title}"
    for rx, name in TYPES.get(kind, ()):
        if rx.search(text):
            return name
    if kind == "footwear" and SNEAKER_MAKERS.search(f"{brand} {title}"):
        return "sneakers"
    return None


# ---------------------------------------------------------------- sizes

# Stores write the same size many ways ("LG", "Large", "50 (Large)", "3").
# size_tokens() turns the in-stock sizes of an item into comparable tokens:
#   tops / outerwear / dresses  letter sizes   "M"
#   bottoms           letter sizes or waist in inches   "M" / "32"
#   footwear          US men's sizes      "9.5"
# The page matches these against the sizes you pick in Settings.

SIZED = ("tops", "outerwear", "dresses", "bottoms", "footwear")
LETTERS = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL"]
ALPHA = {
    "xxs": "XXS", "2xs": "XXS", "xxsmall": "XXS",
    "xs": "XS", "xsmall": "XS", "extrasmall": "XS",
    "s": "S", "sm": "S", "small": "S",
    "m": "M", "md": "M", "med": "M", "medium": "M",
    "l": "L", "lg": "L", "large": "L",
    "xl": "XL", "xlg": "XL", "xlarge": "XL", "extralarge": "XL",
    "xxl": "XXL", "2xl": "XXL", "2x": "XXL", "xxlarge": "XXL", "extraextralarge": "XXL",
    "xxxl": "XXXL", "3xl": "XXXL", "3x": "XXXL", "xxxlarge": "XXXL", "extraextraextralarge": "XXXL",
}
ONE_SIZE = {"os", "osfa", "onesize", "onesizefitsall", "fitsall", "free", "freesize", "f", "one"}
JAPAN = {0: "XXS", 1: "XS", 2: "S", 3: "M", 4: "L", 5: "XL", 6: "XXL"}  # Japanese 1-5 sizing: 3 is a medium


def compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def alpha(label):
    """Letter sizes in a label: "LG" -> L, "x-large" -> XL, "50 (Large)" -> L, "1-S/M" -> S and M."""
    low = label.lower()
    for whole in [low, re.sub(r"\([^)]*\)", "", low)] + re.findall(r"\(([^)]*)\)", low):
        if compact(whole) in ALPHA:
            return {ALPHA[compact(whole)]}
    found = set()
    for chunk in re.split(r"[\s/,()]+", low):
        parts = [chunk] if compact(chunk) in ALPHA else chunk.split("-")   # keep "x-small" whole
        found |= {ALPHA[compact(p)] for p in parts if compact(p) in ALPHA}
    return found


def number(label):
    m = re.search(r"\d+(?:\.\d+)?", label.replace("½", ".5").replace(" 1/2", ".5"))
    return float(m.group(0)) if m else None


def band(n, start, width, first):
    """Letter size for n on an evenly spaced scale: start is the smallest value of size `first`."""
    i = LETTERS.index(first) + int((n - start) // width)
    return {LETTERS[i]} if 0 <= i < len(LETTERS) else set()


def clothing_size(kind, n, scale):
    """Tokens for a bare number, read against every number the item is sold in."""
    if n <= 6 and max(scale) <= 6:
        return {JAPAN[int(n)]}
    if kind == "bottoms":
        if 26 <= n < 44:
            return {str(int(n))}                    # waist in inches
        if 66 <= n <= 112:
            return {str(round(n / 2.54))}           # waist in cm
        return band(n, 44, 2, "XS") if 44 <= n < 60 else set()   # European 44-58
    if 14 <= n < 19:
        return band(n, 14, 1, "S")                  # shirt collar in inches
    if 34 <= n < 60:
        european, chest = band(n, 44, 2, "XS"), band(n, 34, 2, "XS")   # 48 = M; 38 chest = M
        if n >= 48 or (n >= 44 and max(scale) >= 48):
            return european
        if n < 44 or min(s for s in scale if s >= 34) <= 42:
            return chest
        return european | chest                     # only 44/46 on offer: could be either
    return set()


def shoe_us(label):
    """Candidate US men's sizes for a shoe label: "EU 43" -> 9.5 and 10, "27.5 cm" -> 9.5, "UK 8" -> 8.5 and 9."""
    low = label.lower().replace("½", ".5").replace(" 1/2", ".5")
    n = r"(\d+(?:\.\d+)?)"

    def find(*patterns):
        for p in patterns:
            m = re.search(p, low)
            if m:
                return float(m.group(1))
        return None

    womens = re.search(r"\(w\)|wom[ae]n|\bw\s*\d|\d\s*w\b", low)
    us = find(r"\bm(?:en'?s)?\s*" + n, n + r"\s*m\b")                      # "M9/W10.5"
    if us is None and not womens:
        us = find(r"\bus\s*(?:men'?s)?\s*" + n, n + r"\s*us\b")
    uk, eu, cm = find(r"\buk\s*" + n, n + r"\s*uk\b"), find(r"\b(?:eu|eur|fr|it)\s*" + n), find(n + r"\s*cm\b")
    if us is None and uk is None and eu is None and cm is None and not womens:
        bare = number(low)
        if bare is None:
            return set()
        if 200 <= bare <= 330:
            cm = bare / 10                                                 # millimetres
        elif 34 <= bare <= 50:
            eu = bare
        elif 21.5 <= bare <= 32:
            cm = bare
        else:
            us = bare
    if us is not None:
        out = [us]
    elif uk is not None:
        out = [uk + 0.5, uk + 1]
    elif eu is not None:
        # dress shoes run EU - 33; sneakers drift to EU - 34 in larger sizes
        out = [eu - 33, eu - (33 if eu < 42 else 33.5 if eu < 44 else 34)]
    elif cm is not None:
        out = [cm - 18]
    else:
        return set()
    return {f"{round(x * 2) / 2:g}" for x in out if 3 <= x <= 16}


def size_tokens(kind, live, every):
    """Normalised sizes for the in-stock labels `live`; `every` is all labels the item comes in."""
    if kind == "footwear":
        return sorted({t for s in live for t in shoe_us(s)}, key=float)
    scale = [n for n in map(number, every) if n is not None] or [0]
    out = set()
    for s in live:
        if compact(s) in ONE_SIZE:
            out.add("OS")
        elif alpha(s):
            out |= alpha(s)
        elif number(s) is not None:
            out |= clothing_size(kind, number(s), scale)
    return sorted(out, key=lambda t: (LETTERS.index(t) if t in LETTERS else 99, t))


def group_kind(store, ptype, title, brand="", tags=()):
    """(group, kind, type) for an item: the store's group by default, sorted per item for clothing and mixed
    stores; clothing also gets its type within the kind where the listing says ("jeans" among bottoms)."""
    group, kind = store.get("group", "other"), None
    # Clothing stores, and others marked "mixed", get sorted per item.
    # Pure kitchen / stationery shops don't: "Pen Cap" is not a hat.
    if group == "clothing" or store.get("mixed"):
        found = categorize(ptype, title)
        # The second pass only for what would otherwise be "Other clothing": not where the store names a default
        # kind (Shinola's "Zip Brief" is a briefcase), nor a mixed store whose default is not clothing.
        if not found and group == "clothing" and not store.get("kind"):
            found = categorize_more(store, ptype, title, tags)
        group, _, kind = (found or group).partition("/")
        if group == "clothing":
            kind = kind or store.get("kind", "other")
            # "_" read as a space ("knit_sweater"), but run-together words left alone: "TechKnit" is a fabric
            return group, kind, item_type(kind, ptype.replace("_", " "), title.replace("_", " "), brand)
    return group, kind or None, None


# ---------------------------------------------------------------- currency

# Units per US dollar; only used if the rate service and the last build's rates are both missing.
FALLBACK_RATES = {"USD": 1, "JPY": 160, "GBP": 0.76, "EUR": 0.91, "CAD": 1.43, "AUD": 1.48, "KRW": 1340}


def usd_rates(previous):
    """Units of each currency per US dollar: today's, else the last build's, else the table above."""
    status, body = get("https://open.er-api.com/v6/latest/USD")
    try:
        rates = json.loads(body)["rates"]
        if rates["USD"] == 1:
            return rates
    except Exception:
        pass
    return {**FALLBACK_RATES, **(previous.get("rates") or {})}


# ---------------------------------------------------------------- catalogue

# data.json holds what is on the list (sale items, and a few stores' full range). Everything else in stock goes
# in a catalogue: data/catalog/<store>.json keeps it per store between runs, and docs/brands/<n>.json regroups
# it by brand for the page, which fetches only the files of the brands you follow. A brand's file number is
# worked out from its name the same way here and in the page (bucket() in docs/index.html).
CATALOG_DIR = os.path.join(ROOT, "data", "catalog")
BRANDS_DIR = os.path.join(ROOT, "docs", "brands")
BRAND_FILES = 512
EVERYTHING_PATH = os.path.join(ROOT, "docs", "everything.json")
# The page's "Search full price" switch searches the whole catalogue in a background worker, from one file:
# docs/everything.json, {"stores": [[host, name, currency], ...], "items": [row, ...]}, a row being these fields in
# this order (unpack() in docs/index.html reads them back). Rows instead of objects, a store's name and currency
# once, an image without its cache-busting "?v=" and Shopify's file prefix as "~": a third of the size to parse.
EVERYTHING_FIELDS = ("store", "handle", "title", "brand", "price", "usd", "was", "off", "group", "kind", "type",
                     "g", "sz", "img", "url", "m", "mn", "f", "fc", "fs")
SHOPIFY_FILES = "https://cdn.shopify.com/s/files/1/"


def brand_key(name):
    """A brand name reduced to its letters and digits (the page's brandKey does the same)."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(wo)?mens\b", "", (name or "").lower(), flags=re.A))


def brand_file(key):
    n = 0
    for ch in key:
        n = (n * 31 + ord(ch)) % BRAND_FILES
    return n


def write_lines(path, items):
    """A JSON list, one item per line."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("[\n" + ",\n".join(json.dumps(it, ensure_ascii=False, separators=(",", ":")) for it in items) + "\n]\n")


def write_catalog(catalog, stores):
    """Replace the catalogue file of every store read cleanly this run; other stores keep theirs."""
    os.makedirs(CATALOG_DIR, exist_ok=True)
    name = lambda host: re.sub(r"[^\w.-]", "_", host) + ".json"
    for host, found in catalog.items():
        # The page fills in what every catalogue item shares (in stock, no tags, usually no discount) and what
        # it can work out (a Shopify product's address from its id, a dollar price's value in dollars): fill()
        # in docs/index.html. Across 200,000 items that is a quarter of the size.
        slim = [{k: v for k, v in it.items()
                 if k not in ("tags", "avail") and v not in (None, 0, []) and not (k == "usd" and it["cur"] == "USD")
                 and not (k == "url" and v == "https://" + it["id"].replace("/", "/products/", 1))} for it in found]
        write_lines(os.path.join(CATALOG_DIR, name(host)), sorted(slim, key=lambda it: it["id"]))
    keep = {name(host) for host in stores}
    for file in os.listdir(CATALOG_DIR):
        if file not in keep:
            os.remove(os.path.join(CATALOG_DIR, file))


def write_brand_files(marks=None):
    """Regroup every store's catalogue by brand into docs/brands/, with an index of the brands for the page's
    brand lookup: index.json lists [name, items] for each brand, biggest first, under its most common spelling.
    marks: the product each matched listing belongs to (see match_products)."""
    files, names = [[] for _ in range(BRAND_FILES)], {}
    for file in sorted(os.listdir(CATALOG_DIR)) if os.path.isdir(CATALOG_DIR) else []:
        for it in load_json(os.path.join(CATALOG_DIR, file), []):
            if marks and it["id"] in marks:
                it.update(marks[it["id"]])
            name = (it.get("brand") or it["store_name"]).strip()
            key = brand_key(name)
            if key:
                files[brand_file(key)].append(it)
                spellings = names.setdefault(key, {})
                spellings[name] = spellings.get(name, 0) + 1
    os.makedirs(BRANDS_DIR, exist_ok=True)
    for n, found in enumerate(files):
        write_lines(os.path.join(BRANDS_DIR, f"{n}.json"), found)
    write_everything([it for found in files for it in found])
    index = [[max(sp, key=sp.get), sum(sp.values())] for sp in names.values()]
    write_lines(os.path.join(BRANDS_DIR, "index.json"), sorted(index, key=lambda row: (-row[1], row[0].lower())))


MATCH_FIELDS = ("m", "mn", "f", "fc", "fs")
PHOTOS_PER_RUN = 6000      # new photos read per build for matching (about 3 minutes); the rest wait for the next run


def match_products(items, state, rates, stores):
    """Find the listings that are the same item at different stores (matching.py), across the list and every
    store's catalogue on disk (stores not read this run keep theirs). Marks each matched item on the list with
    its exact item and design (`m`, `f` and their counts: matching.match), writes docs/matches/, and returns the
    marks for the brand files. The catalogue files are left without them: they are the build's own input, and the marks are
    worked out afresh every run."""
    t = time.time()
    _, every = matching.load_listings(None, CATALOG_DIR)
    for it in items:
        for k in MATCH_FIELDS:
            it.pop(k, None)
        if it.get("price") is not None:
            every[it["id"]] = it                       # the list's copy wins: it is the one shown
    cache, read = imagesig.load_cache(), [0]

    def look(ids):
        """The photos matching asks for, from the cache or Shopify's image CDN (imagesig.py)."""
        found, read[0] = imagesig.signatures([every[x].get("img") for x in ids], cache, limit=PHOTOS_PER_RUN)
        return {x: found[k] for x in ids for k in [imagesig.photo_key(every[x].get("img"))] if k in found}

    marks = matching.match(every, state, rates, {h: s.get("name", h) for h, s in stores.items()}, look)
    imagesig.save_cache(cache)
    for it in items:
        if it["id"] in marks:
            it.update(marks[it["id"]])
    print(f"matched {len({v['m'] for v in marks.values() if 'm' in v})} items across stores and "
          f"{len({v['f'] for v in marks.values() if 'f' in v})} designs in several colourways ({len(marks)} listings) "
          f"in {time.time() - t:.0f} s ({read[0]} photos read)", flush=True)
    return marks


def write_data(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def regroup():
    """`--brands`: regroup the catalogue for the page when only the page changed, and match products again,
    since docs/data.json then comes straight from the data branch."""
    path = os.path.join(ROOT, "docs", "data.json")
    data = load_json(path, {"items": []})
    cfg = load_json(os.path.join(ROOT, "stores.json"), {"stores": []})
    rates = {**FALLBACK_RATES, **(data.get("rates") or {})}
    marks = match_products(data["items"], history.load_state(), rates, {s["host"]: s for s in cfg["stores"]})
    write_brand_files(marks)
    if data["items"]:
        write_data(path, data)


def write_everything(items):
    """docs/everything.json: every catalogue item, packed (see EVERYTHING_FIELDS)."""
    stores, rows = {}, []
    for it in items:
        host = it["store"]
        if host not in stores:
            stores[host] = [len(stores), [host, it.get("store_name", host), it.get("cur", "USD")]]
        n = stores[host][0]
        img = re.sub(r"\?v=\d+$", "", it.get("img") or "")
        if img.startswith(SHOPIFY_FILES):
            img = "~" + img[len(SHOPIFY_FILES):]
        handle = it["id"].split("/", 1)[1] if "/" in it["id"] else it["id"]
        url = it.get("url") if it.get("url") != f"https://{host}/products/{handle}" else None
        row = [n, handle] + [it.get(k) for k in EVERYTHING_FIELDS[2:13]] + [img or None, url] + \
              [it.get(k) for k in EVERYTHING_FIELDS[15:]]
        if it.get("cur", "USD") != stores[host][1][2]:       # a store quoting two currencies: say it per row
            row.append(it["cur"])
        while row and row[-1] in (None, 0, [], ""):          # trailing blanks cost bytes and say nothing
            row.pop()
        rows.append(row)
    write_data(EVERYTHING_PATH, {"stores": [v[1] for v in sorted(stores.values())], "items": rows})


# ---------------------------------------------------------------- main

def main():
    """Build docs/data.json. `--stalest N` refreshes only the N stores fetched longest ago (link-only stores, which
    cost nothing, always run); every other store keeps its items and status from the last run. The hourly runs
    use it; the daily full run, and runs after a change to the stores or this script, refresh everything."""
    global DELAY, PATIENT, PATIENT_UNTIL
    began = time.time()
    stalest = int(sys.argv[sys.argv.index("--stalest") + 1]) if "--stalest" in sys.argv else None
    cfg = load_json(os.path.join(ROOT, "stores.json"), None)
    if not cfg:
        sys.exit("stores.json missing or invalid")
    settings = cfg.get("settings", {})
    min_off = settings.get("min_discount", 20)
    max_pages = settings.get("max_pages", 20)
    max_items = settings.get("max_items_per_store") or None   # None: no cap
    DELAY = settings.get("delay_seconds", 1.0)
    stores = {s["host"]: s for s in cfg["stores"]}

    out_path = os.path.join(ROOT, "docs", "data.json")
    state = history.load_state()                # host -> everything tracked about that store's products
    previous = load_json(out_path, {"items": []})
    NOW = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")
    T = NOW[:16]                                # the time written into the tracking files (UTC, to the minute)

    # ---- sort wishlist URLs by store
    wish_products, wish_pages = {}, {}
    try:
        lines = open(os.path.join(ROOT, "wishlist.txt"), encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        url = line.strip()
        if not url.startswith("http"):
            continue
        u = urlparse(url)
        host = u.netloc
        # blueowl.us and www.blueowl.us are the same store
        alt = host[4:] if host.startswith("www.") else "www." + host
        if host not in stores and alt in stores:
            host = alt
        store = stores.setdefault(host, {"host": host, "name": host, "platform": "link", "group": "other"})
        m_prod = re.search(r"/products/([^/?#]+)", u.path)
        if store["platform"] == "shopify" and m_prod:
            wish_products.setdefault(host, set()).add(unquote(m_prod.group(1)))
        else:
            clean = url.split("#")[0]
            wish_pages.setdefault(host, [])
            if clean not in wish_pages[host]:
                wish_pages[host].append(clean)

    items, status = {}, []

    def add(item, tag):
        if not item:
            return
        cur = items.setdefault(item["id"], item)
        if tag not in cur["tags"]:
            cur["tags"].append(tag)

    def fetch_store(host):
        """Everything wanted from one store: (item, tag) pairs, errors, how many items qualified, a reading of
        every product in its catalogue (see history.track), whether that is the whole catalogue, and the
        in-stock items that did not qualify for the list (they go in the store's catalogue file).

        Stores are fetched on separate threads; requests to any one store stay
        sequential and spaced out.
        """
        store, found, errors, listed, rest = stores[host], [], [], [], []
        readings, whole = {}, False
        known = state.get(host, {}).get("products", {})
        wanted = set(wish_products.get(host, set()))
        # "feed": a shop whose own address is a custom front end still serves its Shopify data from another
        # host (often <name>.myshopify.com). Items keep the shop's address; only the reading goes there.
        feed = store.get("feed", host)
        # "locale": "/en" -- a store that titles its products in French at its root and in English under a path.
        # Only for a store whose path keeps the same handles and currency (Isabel Marant): AMI and Rouje switch
        # to dollars and Julian renames its handles under /en-us, which would start their price history over.
        locale = store.get("locale", "")
        LANE.delay = store.get("delay_seconds")      # a store whose robots.txt asks for a longer Crawl-delay
        LANE.gzip = store.get("read") == "listing"
        print(f"{host} ...", flush=True)

        def take(it, full, handle=None):
            """One product of the store's catalogue: track it, and list it if it is on sale (or "all")."""
            it["group"], it["kind"], it["type"] = group_kind(
                store, it["ptype"], it["title"], store.get("brand") or it["brand"], it.pop("raw_tags", ()))
            readings[it["id"]] = {"price": it["price"], "off": it["off"], "avail": it["avail"],
                                  "brand": store.get("brand") or it["brand"],
                                  "kind": it["kind"] or it["group"], "published": it["published"]}
            # A quiet price cut: the store shows no "was" price, but the price is well under
            # the highest one tracked here.
            high = max((pt[1] for pt in known.get(it["id"], {}).get("h", [])), default=0)
            if high and it["off"] < min_off and it["price"] <= high * (1 - min_off / 100):
                it["was"], it["tracked_drop"] = high, True
                it["off"] = round((high - it["price"]) / high * 100)
            if handle in wanted:
                found.append((it, "wish"))
                wanted.discard(handle)
            # a listing that cannot tell (avail None) shows what the shop offers for sale
            if it["avail"] is not False:
                (listed if it["off"] >= min_off or full else rest).append(it)

        try:
            if store["platform"] == "shopify":
                if store.get("browse") or wanted:
                    # if the store won't say this time, assume the currency it used last time
                    store["currency"] = served_currency(feed + locale) or state.get(host, {}).get("cur") \
                        or store.get("currency", "USD")
                    if store["currency"] != state.get(host, {}).get("cur"):
                        known = {}               # prices tracked in another currency can't be compared
                if store.get("browse"):
                    # "collections": ["visvim", ...] reads only those brand collections. It's for big
                    # retailers that rate-limit a full catalog read but stock a few brands worth tracking.
                    seen, whole = set(), not store.get("collections")
                    for c in store.get("collections") or [None]:
                        path = locale + (f"/collections/{c}" if c else "")
                        # "all": true lists the store's whole in-stock range, not only its discounts;
                        # "all": ["visvim"] does that for just those collections.
                        full = store.get("all") is True or c in (store.get("all") or [])
                        try:
                            for n, p in enumerate(shopify_feed(feed, path, max_pages), 1):
                                if n == max_pages * 250:
                                    whole = False            # the page cap cut the catalogue short
                                it = shopify_item(host, p, locale=locale)
                                if not it or p.get("handle") in seen:
                                    continue
                                seen.add(p.get("handle"))
                                take(it, full, p.get("handle"))
                        except Exception as e:
                            errors.append(f"catalog{path}: {e}")
                for handle in sorted(wanted):
                    url = f"https://{host}/products/{handle}"
                    try:
                        found.append((shopify_single(host, handle, feed), "wish"))
                    except Gone:  # taken down by the store: not our error
                        found.append((link_item(host, url, "No longer listed"), "wish"))
                    except Exception as e:
                        errors.append(f"{handle}: {e}")
                        found.append((link_item(host, url, "could not read price"), "wish"))
            elif store.get("browse") and store.get("read"):
                # Not an open Shopify feed: the store's listing pages or shop API, read by listings.py.
                # "whole": true says the listed pages cover the store's whole range.
                run = {"errors": errors, "cut": False}
                for d in listings.read(host, store, get, store.get("max_pages", max_pages), run):
                    it = listing_item(host, store, d)
                    if it and it["id"] not in readings:
                        take(it, store.get("all") is True)
                whole = bool(store.get("whole")) and not run["cut"]
            # A few stores would swamp the rest, so keep each store's deepest discounts.
            listed.sort(key=lambda it: -it["off"])
            found += [(it, "sale" if it["off"] >= min_off else "full") for it in listed[:max_items]]

            for url in wish_pages.get(host, []):
                if store["platform"] == "page":
                    try:
                        found.append((page_item(host, url), "wish"))
                        continue
                    except Gone:
                        found.append((link_item(host, url, "No longer listed"), "wish"))
                        continue
                    except Exception as e:
                        errors.append(f"{url[-40:]}: {e}")
                found.append((link_item(host, url), "wish"))
        except Exception as e:  # never let one store take the whole build down
            errors.append(f"unexpected: {e}")
        return found, errors, len(listed), readings, whole, rest

    hosts = list(stores)
    if stalest is not None:
        # never-fetched stores sort first (""), then the oldest refresh
        feeds = sorted((h for h in hosts if stores[h]["platform"] != "link"),
                       key=lambda h: state.get(h, {}).get("fetched", ""))
        chosen = set(feeds[:stalest]) | {h for h in hosts if stores[h]["platform"] == "link"}
        hosts = [h for h in hosts if h in chosen]
        print(f"refreshing the {min(stalest, len(feeds))} stores updated longest ago; the other {len(feeds) - min(stalest, len(feeds))} keep their items", flush=True)
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(settings.get("workers", 6)) as pool:
        results = list(pool.map(fetch_store, hosts))

    # A store that answers 429 (too many requests) is skipped on the first pass, so the run moves on to the
    # others; Shopify sometimes rate-limits nearly a whole run, apparently by the shared address of the machine
    # running it. Come back to those stores once the rest are done (and at least COME_BACK seconds after the
    # run began), and only then wait and retry on a 429, a few stores at a time, for COME_BACK_FOR seconds at
    # most. Stores still refused fall back to their items from the last run.
    limited = [n for n, r in enumerate(results) if any("429" in e for e in r[1])]
    still = len(limited)
    if len(limited) > sum(1 for h in hosts if stores[h]["platform"] != "link") / 2:
        # Most stores refused at once: it is this machine's address being turned away, not the stores' own limits.
        # Waiting on each of them helps nothing (it once held a run for an hour). They keep their old items, and
        # the next run tries again.
        print(f"{len(limited)} stores were rate-limited (HTTP 429): this machine is being refused; leaving them to the next run", flush=True)
    elif limited:
        pause = max(0, COME_BACK - (time.time() - started))
        print(f"{len(limited)} stores were rate-limited (HTTP 429); coming back to them in {pause:.0f} s", flush=True)
        time.sleep(pause)
        PATIENT, PATIENT_UNTIL = True, time.time() + COME_BACK_FOR
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            for n, again in zip(limited, pool.map(fetch_store, [hosts[n] for n in limited])):
                results[n] = again
        PATIENT = False
        still = sum(any("429" in e for e in results[n][1]) for n in limited)
        print(f"after coming back, {still} still rate-limited", flush=True)

    done = dict(zip(hosts, results))
    catalog = {}                               # host -> its in-stock items that are not on the list, for stores read cleanly
    old_status = {s["host"]: s for s in previous.get("status", [])}
    for host, store in stores.items():
        if host not in done:                   # not refreshed this run: keep last run's items and status as they were
            for old in previous.get("items", []):
                if old.get("store") == host and old["id"] not in items:
                    old["carried"] = True
                    items[old["id"]] = old
            if host in old_status:
                status.append(old_status[host])
            continue
        found, errors, qualified, readings, whole, rest = done[host]
        name, before = store.get("name", host), len(items)
        for item, tag in found:
            add(item, tag)
            if item and item.get("price") is not None:     # followed items read one at a time are tracked too
                readings.setdefault(item["id"], {"price": item["price"], "off": item["off"], "avail": item["avail"]})
        if not errors and store.get("browse"):
            catalog[host] = [it for it in rest if it["id"] not in items]
        if readings or store["platform"] != "link":
            history.track(state.setdefault(host, {}), readings, T, min_off, store.get("currency", "USD"), errors, whole)

        # If a store failed completely, keep what we showed last time.
        got = len(items) - before
        if errors and got == 0:
            for old in previous.get("items", []):
                if old.get("store") == host and old["id"] not in items:
                    old["stale"] = True
                    items[old["id"]] = old
        if store["platform"] != "link" or errors:
            # "qualified" is the number before the per-store cap; the page uses it to spot high-volume stores
            status.append({"host": host, "name": name, "ok": not errors, "count": len(items) - before,
                           "qualified": max(qualified, len(items) - before), "errors": errors[:5]})

    # ---- decorate: store info, category, sizes, US dollar price, price history
    rates = usd_rates(previous)
    wide = {host: history.storewide(state[host]) for host in done if host in state}

    def decorate(it):
        s = stores.get(it["store"], {})
        it["store_name"] = s.get("name", it["store"])
        if s.get("brand"):                       # brand-run stores whose own "vendor" field is not the brand
            it["brand"] = s["brand"]
        ptype = it.pop("ptype", "")
        it.pop("raw_tags", None)
        old = it.get("stale") or it.pop("carried", False)   # reused from the last run: keep what it had
        it.pop("published", None)
        if not old and "kind" not in it:         # items from a store's feed were sorted as they were read
            it["group"], it["kind"], it["type"] = group_kind(s, ptype, it["title"], it.get("brand") or "")
        it["cur"] = it.get("cur") or s.get("currency", "USD")
        if not it.get("g") and s.get("fits"):    # a women's or men's boutique: unlabelled items are that
            it["g"] = {"women": "w", "men": "m"}[s["fits"]]
        if not it.get("g"):
            it.pop("g", None)
        if not it.get("type"):
            it.pop("type", None)
        every =it.pop("sizes_all", None) or it["sizes"]
        if not old:
            sz = size_tokens(it["kind"], it["sizes"], every) if it["kind"] in SIZED else []
            it.pop("sz", None)
            if sz:
                it["sz"] = sz
            rec = state.get(it["store"], {}).get("products", {}).get(it["id"])
            if rec and it["off"] >= min_off and it["avail"] and every:
                history.record_sizes(rec, T, sz or it["sizes"], len(every))
            if it.get("price") is not None and rates.get(it["cur"]):
                it["usd"] = round(it["price"] / rates[it["cur"]], 2)
        if old:
            return
        # What the tracking file says about it: when it appeared, its price changes.
        st = state.get(it["store"], {})
        rec = st.get("products", {}).get(it["id"], {})
        h, price = rec.get("h"), it.get("price")
        if not h or price is None:
            return
        points = history.price_points(h)
        if len(points) > 1:
            it["hist"] = points[-12:]
            high = max(p for _, p in points)
            if not it["off"] and high > price:
                it["was"], it["tracked_drop"] = high, True
                it["off"] = round((high - price) / high * 100)
            pct = history.in_storewide(h, wide.get(it["store"], []))
            if pct:
                it["storewide"] = pct            # its latest price change was the whole store's, not this item's
        if it["off"] >= min_off and it.get("was") and not it.get("tracked_drop"):
            check = history.was_check(h, it["was"], min_off)
            if check:
                it["pre"] = check                # what was seen before the sale, to judge the "was" price
        back = history.back_in_stock(h)
        if back and history.days(back, T) <= 14:
            it["back"] = back[:10]
        # "New" on the page means new in the last few days: a sale item by when its sale began, a full-price item
        # by when the store published it. What a store's first read finds on sale had been so for an unknown
        # time already, so it gets no date.
        if it["off"] < min_off and rec.get("p"):
            it["first_seen"] = rec["p"]
            return
        move = history.last_move(h) if it.get("tracked_drop") else None    # a quiet cut is new when the price fell
        began = move[0] if move else history.since(h, min_off)
        if began > st["since"]:
            it["first_seen"] = began[:10]

    for it in items.values():
        decorate(it)
    for found in catalog.values():
        for it in found:
            decorate(it)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    for host in [h for h in state if h not in stores]:      # a store taken out of stores.json is forgotten
        del state[host]
    history.save_state(state)
    write_catalog(catalog, stores)
    write_brand_files(match_products(list(items.values()), state, rates, stores))
    # a line about this run for the metrics page: the only place its speed and its refusals can be seen from outside
    run = {"t": T, "every_store": stalest is None, "stores": sum(1 for h in hosts if stores[h]["platform"] != "link"),
           "minutes": round((time.time() - began) / 60, 1), "requests": COUNT["requests"], "refused": COUNT["refused"],
           "limited": len(limited), "still_limited": still, "failed": sum(1 for s in status if not s["ok"]),
           "lanes": settings.get("workers", 6)}
    history.write_metrics(state, cfg, NOW, run)
    write_data(out_path, {
        "generated": NOW,
        "min_discount": min_off,
        "rates": {c: rates[c] for c in sorted({i["cur"] for i in items.values()}) if c in rates},
        "status": status,
        "items": sorted(items.values(), key=lambda i: (-i["off"], i["store"], i["title"])),
    })

    bad = [s["host"] for s in status if not s["ok"]]
    print(f"\n{len(items)} items written. Stores with errors: {', '.join(bad) or 'none'}")


if __name__ == "__main__":
    if "--brands" in sys.argv:          # only regroup the catalogue for the page (a page change is being published)
        regroup()
    else:
        main()
