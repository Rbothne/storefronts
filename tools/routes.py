#!/usr/bin/env python3
"""Ask every store the build cannot read which of the known routes it will answer.

One request to each store's home page, then only what that page named: a Shopify products.json on the host and
on any *.myshopify.com name the page lets slip, an Algolia application whose key the page prints (and what its
indexes hold), another search platform worth a look by hand, product data the page already carries for its own
scripts, and sibling storefronts. Nothing is fetched twice and nothing is asked harder; a refusal is recorded
and the next route tried. Standard library only.

    py tools/routes.py targets.json routes.json [workers]

where targets.json is [{"host": ..., "name": ..., "where": ...}, ...] -- build it from the stores in
stores.json with no "read", and from the tables in stores-unread.md. Fold what comes back into that file:
the point of this tool is that nobody has to re-probe 131 stores to find out where the leads are.
"""
import json, io, re, sys, gzip, urllib.request, urllib.error, concurrent.futures as cf
from urllib.parse import urlencode, quote
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


def get(url, accept=HTML, timeout=25, cap=3_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept,
                                               "Accept-Language": "en-US,en;q=0.9",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(cap)
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            return r.status, raw.decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", dict(e.headers)
    except Exception as e:
        return 0, str(e)[:60], {}


ALGOLIA = [r'algoliaApplicationId\\?"?\s*[:=]\s*\\?"([A-Z0-9]{8,16})',
           r'algolia_app_id\\?"?\s*[:=]\s*\\?"([A-Z0-9]{8,16})',
           r'algoliaAppId\\?"?\s*[:=]\s*\\?"([A-Z0-9]{8,16})',
           r'applicationId\\?"?\s*[:=]\s*\\?"([A-Z0-9]{8,16})',
           r'([A-Z0-9]{8,16})-dsn\.algolia\.net',
           r'([A-Z0-9]{8,16})\.algolia\.net']
AKEY = [r'algoliaApiKey\\?"?\s*[:=]\s*\\?"([0-9a-f]{32})',
        r'algolia_search_api_key\\?"?\s*[:=]\s*\\?"([0-9a-f]{32})',
        r'algoliaSearchApiKey\\?"?\s*[:=]\s*\\?"([0-9a-f]{32})',
        r'searchApiKey\\?"?\s*[:=]\s*\\?"([0-9a-f]{32})',
        r'x-algolia-api-key=([0-9a-f]{32})',
        r'apiKey\\?"?\s*[:=]\s*\\?"([0-9a-f]{32})']
AIDX = [r'algolia_index_name\\?"?\s*[:=]\s*\\?"([\w.\-]{3,60})',
        r'indexName\\?"?\s*[:=]\s*\\?"([\w.\-]{3,60})',
        r'algoliaIndex\w*\\?"?\s*[:=]\s*\\?"([\w.\-]{3,60})',
        r'(PLP_[A-Z0-9_]{4,50})',
        r'algoliaProductsIndexPrefix\\?"?\s*[:=]\s*\\?"([\w.\-]{3,60})']
OTHER = {"constructor": r'constructorio|cnstrc|constructor\.io',
         "bloomreach": r'bloomreach|dxpapi\.com',
         "klevu": r'klevu\.com|klevuApiKey',
         "searchspring": r'searchspring',
         "attraqt": r'attraqt|fredhopper',
         "coveo": r'coveo\.com',
         "typesense": r'typesense'}


def ask_algolia(app, key, index, hits=1):
    url = "https://%s-dsn.algolia.net/1/indexes/%s?%s" % (
        app.lower(), quote(index),
        urlencode({"x-algolia-api-key": key, "x-algolia-application-id": app, "hitsPerPage": hits, "page": 0}))
    st, body, _ = get(url, accept="application/json", cap=400_000)
    if st != 200:
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    return d.get("nbHits"), (d.get("hits") or [{}])[0]


def indexes(app, key):
    st, body, _ = get("https://%s-dsn.algolia.net/1/indexes?%s" % (
        app.lower(), urlencode({"x-algolia-api-key": key, "x-algolia-application-id": app})),
        accept="application/json", cap=400_000)
    if st != 200:
        return []
    try:
        return [i["name"] for i in json.loads(body).get("items", [])]
    except Exception:
        return []


def probe(t):
    host, out = t["host"], dict(t)
    out["routes"] = []
    st, page, hdr = get("https://%s/" % host)
    out["home"] = st
    out["server"] = (hdr.get("Server") or "")[:22]
    if st != 200 or len(page) < 500:
        for path in ("/collections/all", "/shop", "/sale", "/products"):
            st2, page2, _ = get("https://%s%s" % (host, path))
            if st2 == 200 and len(page2) > 2000:
                st, page = st2, page2
                out["home"] = "%s %s" % (st2, path)
                break
    if st != 200 or len(page) < 500:
        out["routes"].append("page refused (%s)" % st)
        return out

    for cand in [host] + sorted(set(re.findall(r'([a-z0-9][a-z0-9-]{1,40}\.myshopify\.com)', page)))[:3]:
        sj, body, _ = get("https://%s/products.json?limit=1" % cand, accept="application/json", cap=200_000)
        if sj == 200 and '"products"' in body:
            try:
                n = len(json.loads(body).get("products") or [])
            except ValueError:
                n = 0
            if n:
                out["routes"].append("SHOPIFY %s" % cand)
                out["shopify"] = cand
                break

    apps = list(dict.fromkeys(m for p in ALGOLIA for m in re.findall(p, page)))
    keys = list(dict.fromkeys(m for p in AKEY for m in re.findall(p, page)))
    idxs = list(dict.fromkeys(m for p in AIDX for m in re.findall(p, page)))
    if apps and keys:
        out["algolia_seen"] = {"apps": apps[:3], "indexes": idxs[:8]}
        found = False
        for app in apps[:2]:
            for key in keys[:3]:
                names = indexes(app, key)
                order = [i for i in names if re.search(r"(?i)prod|plp|item|catalog", i)] or names or idxs[:5]
                for index in order[:4]:
                    got = ask_algolia(app, key, index)
                    if got and got[0]:
                        out["routes"].append("ALGOLIA %s / %s = %s products" % (app, index, got[0]))
                        out["algolia"] = {"app": app, "key": key, "index": index}
                        out["all_indexes"] = names[:20]
                        out["sample_fields"] = sorted(got[1].keys())[:34]
                        found = True
                        break
                if found:
                    break
            if found:
                break

    for name, pat in OTHER.items():
        if re.search(pat, page, re.I):
            out["routes"].append("search platform: " + name)
    if re.search(r'"@type"\s*:\s*"(Product|ItemList)"', page):
        out["routes"].append("JSON-LD")
    if "__NEXT_DATA__" in page:
        out["routes"].append("__NEXT_DATA__")
    if "__next_f.push" in page:
        out["routes"].append("app-router payload")
    if re.search(r'data-(pid|product-id|itemid)=', page):
        out["routes"].append("tiles with ids")
    if re.search(r'data-(product-info|tracking|tealium)=', page):
        out["routes"].append("tile carries JSON")
    if "/wp-json/wc/store" in page:
        out["routes"].append("WooCommerce")

    sib = set(re.findall(r'https?://([a-z0-9.-]{4,40}\.(?:com|co\.uk|jp|eu|it|fr|de|us))/[a-z]{2}(?:-[a-z]{2})?/',
                         page))
    stem = host.split(".")[-2] if "." in host else host
    sib = {s for s in sib if s != host and stem in s}
    if sib:
        out["siblings"] = sorted(sib)[:5]
    if not out["routes"]:
        out["routes"].append("served, nothing recognised")
    return out


targets = json.load(io.open(sys.argv[1], encoding="utf-8"))
workers = int(sys.argv[3]) if len(sys.argv) > 3 else 8
done = []
with cf.ThreadPoolExecutor(max_workers=workers) as pool:
    futs = {pool.submit(probe, t): t for t in targets}
    for i, f in enumerate(cf.as_completed(futs), 1):
        t = futs[f]
        try:
            r = f.result()
        except Exception as e:
            r = dict(t, routes=["prober failed: %s" % str(e)[:60]])
        done.append(r)
        print("%3d/%d %-34s %-9s %s" % (i, len(targets), r["host"], str(r.get("home")),
                                        "; ".join(r["routes"])[:118]), flush=True)
json.dump(done, io.open(sys.argv[2], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print("\nwritten to", sys.argv[2])
