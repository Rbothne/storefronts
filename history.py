#!/usr/bin/env python3
"""What the site remembers about every product, and the numbers worked out from it.

data/state/<store>.json   one file per store: every product seen in its catalogue, with a point of
                          [time, price, percent off, in stock] for each real change
docs/metrics.json         per-store indicators for the "Site metrics" page (docs/metrics.html)

build.py calls this on every run.  Run  python3 history.py  to recompute docs/metrics.json from the files on disk.

A store's file looks like this (times are UTC, to the minute):
    {"since": first read, "fetched": last clean read, "partial": true if only part of its catalogue is read,
     "cur": currency of the prices, "reads": 12, "fails": 1, "error": the latest failure (absent after a clean read),
     "log": [[day, products, in stock, on sale, typical percent off], ...]   one line per day
     "products": {id: {"f": first seen, "h": [[time, price, percent off, in stock], ...], "x": noticed gone}}}
The percent off is the store's own (its "was" price against its price); 0 when it shows none.
"""
import collections
import datetime
import functools
import json
import os
import re
import statistics
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "data", "state")
METRICS_PATH = os.path.join(ROOT, "docs", "metrics.json")

# Stores that quote converted prices drift a little with exchange rates every day.
# A move smaller than this share of the price is treated as the same price, not a change.
NOISE = 0.03
KEEP_POINTS = 30          # per product
KEEP_DAYS = 90            # a product gone this long is forgotten
HORIZONS = (1, 3, 7, 14, 30)


# ---------------------------------------------------------------- files

def load_state():
    """host -> that store's tracking file."""
    state = {}
    if os.path.isdir(STATE_DIR):
        for name in sorted(os.listdir(STATE_DIR)):
            if name.endswith(".json"):
                with open(os.path.join(STATE_DIR, name), encoding="utf-8") as f:
                    st = json.load(f)
                state[st["host"]] = st
    if not state:
        try:
            with open(os.path.join(ROOT, "data", "state.json"), encoding="utf-8") as f:
                state = migrate(json.load(f))
        except FileNotFoundError:
            pass
    return state


