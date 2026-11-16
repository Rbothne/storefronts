# Listings

A personal page that pulls sale items and wishlist prices from many shops.

- `wishlist.txt`  one URL per line. Product pages are tracked; Shopify collection pages are shown in full.
- `stores.json`   one entry per store. `platform` is `shopify` (open feed), `page` (read price from the product page) or `link` (just a link card). `browse: true` scans the whole store for sale items. `collections` lists collections to show in full. `group` is the store's category; items from `clothing` stores, and from stores marked `"mixed": true`, are sorted one by one into tops, bottoms, skate gear and so on (rules in `build.py`), with `kind` as the fallback for items no rule matches.
- `build.py`      fetches everything and writes `docs/data.json`. Standard library only.
- `docs/`         the page GitHub Pages serves.
- `.github/workflows/update.yml`  runs `build.py` twice a day and commits the result.

On the page:

- **Followed** shows everything in `wishlist.txt` plus items you star (☆). Stars are saved in the browser; **Brands** shows everything else on sale from the brands of your followed items.
- **My sizes** hides items not in stock in the sizes picked under "Sizes & stores". `build.py` converts each store's labels (LG, 50 (Large), Japanese 3, EU 43, 27.5 cm…) to letter sizes, waist inches and US men's shoe sizes; the tables are at the top of its "sizes" section.
- **Store levels** (Priority 1, Priority 2, Gifts, Not interested) are set in the same panel. A default can also be given in `stores.json` as `"tier": "1"`, `"2"`, `"gift"` or `"no"`.
- **✕ on a card** hides everything from that brand. Hidden brands and "not interested" stores only appear under "Hidden: not interested" in the store-level menu.
- **Men's / women's** uses the labels stores put in titles, product types and tags; unlabelled items count as unisex.
- **Best deals** sorts by discount plus bonuses for followed brands, rated stores, your size, a lowest-tracked price and new arrivals (weights in `score()` in `docs/index.html`).
- **Price history**: `data/state.json` keeps a point each time any item's price changes (items unseen for 90 days are dropped). Cards show a small chart once an item has more than one price.
- Stars, sizes, levels and hidden brands live in the browser; the Backup box in the panel copies them to another device.
- Prices in other currencies also show an approximate US dollar value, using rates fetched from open.er-api.com at build time.

Run locally:

    py build.py
    py -m http.server 8000 -d docs     # then open http://localhost:8000

Settings in `stores.json`: `min_discount` (percent off needed to appear in the sale tab), `max_pages` (250 products per page, per store), `delay_seconds` (pause between requests).
