#!/usr/bin/env python3
"""The search keys of the stores read through Algolia, checked and fetched again when one stops working.

Some shops turn a plain request away at the edge (Yoox answers 403 to anything that is not a browser) but build
their listing pages in the visitor's browser out of an Algolia search, with the application id, the index and a
public search key handed to the page. listings.py reads those stores straight from the index with an ordinary
GET, so the build needs no browser at all -- but a shop can rotate its search key, and then the store stops
reading. This is the tool for that:

    py tools/algolia_keys.py                 say whether each store's stored key still answers (no browser)
    py tools/algolia_keys.py --capture       open each store's own page in a browser and read its key again
    py tools/algolia_keys.py --capture --write   ... and put what it finds into stores.json

The check is standard library only and runs anywhere. The capture needs Playwright and a Chromium:

    py -m pip install playwright && py -m playwright install chromium

A store opts in with "read": "algolia" and a "key_page" -- a page of its own that runs the search. See the
Algolia settings in README.md, and .github/workflows/algolia-keys.yml, which runs the check weekly and only
reaches for the browser when a key has died.
"""
import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, quote, urlencode, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORES = os.path.join(ROOT, "stores.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def algolia_stores(doc):
    return [s for s in doc["stores"] if s.get("read") == "algolia"]


def ask(cfg, hits=1):
    """One search against a store's index with the key as stored: (number of products, what went wrong)."""
    url = "https://%s-dsn.algolia.net/1/indexes/%s?%s" % (
        cfg["app"].lower(), quote(cfg["index"]),
        urlencode({"x-algolia-api-key": cfg["key"], "x-algolia-application-id": cfg["app"],
                   "hitsPerPage": hits, "page": 0}))
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(json.loads(r.read()).get("nbHits") or 0), ""
    except urllib.error.HTTPError as e:
        return 0, "HTTP %s %s" % (e.code, e.read()[:120].decode("utf-8", "replace"))
    except Exception as e:                                     # DNS, TLS, timeout
        return 0, str(e)[:120]


def check(doc):
    """Print a line per store and return the hosts whose key no longer works."""
    broken = []
    for s in algolia_stores(doc):
        n, why = ask(s["algolia"])
        print("%-22s %-34s %s" % (s["host"], s["algolia"]["index"],
                                  ("%d products" % n) if n else ("NO -- " + why)))
        if not n:
            broken.append(s["host"])
    return broken


def capture(store, keep_open=False):
    """The application id, index and search key a store's own page hands its scripts, read from the search its
    browser makes. Returns None when the page makes no Algolia search."""
    from playwright.sync_api import sync_playwright          # only the capture needs it

    found = []

    def note(req):
        if "/1/indexes/" in req.url and "algolia" in urlparse(req.url).netloc:
            found.append((req.url, req.post_data))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium", headless=not keep_open, args=[
            "--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1440, "height": 900})
        # a page that says it is being driven is turned away by the same edge rules that refuse a plain request
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()
        page.on("request", note)
        try:
            page.goto(store["key_page"], wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(3000)
            page.mouse.wheel(0, 2500)                        # the grid searches as it comes into view
            page.wait_for_timeout(7000)
        finally:
            browser.close()

    for url, body in found:
        query = dict(parse_qsl(urlparse(url).query))
        app = query.get("x-algolia-application-id")
        key = query.get("x-algolia-api-key")
        index = ""
        try:
            index = (json.loads(body or "{}").get("requests") or [{}])[0].get("indexName", "")
        except ValueError:
            pass
        if not index:
            m = re.search(r"/1/indexes/([^/?]+)", url)        # a search asked for by address, not in a body
            index = m.group(1) if m and m.group(1) != "*" else ""
        if app and key and index:
            return {"app": app, "key": key, "index": index}
    return None


def write_back(found):
    """Put the captured settings into stores.json, one store to a line as the file keeps them."""
    lines = io.open(STORES, encoding="utf-8").read().split("\n")
    out, changed = [], []
    for line in lines:
        body = line.strip().rstrip(",")
        if body.startswith('{"host"'):
            store = json.loads(body)
            cfg = found.get(store["host"])
            if cfg and store.get("algolia") != cfg:
                store["algolia"] = cfg
                changed.append(store["host"])
            comma = "," if line.rstrip().endswith(",") else ""
            out.append("    " + json.dumps(store, ensure_ascii=False, separators=(", ", ": ")) + comma)
        else:
            out.append(line)
    if changed:
        io.open(STORES, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    return changed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--capture", action="store_true", help="read each store's key again from its own page")
    ap.add_argument("--write", action="store_true", help="put what the capture found into stores.json")
    ap.add_argument("--only", help="one store's host, instead of all of them")
    ap.add_argument("--show", action="store_true", help="capture with the browser window open, to watch it")
    args = ap.parse_args()

    doc = json.load(io.open(STORES, encoding="utf-8"))
    stores = algolia_stores(doc)
    if args.only:
        stores = [s for s in stores if s["host"] == args.only]
        if not stores:
            sys.exit("no store read through Algolia with that host")
    if not stores:
        print("no stores are read through Algolia")
        return 0

    if not args.capture:
        broken = check({"stores": stores})
        if broken:
            print("\nkeys to fetch again: " + ", ".join(broken))
            print("    py tools/algolia_keys.py --capture --write")
        return 1 if broken else 0

    found, failed = {}, []
    for s in stores:
        if not s.get("key_page"):
            print("%-22s no \"key_page\" in stores.json to read the key from" % s["host"])
            failed.append(s["host"])
            continue
        try:
            cfg = capture(s, keep_open=args.show)
        except Exception as e:
            print("%-22s browser could not read it: %s" % (s["host"], str(e)[:90]))
            failed.append(s["host"])
            continue
        if not cfg:
            print("%-22s its page made no Algolia search" % s["host"])
            failed.append(s["host"])
            continue
        n, why = ask(cfg)
        same = " (unchanged)" if cfg == s.get("algolia") else " (NEW)"
        print("%-22s %s %s %s -- %s%s" % (s["host"], cfg["app"], cfg["index"], cfg["key"],
                                          ("%d products" % n) if n else why, same))
        if n:
            found[s["host"]] = cfg
        else:
            failed.append(s["host"])

    if args.write and found:
        changed = write_back(found)
        print("\nstores.json: " + (", ".join(changed) + " updated" if changed else "nothing to change"))
    elif found and not args.write:
        print("\nnothing written; add --write to put these into stores.json")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
