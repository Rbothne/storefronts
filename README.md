# Listings

A personal page that pulls sale items and wishlist prices from many shops.

- `wishlist.txt`  one URL per line: product pages to follow and price-track.
- `stores.json`   one entry per store. `platform` is `shopify` (open feed), `page` (read price from the product page) or `link` (just a link card). `browse: true` scans the whole store for sale items. `currency` is only a fallback for Shopify stores: they localise prices by visitor country, so the build asks each one which currency it is quoting. `group` is the store's category; items from `clothing` stores, and from stores marked `"mixed": true`, are sorted one by one into tops, bottoms, skate gear and so on (rules in `build.py`), with `kind` as the fallback for items no rule matches. `"all": true` lists a store's whole in-stock range, not only its discounts (shown under Everything). `"fits": "women"` or `"men"` marks a boutique whose unlabelled items are all one or the other. `brand` overrides the brand name for a brand's own store when its feed leaves it blank, puts something else there or spells it several ways. `"collections": ["visvim"]` reads only those Shopify collections, for a big retailer that rate-limits a full catalog read but stocks a few brands worth tracking.
- `build.py`      fetches everything and writes `docs/data.json`. Standard library only.
- `docs/`         the page GitHub Pages serves. `docs/chat.js` is the shopping assistant; `docs/vendor/` holds the official Anthropic SDK bundled into one file (see the note there).
- `.github/workflows/update.yml`  runs `build.py` twice a day and commits the result.

On the page:

- **Followed** shows everything in `wishlist.txt` plus items you star (☆). Stars are saved in the browser; **Brands** shows everything else on sale from the brands of your followed items.
- **My sizes** hides items not in stock in the sizes picked under "Sizes & stores". `build.py` converts each store's labels (LG, 50 (Large), Japanese 3, EU 43, 27.5 cm…) to letter sizes, waist inches and US men's shoe sizes; the tables are at the top of its "sizes" section.
- **Your own categories**: ⋯ on any card puts the item, or its store, into categories you name yourself ("shirts", "blue", "gift shops"…). The two "my categories" menus in the filter bar then filter by them. The same menu marks a store or brand "not interested", which hides it everywhere except the "Hidden: not interested" view.
- **High-volume stores**: a switch in "Sizes & categories" hides stores that list far more items than the rest. A store is flagged when its count of qualifying items (before the cap below) is above Q3 + 1.5 × (Q3 − Q1) of items-per-store, the box-plot rule for outliers. Stores you put in a category are never hidden.
- **Men's / women's** uses the labels stores put in titles, product types and tags; unlabelled items count as unisex.
- **Best deals** sorts by discount plus bonuses for followed brands, your size, a lowest-tracked price and new arrivals (weights in `score()` in `docs/index.html`).
- **Price history**: `data/state.json` keeps a point each time any item's price changes by 3% or more (smaller moves are exchange-rate drift at stores that quote converted prices; items unseen for 90 days are dropped). Cards show a small chart once an item has more than one price, with today's price as a dot: green at its lowest tracked price, red at its highest, yellow in between, and two sort orders use it: **Biggest price drop** (furthest below its highest tracked price) and **At lowest price so far**.
- Stars, categories, sizes and hidden stores and brands live in the browser; the Backup box in the panel copies them to another device.
- **Ask the assistant** opens a chat with Claude that searches the site's items, knows your sizes, follows and categories, shows item cards in the conversation and remembers preferences you tell it. It needs your own Anthropic API key, pasted into the panel: the key is kept in that browser only (separate from the Backup box) and is sent only to Anthropic, so it is never part of the site or the repository and nobody else can use it. The Web switch lets it search the web and open links; the site's own items always come first.
- Prices in other currencies also show an approximate US dollar value, using rates fetched from open.er-api.com at build time.

Run locally:

    py build.py
    py -m http.server 8000 -d docs     # then open http://localhost:8000

Settings in `stores.json`: `min_discount` (percent off needed to appear in the sale tab), `max_pages` (250 products per page, per store), `max_items_per_store` (optional cap, keeps each store's deepest discounts; unset means no cap), `workers` (stores fetched at the same time), `delay_seconds` (pause between requests to one store).
