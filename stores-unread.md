# Stores this site cannot read yet

Every store the build reads is in `stores.json`; how each kind is read is in `README.md` and at the top of
`listings.py`. **This file is for the other ones** — what each store actually serves, what has already been
tried on it, and which wall it hit. Read it before probing a store again: most of the stores below have been
probed two or three times over, and a verdict here is a probe you do not need to repeat.

Keep it current. A store that gets read moves out of the tables; a store that turns out to be readable gets its
finding corrected rather than a second row underneath.

## No store in here is finished

A verdict below records **a route that was tried**, never a store that is beyond reading. Both of the hardest
stores on this site were written off twice before they were read: Yoox had three sessions of `guarded` against
its name and now hands over 11,973 products with sizes and both prices in twelve plain requests, because
somebody finally looked at which host its grid was built from instead of at its front door again. Cettire was
`guarded` too, and its rendered grid turned out to read with eight words of configuration.

So:

- **A closed route is not a closed store.** Work down every route in the list below before you write anything
  in the `no route` direction, and name the ones you tried so the next person starts past them.
- **Change route rather than push harder.** A route that answered 403 twice will answer it the third time;
  the catalogue is usually sitting somewhere else, openly served, with better fields than the page would have
  given you. Yoox's search index carries sizes and two prices; its pages carry nothing at all.
- **Asking harder is the one thing that loses a store for good.** Every lasting refusal recorded here was
  earned by a single greedy request, and it cost the store. Talbots reads cleanly at `sz=18`; `sz=72` ended it
  for the session. Being relentless means trying more *routes*, not more requests down one of them.
- **Come back.** Shops get rebuilt, shut shops reopen, keys rotate, platforms get replaced. Children of the
  Discordance was probed on a day its shop was empty. The Tokyo renamed its tile classes and quietly stopped
  giving up brand names. A verdict with a date on it is a starting point, not an answer.
- **Nothing is left at "it refused me".** If every route in a row is spent, the row should say what would open
  it — a header that was never captured, a page nobody has managed to render, a count nobody has taken. The
  "Still open" list at the bottom is written that way on purpose.

## Looking for a way to read a store

In the order that has actually paid off, cheapest first — but go on down the list, not back up it. Nine of
these routes have each read at least one store that an earlier route had refused.

1. **A Shopify feed.** `/products.json?limit=250&page=N` on the host, then on the sibling host a headless front
   end hides behind — look in the page for `cdn.shopify.com`, `Shopify.shop` or a `*.myshopify.com` name. Eight
   stores on the site are read through a `feed` host their own address never mentions.
2. **What the page already carries.** JSON-LD (`via: ld`), the data it hands its own scripts (`via: json` —
   `__NEXT_DATA__`, an Apollo cache, a Next.js app-router payload), its tiles (`via: tiles` — `data-pid`, plus
   `data-product-price` / `-listprice` / `-availability` attributes, or a whole product as JSON in one attribute:
   `tile_json`), or the product links with the prices printed around them (`via: links`). Forty-odd stores are
   read this way, and testing an extractor against a saved page costs nothing.
3. **A third-party search host whose key the page prints.** *This is the one worth reaching for early.* A shop
   that refuses every request at its own edge often has its grid built in the visitor's browser by Algolia,
   Constructor, Bloomreach, Searchspring or Klevu — on **their** host, which has no interest in who is asking
   and answers a plain `GET`. Grep the page for `algoliaApplicationId`, `algolia_app_id`, `apiKey` or
   `-dsn.algolia.net`, or watch one page load in a browser and log its requests. Yoox — which answers 403 to
   everything, its own home page and sitemap included — gives up its whole sale, 11,973 products with sizes and
   both prices, in twelve plain requests this way. Miu Miu prints its key in its own HTML.
4. **The store's own API on its own host.** Usually behind the same guard as its pages, so it rarely helps, and
   a request replayed from outside the browser can be refused even with the body copied verbatim (Cettire's
   `search.cettire.com` answers 400 to a byte-identical body, and 415 to the wrong content type).
5. **A browser harvest** (`"read": "harvest"`, `tools/harvest.py`). Only for a store that serves a usable grid
   to a browser and nothing to a plain request. It runs on its own daily schedule, never inside the build.
6. **Sitemaps.** The product sitemap gives a count and one request per product, which is workable only for a few
   hundred — note the count here so nobody counts it again. The *collection* sitemap is the more useful half:
   it hands you the category paths a `listing` store needs, which is otherwise the slowest part of adding one.
7. **The same shop somewhere else.** A shop is often several storefronts, and they are not all built the same:
   another country or language site (`/eu/`, `/en-gb/`, a national domain), the mobile host, an older platform
   left running on a subdomain, or a regional franchise. Vilebrequin serves `/us/`, `/eu/` and more off one
   catalogue; Manastash's reading comes from `mix.tokyo`, Visvim's from `shop.visvim.com`, BYLT's from a
   `myshopify` host — none of which is the address the store is listed under.
8. **The same catalogue through somebody else.** A brand that cannot be read directly is often carried, with
   prices, by a retailer the site already reads — and a retailer that cannot be read is sometimes the only
   place a brand's own shop is readable. Worth checking before a store is left out entirely.