def save_state(state):
    """Write one file per store (one product per line) and remove the files of stores no longer tracked."""
    os.makedirs(STATE_DIR, exist_ok=True)
    cutoff = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    keep = set()
    for host, st in state.items():
        name = re.sub(r"[^\w.-]", "_", host) + ".json"
        keep.add(name)
        head = {"host": host, **{k: v for k, v in st.items() if k not in ("host", "products")}}
        lines = [f"{json.dumps(k)}:{json.dumps(v, separators=(',', ':'))}"
                 for k, v in sorted(st["products"].items()) if v.get("x", "9") >= cutoff]
        with open(os.path.join(STATE_DIR, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(head, separators=(",", ":"))[:-1] + ',\n"products":{\n' + ",\n".join(lines) + "\n}}\n")
    for name in os.listdir(STATE_DIR):
        if name.endswith(".json") and name not in keep:
            os.remove(os.path.join(STATE_DIR, name))


def migrate(old):
    """One-time: the single state.json kept before November 2026 (listed items only; a day and a price per point,
    no discount or stock). Those points get an unknown discount (None), filled in when the product is next read."""
    state, last = {}, {}
    for pid, r in old.get("prices", {}).items():
        if not r.get("h"):
            continue
        host = urlparse(pid).netloc if pid.startswith("http") else pid.split("/")[0]
        st = state.setdefault(host, {"products": {}})
        st["products"][pid] = {"f": r["first"], "h": [[day, price, None, 1] for day, price in r["h"]]}
        last[pid] = r.get("last", r["first"])
    for host in old.get("fetched", {}):                  # stores read before that had nothing listed
        state.setdefault(host, {"products": {}})
    for host, st in state.items():
        st["fetched"] = (old.get("fetched", {}).get(host) or "")[:16]
        st["since"] = min((rec["f"] for rec in st["products"].values()), default=st["fetched"])
        latest = max((last[pid] for pid in st["products"]), default="")
        for pid, rec in st["products"].items():
            if last[pid] < latest:
                rec["x"] = last[pid]
    return state


# ---------------------------------------------------------------- recording

def record(rec, now, price, off, stock):
    """Add a reading to a product's history. A new point only when something really changed: the price level,
    the discount (3 points or more) or whether it is in stock."""
    h = rec["h"]
    gone = rec.pop("x", None)
    if gone and not stock:
        now = gone                           # back in the catalogue but sold out: unavailable since it went missing
    if h:
        last = h[-1]
        same_price = abs(price - last[1]) < NOISE * last[1]
        if last[2] is None and same_price:
            last[2] = off                    # a point from the old format: its discount was not kept then
        if same_price and last[3] == stock and last[2] is not None and abs(off - last[2]) < 3:
            last[1], last[2] = price, off    # same state: keep it current, add no point
            return
    h.append([now, price, off, stock])
    del h[:-KEEP_POINTS]


def track(st, readings, now, min_off, cur, errors, whole):
    """Fold one read of a store into its tracking file.

    readings: id -> (price, percent off, in stock) for every product the read returned.
    errors:   what went wrong, if anything. Products are only marked as gone after a clean read.
    whole:    False when the read covers part of the catalogue (some collections, or the page cap was hit),
              so a product missing from it may simply be outside the part that was read.
    """
    products = st.setdefault("products", {})
    st.setdefault("since", now)
    if st.get("cur") not in (None, cur):     # prices now come in another currency: the old ones can't be compared
        for rec in products.values():
            rec["h"] = []
    st["cur"] = cur
    for pid, (price, off, avail) in readings.items():
        if price:
            record(products.setdefault(pid, {"f": now, "h": []}), now, price, off, 0 if avail is False else 1)
    st["reads"] = st.get("reads", 0) + 1
    if errors:
        st["fails"], st["error"] = st.get("fails", 0) + 1, errors[0][:200]
        return
    st.pop("error", None)
    st["fetched"], st["partial"] = now, not whole
    for pid, rec in products.items():
        if pid not in readings and "x" not in rec:
            rec["x"] = now
    # a line a day for the store's long-run picture; product histories are trimmed, this is not
    live = [off for price, off, avail in readings.values() if price and avail is not False]
    sale = [off for off in live if off >= min_off]
    log = st.setdefault("log", [])
    if log and log[-1][0] == now[:10]:
        log.pop()
    log.append([now[:10], len(readings), len(live), len(sale), round(statistics.median(sale)) if sale else 0])
    del log[:-400]


# ---------------------------------------------------------------- reading a history

def price_points(h):
    """[day, price] for each change of price level (the page draws these)."""
    out = []
    for t, price, *_ in h:
        if out and abs(price - out[-1][1]) < NOISE * out[-1][1]:
            out[-1][1] = price
        else:
            out.append([t[:10], price])
    return out


def on_sale(point, min_off):
    """In stock with at least the minimum discount (points from the old format were all listed items)."""
    return bool(point[3]) and (point[2] is None or point[2] >= min_off)


def since(h, min_off):
    """When the product's current stretch began: on sale if it is now, otherwise in stock."""
    test = (lambda p: on_sale(p, min_off)) if on_sale(h[-1], min_off) else (lambda p: bool(p[3]))
    t = h[-1][0]
    for p in reversed(h):
        if not test(p):
            break
        t = p[0]
    return t


def last_move(h):
    """(time, percent) of the latest change of price level, or None."""
    for a, b in zip(h[-2::-1], h[::-1]):
        if abs(b[1] - a[1]) >= NOISE * a[1]:
            return b[0], round((b[1] / a[1] - 1) * 100)
    return None


def storewide(st):
    """Times a store moved many prices by the same percentage at once, discounts unchanged: a repricing (or a
    currency swing), not a sale. [{"t": time, "pct": percent, "n": products}]"""
    moves = collections.defaultdict(collections.Counter)
    for rec in st["products"].values():
        for a, b in zip(rec["h"], rec["h"][1:]):
            # A point from the old format (a day, no time) had no discount recorded, but only items whose sale
            # held were kept then, so a move that ends at one counts. A move from an old point to a new one
            # could be a sale ending, so it doesn't.
            same_discount = len(b[0]) == 10 or (a[2] is not None and b[2] is not None and abs(a[2] - b[2]) < 3)
            if abs(b[1] - a[1]) >= NOISE * a[1] and same_discount:
                moves[b[0]][round((b[1] / a[1] - 1) * 100)] += 1
    floor, out = max(20, 0.05 * len(st["products"])), []
    for t, c in sorted(moves.items()):
        pct = c.most_common(1)[0][0]
        n = c[pct - 1] + c[pct] + c[pct + 1]
        if n >= floor and n >= 0.5 * sum(c.values()):
            out.append({"t": t, "pct": pct, "n": n})
    return out


def in_storewide(h, events):
    """The percentage, if the product's latest price change was part of a store-wide one."""
    move = last_move(h)
    for e in events:
        if move and move[0] == e["t"] and abs(move[1] - e["pct"]) <= 1:
            return e["pct"]
    return None


# ---------------------------------------------------------------- deals

@functools.lru_cache(maxsize=None)
def when(t):
    return datetime.datetime.fromisoformat(t)


def days(a, b):
    return (when(b) - when(a)).total_seconds() / 86400


def deals(rec, st, min_off):
    """The stretches a product spent on sale and in stock, one dict each:
    start, off (at the start), known (False if it was already on sale when first seen, so the true start is
    unknown), end + how ("sold out", "sale ended", "taken down"; no "how" when it merely left a partial read),
    cut (when the price first fell 5% or more below the price the deal began at), marked_up (True/False where it can be told: the
    "was" price is over 5% above every price tracked before the sale)."""
    out, cur, h = [], None, rec["h"]
    for n, p in enumerate(h):
        if on_sale(p, min_off):
            if cur is None:
                cur, price = {"start": p[0], "off": p[2], "known": n > 0 or rec["f"] > st["since"]}, p[1]
                if n and p[2] is not None and p[2] < 100:
                    cur["marked_up"] = p[1] / (1 - p[2] / 100) > 1.05 * max(q[1] for q in h[:n])
            elif cur["off"] is None:
                cur["off"] = p[2]
            elif p[1] <= 0.95 * price:           # the price itself fell: a deeper discount on a raised "was" price is not a cut
                cur.setdefault("cut", p[0])
        elif cur:
            cur["end"], cur["how"] = p[0], "sale ended" if p[3] else "sold out"
            out.append(cur)
            cur = None
    if cur:
        if "x" in rec:
            cur["end"] = rec["x"]
            if not st.get("partial"):
                cur["how"] = "taken down"
        out.append(cur)
    return [d for d in out if d["off"] is not None]


def survival(spans):
    """Kaplan-Meier estimate from spans of (days observed, ended?).

    Deals still running count for as long as they have been watched, so young deals don't make lives look
    short. Returns ({days: share still running}, days until half are gone); None where no deal has been
    watched that long.
    """
    spans = sorted(spans, key=lambda s: (s[0], not s[1]))
    left, half, share, alive, n = {}, None, 1.0, len(spans), 0
    for day in HORIZONS:
        while n < len(spans) and spans[n][0] <= day:
            if spans[n][1]:
                share *= 1 - 1 / alive
                if half is None and share <= 0.5:
                    half = round(spans[n][0], 1)
            alive -= 1
            n += 1
        left[day] = round(share, 3) if spans and spans[-1][0] >= day else None
    return left, half


def summarise(found):
    """Indicators for a list of (deal, when its store was last read)."""
    known = [(d, t) for d, t in found if d["known"]]
    spans = [(max(0.0, days(d["start"], d.get("end") or t)), "how" in d) for d, t in known]
    left, half = survival(spans)
    how = collections.Counter(d["how"] for d, _ in known if "how" in d)
    cuts = [days(d["start"], d["cut"]) for d, _ in known if "cut" in d]
    checked = [d["marked_up"] for d, _ in found if "marked_up" in d]
    return {"deals": len(known), "running": sum(1 for d, _ in known if "end" not in d),
            "sold_out": how["sold out"], "sale_ended": how["sale ended"], "taken_down": how["taken down"],
            "left": left, "half": half,
            "cut": len(cuts), "cut_days": round(statistics.median(cuts), 1) if cuts else None,
            "marked_up": sum(checked), "checked": len(checked)}


def bands(min_off):
    edges = [min_off] + [e for e in (40, 60) if e > min_off] + [101]
    return [(a, b, f"{a}–{b - 1}%" if b <= 100 else f"{a}%+") for a, b in zip(edges, edges[1:])]


def by_band(found, min_off):
    return {label: summarise([(d, t) for d, t in found if a <= d["off"] < b]) for a, b, label in bands(min_off)}


def metrics(state, names, min_off, now):
    """Everything the metrics page shows."""
    stores, everything, now_off, events = [], [], [], []
    for host, st in sorted(state.items()):
        fetched = st.get("fetched") or st["since"]
        found = [(d, fetched) for rec in st["products"].values() for d in deals(rec, st, min_off)]
        everything += found
        live = [rec["h"][-1] for rec in st["products"].values() if rec["h"] and "x" not in rec and rec["h"][-1][3]]
        sale = [p[2] for p in live if p[2] is not None and p[2] >= min_off]
        now_off += sale
        wide = storewide(st)
        events += [{"host": host, "name": names.get(host, host), "of": len(st["products"]), **e} for e in wide]
        starts = [when(d["start"]).weekday() for d, _ in found if d["known"] and len(d["start"]) > 10]
        stores.append({
            "host": host, "name": names.get(host, host), "since": st["since"], "fetched": st.get("fetched"),
            "partial": bool(st.get("partial")), "reads": st.get("reads", 0), "fails": st.get("fails", 0),
            "error": st.get("error"),
            "products": sum(1 for rec in st["products"].values() if "x" not in rec),
            "stock": len(live), "sale": len(sale), "typical": round(statistics.median(sale)) if sale else None,
            "log": st.get("log", [])[-90:],
            "weekday": [starts.count(n) for n in range(7)],
            "storewide": wide,
            **summarise(found), "bands": by_band(found, min_off)})
    return {"generated": now, "min_discount": min_off, "horizons": HORIZONS,
            "since": min((s["since"] for s in stores), default=now),
            "products": sum(s["products"] for s in stores), "stock": sum(s["stock"] for s in stores),
            "sale": len(now_off), "typical": round(statistics.median(now_off)) if now_off else None,
            **summarise(everything), "bands": by_band(everything, min_off),
            "storewide": sorted(events, key=lambda e: e["t"], reverse=True)[:100],
            "stores": stores}


def write_metrics(state, cfg, now):
    names = {s["host"]: s.get("name", s["host"]) for s in cfg["stores"]}
    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics(state, names, cfg.get("settings", {}).get("min_discount", 20), now), f,
                  ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    with open(os.path.join(ROOT, "stores.json"), encoding="utf-8") as f:
        config = json.load(f)
    write_metrics(load_state(), config, datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"))
    print("wrote", METRICS_PATH, file=sys.stderr)
