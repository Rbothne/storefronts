#!/usr/bin/env python3
"""The stores no plain request reaches, read in a browser on a schedule of their own.

Most shops the build cannot read as a feed still serve their listing pages to a plain request, and listings.py
reads those directly. A handful do not: Farfetch, Net-a-Porter, LuisaViaRoma and Cettire answer HTTP 403 (or a
Cloudflare challenge) to anything their edge does not recognise as a person's browser, sometimes to every address
on the host including the home page and the sitemap. For those, a store in stores.json says

    "read": "harvest"

and this tool opens its pages in a real Chromium, lets them finish building themselves, and runs the page through
the very same extractors listings.py uses ("via": "tiles", "links", "ld", "json" and the rest, with every setting
those take). What it finds is written to data/harvest/<host>.json, and the hourly build reads that file from disk
instead of going near the store -- so the build stays standard-library-only and fast, and the browser runs once a
day in a workflow of its own (.github/workflows/harvest.yml).

    py tools/harvest.py                      every harvest store, headless
    py tools/harvest.py --only www.cettire.com --show     one of them, with the window open to watch
    py tools/harvest.py --channel msedge     a different browser, where Chromium is turned away

Needs Playwright and a browser:  py -m pip install playwright && py -m playwright install chromium

**A hosted runner is the worst possible vantage point for this.** These edges judge the client's address as much
as its fingerprint: a GitHub-hosted runner is a datacenter address and is usually refused before the browser is
even looked at. The tool reports, per store and per page, exactly what it was answered, so a run says plainly
whether the machine it ran on can see the store at all; point the workflow at a self-hosted runner on an ordinary
connection and the same code gets through. A store the browser cannot reach keeps its last harvest (the build
says how old it is) rather than emptying.
"""
import argparse
import io
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import listings                                            # the extractors, shared with the build  # noqa: E402

OUT = os.path.join(ROOT, "data", "harvest")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36")
# A page that says it is being driven is turned away by the same rules that refuse a plain request.
STEALTH = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
window.chrome = window.chrome || {runtime:{}};
const q = navigator.permissions && navigator.permissions.query;
if (q) navigator.permissions.query = (p) => p && p.name === 'notifications'
  ? Promise.resolve({state: Notification.permission}) : q(p);
"""
ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage",
        "--window-size=1440,900"]
BLOCKED = re.compile(r"(?i)access denied|attention required|just a moment|unusual activity|are you a (human|robot)"
                     r"|bot detect|request blocked|pardon our interruption")


def harvest_store(ctx, store, pages_cap, settle=6000):
    """Open each of a store's listing pages in the browser and pull the products out of what it renders.
    Returns (items, notes), with a note per page saying what came back."""
    host = store["host"]
    origin = "https://%s" % store.get("feed", host)
    paging, size = store.get("paging", ""), store.get("size", 0)
    page = ctx.new_page()
    items, notes, seen = [], [], set()
    try:
        for entry in store["pages"]:
            start, kind = (entry, "") if isinstance(entry, str) else entry
            url = urljoin(origin + "/", start)
            for n in range(1, pages_cap + 1):
                try:
                    answer = page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                    status = answer.status if answer else 0
                except Exception as e:
                    notes.append("%s: %s" % (start, str(e)[:60]))
                    break
                page.wait_for_timeout(settle)
                for _ in range(store.get("scrolls", 3)):                   # a grid that loads as it comes into view
                    page.mouse.wheel(0, 3000)
                    page.wait_for_timeout(1500)
                html = page.content()
                if status != 200 or BLOCKED.search(page.title() or "") or BLOCKED.search(html[:4000]):
                    notes.append("%s: HTTP %s %s" % (start, status, (page.title() or "")[:40]))
                    break
                fresh = 0
                for d in listings.products(html, url, store):
                    key = listings.canon(d["url"])
                    if d["price"] and key not in seen:
                        seen.add(key)
                        d["ptype"] = d["ptype"] or kind
                        items.append(d)
                        fresh += 1
                notes.append("%s page %d: %d products" % (start, n, fresh))
                if not fresh or not paging:
                    break
                url = listings.page_address(urljoin(origin + "/", start), paging, n + 1, size)
    finally:
        page.close()
    return items, notes


def write(host, items, notes, ok):
    """Keep what a browser found. A run that was refused leaves the last harvest alone."""
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, host + ".json")
    if not ok or not items:
        return False, path
    io.open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(
        {"host": host, "at": int(time.time()),
         "fetched": time.strftime("%Y-%m-%dT%H:%M", time.gmtime()),
         "notes": notes, "items": items}, ensure_ascii=False, indent=1))
    return True, path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--only", help="one store's host instead of all of them")
    ap.add_argument("--show", action="store_true", help="keep the browser window open, to watch it work")
    ap.add_argument("--channel", default="chromium", help="chromium (the default), chrome or msedge")
    ap.add_argument("--pages", type=int, help="cap the pages per listing, whatever the store says")
    args = ap.parse_args()

    doc = json.load(io.open(os.path.join(ROOT, "stores.json"), encoding="utf-8"))
    stores = [s for s in doc["stores"] if s.get("read") == "harvest"]
    if args.only:
        stores = [s for s in stores if s["host"] == args.only]
    if not stores:
        print("no stores are read by harvesting")
        return 0

    from playwright.sync_api import sync_playwright          # only this tool needs it
    refused = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=not args.show, args=ARGS)
        for store in stores:
            host = store["host"]
            cap = args.pages or store.get("max_pages", doc["settings"].get("max_pages", 8))
            ctx = browser.new_context(user_agent=UA, locale="en-US", timezone_id="America/New_York",
                                      viewport={"width": 1440, "height": 900},
                                      extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
            ctx.add_init_script(STEALTH)
            started = time.time()
            try:
                # the home page first: an edge that watches a session wants one before a listing
                if store.get("warm_up"):
                    warm = ctx.new_page()
                    try:
                        answer = warm.goto(store["warm_up"], wait_until="domcontentloaded", timeout=90_000)
                        print("%-22s warm-up HTTP %s" % (host, answer.status if answer else "?"))
                        warm.wait_for_timeout(4000)
                    except Exception as e:
                        print("%-22s warm-up failed: %s" % (host, str(e)[:60]))
                    finally:
                        warm.close()
                items, notes = harvest_store(ctx, store, cap)
            except Exception as e:
                items, notes = [], ["the browser gave up: %s" % str(e)[:80]]
            finally:
                ctx.close()
            ok = bool(items)
            saved, path = write(host, items, notes, ok)
            print("%-22s %5d products in %3.0fs -- %s" % (
                host, len(items), time.time() - started,
                "written to " + os.path.relpath(path, ROOT) if saved else "REFUSED, last harvest kept"))
            for note in notes[:12]:
                print("      " + note)
            if not ok:
                refused.append(host)
        browser.close()

    if refused:
        print("\nthis machine could not see: " + ", ".join(refused))
        print("these edges judge the address as much as the browser; a hosted runner is a datacenter address.")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