9. **A feed the shop publishes on purpose.** A Google Shopping or Meta catalogue file linked from the site.
   (Affiliate-network feeds need an account; don't sign up for one.)

## What keeps going wrong

- **`Accept: application/json` on a request for a page is a bot signature.** No browser sends it. That header
  alone earned a 403 from Talbots where a browser-shaped `Accept` got 200. `build.py` still asks for JSON first
  because that is right for the feeds it mostly reads — so if a store refuses a page but serves its robots.txt,
  try the header before concluding anything. Check the body it buys, not the status: the 200 Talbots then gave
  was a stub.
- **A 403 is usually about the address, not the request.** Farfetch refuses a real signed Edge on its home
  page; LuisaViaRoma's Cloudflare challenge cleared once in three seconds and then hardened to a flat refusal;
  Cettire served its grid and then blocked the host; Talbots blocked every HTML path after one greedy read. No
  header, no TLS impersonation and no amount of stealth moves reputation. **A datacenter address is the worst
  place to try from**, which is why `tools/harvest.py` takes a `HARVEST_RUNNER`.
- **`robots.txt` is not the authority on what you will be served.** Yoox's robots.txt explicitly `Allow`s
  `/*/women/sale/shoponline$` and the edge refuses it anyway. Honour the file — the build does — but do not read
  permission in it as a promise.
- **Static files pass where pages do not.** `robots.txt` answered 200 on every guarded store here, so a 200
  there tells you nothing about the pages.
- **Ask politely and the store stays readable.** Every lasting block in this file was earned by one greedy
  request: a bigger page size, a deeper page, a burst of them. A small page and a low `max_pages` keep a store.
- **A search key rotates.** `tools/algolia_keys.py` checks the stored ones with the standard library and only
  reaches for a browser when one has died; a weekly workflow runs it.
- **Algolia's own limits:** `hitsPerPage` is capped at 1000, deep paging at about 20,000 hits a query, and an
  attribute is numerically filterable only if the shop configured it so (Yoox's discount is a facet but not a
  filter) — so slice a big index by category or price band rather than paging forever.
- **Check what a price field means before trusting it as a "was".** Yoox carries `retailPrice` (an *estimate* of
  the price elsewhere, marked "Estimate" or "Supplier") beside `fullPrice` (what Yoox itself asked). Only the
  second is honest as an original price.
- **A grid can repeat itself.** Intimissimi's category pages all serve the same 28 promotional products,
  men's and women's alike. If every page of a store starts with the same product, it is not the category being
  served.

## The November 2026 re-sweep

All 131 stores in the tables below were probed again in one pass, with the routes above rather than the ones the
original verdicts were reached with: a browser-shaped `Accept`, a `products.json` on the host *and* on any
`*.myshopify.com` name the page lets slip, the search credentials a page prints (Algolia and its cousins), and
what the page carries for its own scripts. One request to the home page each, then only what that named.

    served a page and named a search platform      11
    served a page carrying product data             5
    served a page with a Shopify products.json      9   (all but one already used, or robots-disallowed)
    served an Algolia index that answers            1   (Lelit)
    served a page, nothing in it recognised        28
    refused the page outright                      77

**It opened no new store, and that is worth knowing precisely.** The nine Shopify feeds are the four
`myshopify` hosts the site already reads through, The Outnet's and Buck Mason's (both `Disallow: /` on the
feed's own host), Suitsupply's (gift cards only), Soft Essentials' (already a Shopify store), and Bally's —
which is new, answers on its own host, robots-allowed, 56 products with real compare-at prices, and reports
**every one of its 208 variants out of stock**. That is not a shop with nothing in stock; it is a feed served
without inventory, and until "no variant of any product available" is read as *unknown* rather than *no*,
listing it would add 56 products the site would then hide. It is the one store here waiting on a decision in
`build.py` rather than on a route.

The leads the sweep did turn up, in the order I would take them:

1. **Bloomreach, on seven stores** — Gap, Old Navy, Banana Republic, Athleta, Ecco, Charles Tyrwhitt, Tommy
   Bahama, Indochino. Bloomreach's `dxpapi.com` is a third party's host, exactly like Algolia: it answers a
   plain `GET` given the `account_id` and `auth_key` a storefront's page or script bundle carries. Four of
   those stores are the Gap group, which is a great deal of catalogue for one integration, and the reader would
   be a sibling of `read_algolia` -- the same `fields`/`title`/`product_url` description over a different host.
2. **Constructor, on three** — Under Armour, Journeys, The Outnet. Same shape (`cnstrc.com`, a public key).
   The Outnet matters twice over: its Shopify feed is robots-disallowed, so Constructor may be the only
   honest route into it.
3. **Searchspring, on Mizuno.** Same again, smaller prize.
4. **Ecco specifically** renders an Algolia `InstantSearchInitialResults` block naming the index
   `prod_US_en-US_USD_Products`, and has a `_discount_desc` replica — a sorted-by-discount index, which no
   store on the site has. Its application id and key are not in the HTML and were not in the first fourteen of
   its forty-seven script chunks; they are worth finding.
5. **Five stores the sweep said carried product data — they do not.** Mango, Comoli, White House Black Market,
   ba&sh and Hive & Colony were all tagged as carrying an app-router payload, and every one of them serves a
   listing page with **zero prices in it**: the tag was detecting the framework, not the products. `tools/routes.py`
   now requires a page to actually price something before it claims anything about page data, and says
   "framework present but the page prices nothing" instead. Of the five, Comoli turns out to have no online shop
   at all (its site is contact, info, mail-order and stockists), White House Black Market serves its home page
   and 403s its categories, and Mango, ba&sh and Hive & Colony render their grids entirely in the browser.
   That last three are harvest candidates, not plain-request ones.

**And one store the sweep did open, by a different route than expected.** Gap's category pages carry no prices
at all to a plain request -- 2.9 MB of client-side shell, zero price strings -- but a browser renders the grid,
and the rendered page reads with `via: links` on `/browse/product.do?pid=`. It is in `stores.json` now as a
`harvest` store over fourteen categories: **390 products a run, 271 of them at 20% off or more**, with their
original prices, images and sensible categories. Its Bloomreach endpoint was never needed. Old Navy, Banana
Republic and Athleta are the same platform and the same `category.do?cid=` addresses -- Banana Republic gave 15
products and Old Navy 10 off a first guess at their paths, so they want their real category ids and a longer
settle, which is an hour's work for three more storefronts.

The 28 that served a page with nothing recognised in it are the ones to re-probe next: a home page naming
nothing does not mean a category page names nothing, and none of them has had a listing path tried.

## Verdicts used below

Every one of these describes a route, not a store. None of them means the store is done with.

- `read, …` — readable; it is in `stores.json` now and should not be in these tables.
- `feed` / `pages` / `search key` — a way in was found and written down; nobody has wired it up yet. These are
  the rows with something ready to use, and they are the cheapest work in the file.
- `guarded, address` — this client is refused at the edge however it asks, so the way on is a different route
  into the same catalogue, or the same route from somewhere else. The row says what answered (status, challenge
  name, `Server`) and whether a browser was refused too, because that is the difference between a store that
  wants a harvest and one that wants another route entirely.
- `guarded, robots` — a feed exists but its host's `robots.txt` forbids it, so that feed is not used. Only the
  feed is settled; the store is not, and another route may serve the same catalogue.
- `nothing` — reachable, and no product list in what was looked at. The row says what that was, so the next
  attempt looks somewhere new.
- `wrong-address` — the host is not the store's. Find the right one.

## In `stores.json`, not read yet

| store | host | verdict | what was found |
|---|---|---|---|
| Buck Mason | `buckmason.com` | feed | Allowed alternative, already how the site is read today: https://www.buckmason.com/sitemap-0.xml lists 1,303 /products/ pages and 423 /collections/ pages. www.buckmason.com/robots.txt disallows every address with a query string except ?variant=. buckmason.com/products.json itself **Re-swept Nov 2026:** products.json on buck-mason-usa.myshopify.com |
| Children of the Discordance | `childrenofthediscordance.com` | nothing | The listing address exists (https://childrenofthediscordance.com/item and /item?brand_id=1) but both say there are no items at the moment, so no product was seen. Worth re-checking when the shop is stocked: it should then read like Hender Scheme. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Geeks Rule | `geeksrule.jp` | nothing | The site is a WordPress page with a contact link and nothing else; no shop. /products.json is 404 and the WooCommerce Store API route does not exist (rest_no_route). No other store host is linked. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Jazzy Sport Shimokitazawa | `jsshimokita.theshop.jp` | pages | I stopped at three requests after reading that. 1,302 pages is also too many for one request each. — `https://jsshimokita.theshop.jp/sitemap.xml` **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Ostrya | `ostryaequipment.com` | nothing | The shop is closed behind Shopify's password page: the home page redirects to /password and /products.json answers 401. Not something to get past. Its robots.txt also carries a block of instructions addressed to AI agents (install a Shop skill, use its UCP/MCP endpoints); I did n **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Sasquatchfabrix | `sasquatchfabrix.com` | nothing | The WordPress /products/ archive lists 108 product posts without prices. The shop it links to, sasquatchfabrix-onlinestore-os.myshopify.com, answers 401 for /products.json (closed / password). Not something to get past. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Tacoma Fuji Records | `tacomafuji.shop-pro.jp` | pages | https://tacomafuji.shop-pro.jp/?mode=rss is a product RSS (16 newest: title, address, image, no price). No sitemap is announced. — `https://tacomafuji.shop-pro.jp/?mode=grp&gid=3169838` **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Mizuno | `usa.mizuno.com` | nothing | Category pages (/running/mens-shoes/) are filled in the browser by Searchspring, so the HTML has no product grid (3 price strings). The Searchspring site id is printed in the page (storefront search key); the one permitted trial was not run - this session's permission system decl **Re-swept Nov 2026:** uses searchspring |
| Wacko Maria | `wackomaria.co.jp` | wrong-address | wackomaria.co.jp is the brand site; its ONLINE STORE link goes to wackomaria-paradisetokyo.jp. There the front page lists /detail/N product pages by name with SOLD OUT marks but no prices; a product page (/detail/8266) has the price in meta tags (product:price:amount 200000, prod **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| 24S | `www.24s.com` | guarded, address | 403 AkamaiGHost **Re-swept Nov 2026:** page refused again (403) |
| 1017 ALYX 9SM | `www.alyx.com` | wrong-address | www.alyx.com is an unrelated one-page site titled 'Alyx' (plain Apache, 920 bytes). The brand's store host alyxstudio.com is a Shopify shop that answers 404 'This store is unavailable' for the home page and {"errors":"Not Found"} for /products.json, so there is nothing to read at **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Amazon | `www.amazon.com` | skipped | Not asked: skipped on instruction. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Artistic Nippon | `www.artisticnippon.com` | nothing | https://www.artisticnippon.com/sitemap.xml lists 500 pages, but they are hand-made HTML: a product page (2.5 KB) has the price as plain text ($110.00) and no JSON-LD, no stock field. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Asics | `www.asics.com` | guarded, address | 403 AkamaiGHost **Re-swept Nov 2026:** page refused again (403) |
| Balenciaga | `www.balenciaga.com` | guarded | Home page: 403 'Access Denied' (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Bally | `www.bally.com` | not probed |  **Re-swept Nov 2026:** products.json on www.bally.com; page carries tiles with ids |
| Beams Japan | `www.beams.co.jp` | guarded, address | 403 AkamaiGHost **Re-swept Nov 2026:** page refused again (403) |
| Bloomingdale's | `www.bloomingdales.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Blue Blue Japan | `www.bluebluejapan.com` | nothing | A brand site built on Studio.Design (script-rendered; its sitemap has only 'new-arrival' and 'information' posts). No shop and no shop link found on the host. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Bottega Veneta | `www.bottegaveneta.com` | guarded | Home page: 403 'Access Denied' (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Brooks | `www.brooksrunning.com` | guarded | /products.json: 403 'Access Denied' (Akamai). robots.txt was served. Home page not asked. **Re-swept Nov 2026:** page refused again (403) |
| Chanel | `www.chanel.com` | guarded | Home page: 403 'Access Denied'. robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Comoli | `www.comoli.jp` | nothing | No online shop: /shop lists the two physical stores. /mailorder lists 15 mail-order items; an item page (/mailorder/cashmere_knit_pants_c) shows a price in the HTML (¥107,800) but has no JSON-LD and no cart. **Re-swept Nov 2026:** page carries app-router payload **Checked again Nov 2026:** no online shop at all: the site is contact, info, mail-order and stockists. The app-router payload the sweep saw prices nothing |
| Dior | `www.dior.com` | guarded | Home page: 403 (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Farfetch | `www.farfetch.com` | guarded, address | 403 AkamaiGHost to headless Chromium, to headed Chromium and to a real signed Edge, on the home page as well as the listings **Re-swept Nov 2026:** page refused again (403) |
| Gucci | `www.gucci.com` | guarded | Home page never answered (read timed out after 30 s) while robots.txt was served at once; treated as refusing scripted clients and not retried. No explicit refusal was seen. **Re-swept Nov 2026:** page refused again (0) |
| Hermès | `www.hermes.com` | guarded | Home page: 403 with a DataDome captcha page. robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Hoka | `www.hoka.com` | guarded | Home page: 403 with a DataDome captcha page (captcha-delivery). robots.txt was served; /products.json answered 410 with an ordinary page. **Re-swept Nov 2026:** page refused again (403) |
| Lelit | `www.lelit.com` | search key | Algolia app VBT275CJRZ, index Lelit_BUS_PUS, key in the page; answers with 94 products but holds no product address, and its own are a slug of the name plus part of the SKU — `https://www.lelit.com/en-us/shop/shop-all` **Re-swept Nov 2026:** Algolia VBT275CJRZ/Lelit_BUS_PUS answers; page carries app-router payload |
| Louis Vuitton | `www.louisvuitton.com` | guarded | Home page: 403 'Access Denied' (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| LuisaViaRoma | `www.luisaviaroma.com` | guarded, address | Cloudflare 'Just a moment' cleared once in 3s, then hardened to 'Attention Required' on the home page too **Re-swept Nov 2026:** page refused again (403) |
| lululemon | `www.lululemon.com` | guarded | robots.txt and /products.json both: 403 'Access Denied' (Akamai). **Re-swept Nov 2026:** page refused again (403) |
| Macy's | `www.macys.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Maison Margiela | `www.maisonmargiela.com` | guarded | Home page: 403 'Access Denied' (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Moncler | `www.moncler.com` | guarded | Home page: 403 (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Mr Porter | `www.mrporter.com` | guarded | Home page: 403 'Access Denied'. robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| Mytheresa | `www.mytheresa.com` | nothing | 200 to a plain request, but 9.8 KB of React shell marked noindex, with no products in it **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Neiman Marcus | `www.neimanmarcus.com` | guarded | robots.txt 403 DataDome captcha; home page 403 DataDome captcha. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Net-a-Porter | `www.net-a-porter.com` | guarded, address | home page 200 in a browser, every listing under it 403 Access Denied **Re-swept Nov 2026:** page refused again (403) |
| Nexus Mods | `www.nexusmods.com` | skipped | Not asked: skipped on instruction. **Re-swept Nov 2026:** page refused again (403) |
| Nordstrom | `www.nordstrom.com` | guarded | robots.txt 200; the home page answers 200 with no content, only an obfuscated JavaScript check. /products.json was 404. First probe: /products.json answered 404. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| On | `www.on.com` | pages | Listing pages (/en-us/shop/mens/shoes, /en-us/shop/womens/gifts) come back as a 23 KB script shell with no products; the listing data comes from /api, which robots.txt disallows, so I did not ask it. 4,366 pages is too many for one request per product. — `https://www.on.com/en-us **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Onitsuka Tiger | `www.onitsukatiger.com` | nothing | /us/en-us/graphql answers 200 but {"data":{"products":{"total_count":0,"items":[]}}} for three product queries (empty search, price filter, search 'mexico'). The sitemap index (https://www.onitsukatiger.com/sitemaps/sitemap-index.xml, 41 files) has no US product sitemap, and the **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Saks Fifth Avenue | `www.saksfifthavenue.com` | guarded | robots.txt 403 DataDome captcha; home page 403 DataDome captcha. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Stone Island | `www.stoneisland.com` | guarded | Home page: 403 (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |
| The Outnet | `www.theoutnet.com` | guarded, robots | a Shopify feed exists on wcacjh-wq.myshopify.com but that host's robots.txt is Disallow: / — `https://wcacjh-wq.myshopify.com/products.json?limit=1` **Re-swept Nov 2026:** products.json on wcacjh-wq.myshopify.com; uses constructor |
| Yoshida & Co. | `www.yoshidakaban.com` | guarded | robots.txt and home page both: 403 'access forbidden' from the site's Varnish front (a flat refusal with a Japanese notice, possibly by region). Not retried. **Re-swept Nov 2026:** page refused again (403) |
| Saint Laurent | `www.ysl.com` | guarded | Home page: 403 'Access Denied' (Akamai). robots.txt was served. **Re-swept Nov 2026:** page refused again (403) |

## Probed and never added

Mall and department stores, and alternative hosts found for stores already on the site. Most are `guarded`; the
`feed` and `pages` rows are the ones with something left in them.

| store | host | verdict | what was found |
|---|---|---|---|
| Alpaca Tinkuy | `alpacatinkuy.com` | wrong-address | alpacatinkuy.com does not resolve. The mall directory lists Alpaca Tinkuy as a Level 2 kiosk with no website or social link, and a web search found no shop site for it. **Re-swept Nov 2026:** page refused again (0) |
| Athleta | `athleta.gap.com` | nothing | https://athleta.gap.com/browse/new/all-new-arrivals?cid=1006482 embeds the first styles of a category (styleId, styleName, effectivePrice, regularPrice, percentageOff, inventoryStatus per colour) but the rest load in the browser from api.gap.com; adding &pageId=1 returned the sam **Re-swept Nov 2026:** uses bloomreach; page carries app-router payload |
| ba&sh | `ba-sh.com` | no route yet | Only the first page of a category was readable: https://ba-sh.com/us/dresses/ shows 63 of 237 products with prices, but robots.txt disallows the paging parameters (*sz*, *?start=*) and /on/demandware.store/, so page 2 was not requested. /products.json is 404. **Re-swept Nov 2026:** page carries tiles with ids **Checked again Nov 2026:** US and FR listing pages served (660 KB, 175 KB), zero price strings in either; grid built in the browser |
| Banana Republic | `bananarepublic.gap.com` | nothing | https://bananarepublic.gap.com/browse/men/mens-sale?cid=26219 embeds the first styles of a category (styleId, styleName, effectivePrice, regularPrice, percentageOff, inventoryStatus per colour) but the rest load in the browser from api.gap.com; adding &pageId=1 returned the same **Re-swept Nov 2026:** uses bloomreach; page carries app-router payload |
| BYLT | `bylt-apparel.myshopify.com` | feed | byltbasics.com/products.json is 404 (headless). myshopify name found in the home page source. 204 of 248 products carry a higher compare_at_price. Product page: https://byltbasics.com/products/<handle>. — `https://bylt-apparel.myshopify.com/products.json?limit=250&page=N` **Re-swept Nov 2026:** products.json on bylt-apparel.myshopify.com |
| Hive & Colony | `hiveandcolony.com` | no route yet | A made-to-measure tailor with showrooms. hiveandcolony.com redirects to www.hiveandcolony.com; /products.json is 404 and https://www.hiveandcolony.com/collection/shoes shows a handful of styles with no listing data or sitemap entry in robots.txt. Nothing to follow for sales. **Re-swept Nov 2026:** page carries app-router payload **Checked again Nov 2026:** /collection/* pages served (188 KB), zero price strings; grid built in the browser |
| johnnie-O | `johnnie-oadmin.myshopify.com` | feed | Very large because of licensed team goods (NHL, MLB, NCAA product types); a full read is 40+ requests and 130+ MB. None of the first 250 had a compare_at_price above price. Product page: https://www.johnnie-o.com/products/<handle>. — `https://johnnie-oadmin.myshopify.com/products **Re-swept Nov 2026:** products.json on johnnie-oadmin.myshopify.com |
| Mejuri | `mejuri-us.myshopify.com` | feed | This is the US shop (name mejuri-us, prices in USD). No compare_at_price above price in the first 250. Product page: https://mejuri.com/products/<handle>. — `https://mejuri-us.myshopify.com/products.json?limit=250&page=N` **Re-swept Nov 2026:** products.json on mejuri-us.myshopify.com |
| Old Navy | `oldnavy.gap.com` | nothing | https://oldnavy.gap.com/browse/toddler/sale?cid=3043045 embeds the first styles of a category (styleId, styleName, effectivePrice, regularPrice, percentageOff, inventoryStatus per colour) but the rest load in the browser from api.gap.com; adding &pageId=1 returned the same produc **Re-swept Nov 2026:** uses bloomreach; page carries app-router payload |
| Ordinary Fits | `ordinary-fits.com` | feed | ordinary-fits.com is the brand's WordPress site; the shop is shop.ordinary-fits.online. Its RSS (/?mode=rss) lists the 15 newest only. — `https://shop.ordinary-fits.online/?mode=grp&gid=2728750&sort=n` **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Shicka | `shicka.com` | wrong-address | shicka.com is a parked domain offered for sale. The mall directory lists SHICKA (Level 1) with only an Instagram account (shicka_store) and no website; a web search found no online shop. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Diesel | `shop.diesel.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Mango | `shop.mango.com` | no route yet | https://shop.mango.com/us/en/c/women/see-all/a5143b28 embeds 86 of 7,808 products with prices; the rest load in the browser from api.shop.mango.com, whose address pattern is not in the page. Not pursued. /products.json answered 403. **Re-swept Nov 2026:** page carries app-router payload **Checked again Nov 2026:** category page served (528 KB) with zero price strings; grid is built in the browser, so a harvest candidate |
| WYE | `shopwye.com` | wrong-address | shopwye.com is a parked domain (Hostinger placeholder; https fails, http answers). The mall directory lists WYE as a Level 1 kiosk under an electronics icon with no website; it does not look like a clothing store. **Re-swept Nov 2026:** page refused again (0) |
| SKIMS | `skimsbody.myshopify.com` | feed | One product per colour. 41 of the first 250 carry a higher compare_at_price; 158 of 250 available. Product page: https://skims.com/products/<handle>. — `https://skimsbody.myshopify.com/products.json?limit=250&page=N` **Re-swept Nov 2026:** products.json on skimsbody.myshopify.com |
| Soft Essentials | `softessentials.com` | wrong-address | softessentials.com does serve a Shopify feed, but it is a Swedish-language lingerie web shop priced in SEK (shop 1724ac.myshopify.com, locale sv, country SE). The mall directory lists Soft Essentials as a Level 1 kiosk with no website, and nothing ties the two together. No site f **Re-swept Nov 2026:** products.json on softessentials.com |
| Suitsupply | `suitsupply.com` | nothing | 12 price strings a page; React grid, no product list served. Its myshopify host holds only gift cards and is Disallow: / — `https://suitsupply.com/sitemap/products/0.xml` **Re-swept Nov 2026:** products.json on suitsupply-prod.myshopify.com |
| Ecco | `us.ecco.com` | nothing | Only the first page of a category was readable: https://us.ecco.com/men/shoes embeds 52 of 305 products with listPrice, price, hasDiscount and per-size stock, but robots.txt disallows /*?page= and /api/, so paging was not requested. **Re-swept Nov 2026:** uses bloomreach; page carries app-router payload |
| Max Mara | `us.maxmara.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Pandora | `us.pandora.net` | guarded | robots.txt 200; home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| American Eagle / Aerie | `www.ae.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Aesop | `www.aesop.com` | guarded | robots.txt 200; home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Allen Edmonds | `www.allenedmonds.com` | nothing | https://www.allenedmonds.com/browse/sale/shoes shows 17 product links with prices in the HTML; no paging or JSON listing was found. The sitemap index points to a product file on another host (ecomprdsharedstorage.blob.core.windows.net), which was not read. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Altar'd State | `www.altardstate.com` | guarded | robots.txt 200; home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Ann Taylor | `www.anntaylor.com` | guarded | The category page https://www.anntaylor.com/sale/all-sale/cata000055/ was readable (96 of 1,594 products as JSON-LD), but its own page-2 link was answered by a redirect to the PerimeterX block (.../PX-ABR). Stopped there. First probe: /products.json answered 404. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Anthropologie | `www.anthropologie.com` | guarded | robots.txt 403 DataDome captcha; home page 403 DataDome captcha. First probe: /products.json answered 404. **Re-swept Nov 2026:** page refused again (403) |
| Aritzia | `www.aritzia.com` | guarded | robots.txt 403 Cloudflare challenge (Just a moment); home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Blue Nile | `www.bluenile.com` | nothing | Only the first page of a category was readable: https://www.bluenile.com/jewelry/earrings/hoop-earrings embeds 42 of 591 products with price and salePrice, but robots.txt disallows every address with a query string (/*?*), so page 2 was not requested. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Brooks Brothers | `www.brooksbrothers.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Celine | `www.celine.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 404. **Re-swept Nov 2026:** page refused again (403) |
| Champs Sports | `www.champssports.com` | nothing | Only the first page of a category was readable: https://www.champssports.com/category/mens/shoes.html embeds 48 products (128 style SKUs) with list and sale price; paging (currentPage) is disallowed in robots.txt and was not requested. Kasada script present. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Charles Tyrwhitt | `www.charlestyrwhitt.com` | nothing | https://www.charlestyrwhitt.com/us/mens-shirts/ shows the first tiles of 359 with prices, but no working paging address was found (?limit=50&offset=50 returned the same products). The US product sitemap lists 1,172 product pages; the one sampled had prices in the HTML but no JSON **Re-swept Nov 2026:** uses bloomreach; page carries tile carries JSON |
| Clarks | `www.clarks.com` | guarded | robots.txt 403 Cloudflare challenge (Just a moment); home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Coach | `www.coach.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Dick's | `www.dickssportinggoods.com` | guarded | robots.txt 200; home page 403 'Site Unavailable' block page. First probe: /products.json answered 200 not json; final https://www.dickssportinggoods.com/products.json?limit=1. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Dr. Martens | `www.drmartens.com` | guarded | robots.txt and home page both 403 with the site's own block page (Cloudflare challenge script). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Eileen Fisher | `www.eileenfisher.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Express | `www.express.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Fabletics | `www.fabletics.com` | guarded | robots.txt 200; home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Ferragamo | `www.ferragamo.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Foot Locker | `www.footlocker.com` | nothing | Only the first page of a category was readable: https://www.footlocker.com/category/mens/shoes.html embeds 48 products (161 style SKUs) with name, sku, listPrice and salePrice, but robots.txt disallows /*currentPage=* so paging was not requested. The site loads a Kasada script. A **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| FP Movement | `www.fpmovement.com` | wrong-address | www.fpmovement.com did not answer (connection timed out, twice). FP Movement is sold on the Free People site, which answered 403 with a DataDome captcha for both robots.txt and the home page, so it is guarded there. **Re-swept Nov 2026:** page refused again (0) |
| Free People | `www.freepeople.com` | guarded | robots.txt 403 DataDome captcha; home page 403 DataDome captcha. First probe: /products.json answered 404. **Re-swept Nov 2026:** page refused again (403) |
| Gap | `www.gap.com` | read, harvest | https://www.gap.com/browse/women/sweaters?cid=5745 embeds the first styles of a category (styleId, styleName, effectivePrice, regularPrice, percentageOff, inventoryStatus per colour) but the rest load in the browser from api.gap.com; adding &pageId=1 returned the same products. T **Re-swept Nov 2026:** uses bloomreach; page carries app-router payload **Checked again Nov 2026:** category pages carry no prices to a plain request (2.9 MB of shell, zero price strings); a browser renders the grid and it reads with via links on /browse/product.do?pid= -- 390 products a run, 271 at 20% off or more |
| Garage | `www.garageclothing.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Indochino | `www.indochino.com` | nothing | 9 price strings; made-to-measure, and the grid comes from an Algolia whose key is not in the page — `https://www.indochino.com/sitemap.xml` **Re-swept Nov 2026:** uses bloomreach |
| Jacadi | `www.jacadi.us` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| J.Crew | `www.jcrew.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| JD Sports | `www.jdsports.com` | guarded | robots.txt 200; home page 403 (bare block page). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Journeys | `www.journeys.com` | nothing | The collection page (https://www.journeys.com/collections/new-arrivals-collection) is filled in the browser by Constructor; the HTML has no products and the search key is not in the page source (it sits in an external script, which was not fetched). **Re-swept Nov 2026:** uses constructor |
| Kate Spade | `www.katespade.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Kurt Geiger | `www.kurtgeiger.us` | guarded | robots.txt 200; home page 403 with a DataDome captcha. First probe: /products.json answered 404. **Re-swept Nov 2026:** page refused again (403) |
| Lafayette 148 | `www.lafayette148ny.com` | guarded | robots.txt 200; home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Levi's | `www.levi.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Madewell | `www.madewell.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Manastash | `www.manastash.com` | feed | www.manastash.com is a brand site whose shop links go to mix.tokyo, a multi-brand Shopify mall; the collection 'mana-all' is Manastash only. en.manastash.com/products.json is 404 (bare nginx). — `https://mix.tokyo/collections/mana-all/products.json?limit=250&page=N` **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Michael Kors | `www.michaelkors.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Montblanc | `www.montblanc.com` | nothing | 4 price strings a page; no product list served — `https://www.montblanc.com/en-us/sitemap-customSiteMapProduct_MontblancRNA_1.xml` **Re-swept Nov 2026:** page refused again (0) |
| Oakley | `www.oakley.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| PacSun | `www.pacsun.com` | nothing | Only the first page of a category was readable: https://www.pacsun.com/womens/tops/ shows 12-15 of 3,375 products; robots.txt disallows *sz=* and Search-ShowAjax, so paging was not requested. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Peter Millar | `www.petermillar.com` | guarded | robots.txt 403 Imperva/Incapsula block; home page 403 Imperva/Incapsula block. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Primark | `www.primark.com` | guarded | robots.txt 200; home page 403 'Planned maintenance' block page. Primark does not sell online in the US in any case. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Ralph Lauren | `www.ralphlauren.com` | guarded | robots.txt 200; home page 403 (captcha page). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (307) |
| Rhude | `www.rhude.com` | feed | WRONG HOST in the list: www.rhude.com and rhude.com present a self-signed certificate and are not the store. The store is rh-ude.com. None of the first 250 had a compare_at_price. — `https://rh-ude.com/products.json?limit=250&page=N` **Re-swept Nov 2026:** page refused again (0) |
| Rodd & Gunn | `www.roddandgunn.com` | guarded | robots.txt 403 Cloudflare challenge (Just a moment); home page 403 Cloudflare challenge (Just a moment). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Rolla's | `www.rollas.com` | feed | WRONG HOST in the list: www.rollas.com / rollas.com present a certificate for another name. The store is rollasjeans.com. /products.json there is 404 and no myshopify name is printed. — `https://rollasjeans.com/collections/mens` **Re-swept Nov 2026:** page refused again (0) |
| Sunglass Hut | `www.sunglasshut.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Sun Surf | `www.sunsurf.jp` | feed | www.sunsurf.jp is a WordPress brand site (its sitemap has posts only). The products are sold on store.toyo-enterprise.co.jp, which has the sister brands too (/shopbrand/sugarcane/, /buzzricksons/, /tailortoyo/ ...). — `https://store.toyo-enterprise.co.jp/shopbrand/sunsurf/` **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Swarovski | `www.swarovski.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| The North Face | `www.thenorthface.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Tommy Bahama | `www.tommybahama.com` | nothing | https://www.tommybahama.com/en/c/men-shirts shows 24 of 173 products with prices in the HTML, but the paging address was not found (&page=1 returned the same products). The sitemap index lists 8 product files; none was read. **Re-swept Nov 2026:** uses bloomreach |
| Tumi | `www.tumi.com` | guarded | robots.txt 200; home page 403 (captcha page). First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (307) |
| Under Armour | `www.underarmour.com` | guarded | Home page readable, but the category page (https://www.underarmour.com/en-us/c/mens/shoes/) returned a 3 KB 'Client Challenge' JavaScript check. Stopped there. A Constructor search key is in the home page; it was not tried. First probe: /products.json answered 404. **Re-swept Nov 2026:** uses constructor; page carries app-router payload |
| Uniqlo | `www.uniqlo.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied' (after redirect to /us/en/). The catalogue API was not requested. First probe: /products.json answered 404. **Re-swept Nov 2026:** page refused again (403) |
| Urban Outfitters | `www.urbanoutfitters.com` | guarded | robots.txt 403 DataDome captcha; home page 403 DataDome captcha. First probe: /products.json answered 404. **Re-swept Nov 2026:** page refused again (403) |
| Versace | `www.versace.com` | guarded | Python refuses the TLS certificate chain (CERTIFICATE_VERIFY_FAILED), which is what the build would hit too; one curl request for robots.txt was answered 403. First probe: /products.json answered 0 <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate. **Re-swept Nov 2026:** page refused again (403) |
| Victoria's Secret | `www.victoriassecret.com` | nothing | https://www.victoriassecret.com/us/vs/sale embeds a few dozen of 975 products (name, price, salePrice, altPrices); the rest load in the browser from api.victoriassecret.com. Paging was not found in the page and not pursued. **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Vineyard Vines | `www.vineyardvines.com` | nothing | 58 price strings but no product list in the HTML; Chakra/React — `https://www.vineyardvines.com/sitemap_0.xml` **Re-swept Nov 2026:** page served, nothing recognised in the home page |
| Warby Parker | `www.warbyparker.com` | guarded | robots.txt 403 DataDome captcha; home page 403 DataDome captcha. First probe: /products.json answered 200 not json; final https://www.warbyparker.com/products.json?limit=1. **Re-swept Nov 2026:** page refused again (403) |
| White House Black Market | `www.whitehouseblackmarket.com` | no route yet | Home page readable, but the category page (/store/category/sale/catsaleswhbm) answered 403 Akamai 'Access Denied'. Stopped there. First probe: /products.json answered 404. **Re-swept Nov 2026:** page carries app-router payload **Checked again Nov 2026:** home page served, /store/category/... 403s; the app-router payload on the home page prices nothing |
| Zara | `www.zara.com` | guarded | robots.txt 200; home page 403 Akamai 'Service Unavailable' with a reference number. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| Zegna | `www.zegna.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |
| H&M | `www2.hm.com` | guarded | robots.txt 200; home page 403 Akamai 'Access Denied'. First probe: /products.json answered 403. **Re-swept Nov 2026:** page refused again (403) |

## Still open, in rough order of what it would be worth

This is a work queue, not a list of defeats. Every item says the next thing to try, because every item has one.

1. **Bloomreach and Constructor: a reader each, and eleven stores between them.** `read_algolia` cost about
   eighty lines and opened two of the hardest stores on the site. Bloomreach (`dxpapi.com`) is the same shape
   and the same kind of host, and the Gap group alone — Gap, Old Navy, Banana Republic, Athleta — is four
   stores off one integration, with Ecco, Charles Tyrwhitt, Tommy Bahama and Indochino behind it. Constructor
   (`cnstrc.com`) brings Under Armour, Journeys and The Outnet, and is the only honest route left into The
   Outnet, whose Shopify feed is `Disallow: /`. Find the account id and key the way the Algolia ones were
   found: the page first, then the script bundles it loads, then one browser page-load with its requests
   logged. This is the highest-yield work in the file by a wide margin.
2. **Old Navy, Banana Republic and Athleta.** Gap is read now, and these three run the same platform, the same
   `/browse/category.do?cid=` addresses and the same product links. What they need is each brand's real category
   ids (the rendered home page lists them, as Gap's did) and a longer settle before the grid is read -- a first
   guess at their paths gave 15, 10 and 0 products. Three storefronts for an hour's work, and the same
   `harvest` entry shape as Gap's, which is already written.
3. **Mango, ba&sh and Hive & Colony** render their grids in the browser and price nothing for a plain request,
   so they are harvest candidates: a listing path each and a `via` that matches what the browser leaves behind.
4. **Bally** needs a decision, not a route: its `products.json` answers on its own host, robots-allowed, 56
   products with real compare-at prices, and every one of 208 variants reporting out of stock. If `build.py`
   treated "no variant of any product in a feed is available" as *unknown* rather than *no*, it would list;
   as things stand it would add 56 products the site then hides. The same rule would cover any Shopify
   storefront that resolves stock outside its feed.
5. **The 28 that served a page with nothing recognised in it.** Only their home pages have been looked at. A
   category page is a different thing, and none has had one tried.
6. **Farfetch, Net-a-Porter, LuisaViaRoma.** All three refuse this machine's address, not its browser. Point
   `HARVEST_RUNNER` at a runner on an ordinary connection, open one listing page of each, and look for either a
   usable rendered grid (`read: harvest`) or a third-party search call (`read: algolia`, far better). Nothing
   else is missing — the readers and the workflow are already in place.
7. **Cettire's search host.** `search.cettire.com/search-ai/pro-search/api/aiRecommendForProduct` returns 96
   products a page out of 359,790, and a browser's call to it succeeds. A replay from outside gets 400 with a
   byte-identical body, so a required header was not captured before the host blocked this address. Capture the
   full request headers once from a browser that can see the site and Cettire becomes a plain-request store
   instead of a daily harvest.
8. **Talbots.** The settings that read it cleanly are in `README.md`; it needs an address it has not blocked.
9. **Prada and Loewe** are read from their pages (626 and 190 products) but carry no original price and no
   sizes. Neither makes a search call from a category page — checked — so the fuller reading has to come from
   somewhere else: their other storefronts (`/it/`, `/eu/`, a national domain) run the same catalogue and may
   be built differently, and a product page may carry JSON-LD with both prices where the grid does not.
10. **The `feed` and `pages` rows above** that nobody has wired up: each is a `stores.json` entry away, and the
   endpoint in the table was actually fetched when it was recorded. This is the cheapest work in the file.
11. **The `nothing` and `guarded` rows.** None of them has had all nine routes spent on it — most were probed
   before the search-host route and the sibling-storefront route were known to work at all. They are worth
   another pass, starting at route 3, not route 1.
12. **Everything already read, read wider.** Twenty-odd `listing` stores cover a handful of categories out of
   dozens, and a store whose `max_pages` cap is being hit (`cut` in the run log) is being read short. Adding a
   category to a store that already works is the least glamorous and most reliable way to grow the site.
