# Source and rule evidence — 27 October 2026

## Independent verdict

**PROCEED_WITH_CHANGES.** The supplied 149 application tests and separate 15
diagnostic tests reproduce, but the old collector used one API version for
every route, stopped after a transient failure, and had no public-safe Pages
export or durable automated paper state. Those are implementation defects,
not evidence of an unavailable provider or profitable strategy. The new
pipeline is separately gated; passing tests remain software evidence only.

## Actual execution-host capture

One identified, bounded live probe on 2026-10-27 20:22–20:23 UTC returned
HTTP 200 for each of these OSRS **main-game v2** routes. The host sent
`osrs-flipper/0.4 (+https://github.com/NickNicky19/osrs-flipper)` as User-Agent.
Raw response bytes and nonsecret response metadata were retained in the
local task's source evidence; this repository records hashes and facts, not
daily full-universe raw payloads on the source branch.

| Route | Response bytes | Items/points | SHA-256 |
| --- | ---: | ---: | --- |
| `/api/v2/osrs/mapping` | 866,584 | 4,674 items | `801c0e45df3e817bc46b6747596dc2ba90f4b72f9d19014c3dd5744bbec708ca` |
| `/api/v2/osrs/latest` | 343,699 | 4,551 items | `f5758c42a9dae0419a10fcc45e53a54c3fc671448908a42fc41125a33997272b` |
| `/api/v2/osrs/5m` | 172,419 | 1,922 items | `fe3082f25333b2b8b57004033d1d23c2e8a6cb4ab66366bd17fbd57a408c144d` |
| `/api/v2/osrs/1h` | 311,210 | 3,362 items | `3ee34bb057da4572fa5146bbd2e93af82e9214d851fe00b77331d3c52db8cbb9` |
| `/api/v2/osrs/24h` | 410,147 | 4,246 items | `09862205d027ef81f107f21beac6e4e622a443c65840f37311e6efb64acb5be0` |
| `/api/v2/osrs/timeseries?id=4151&lookback=7d` | 19,583 | 168 hourly points | `a44ebf60409fa2197bc4eafc893870f8978cc0b4d54a93829186cc6bf861d6df` |
| `/api/v2/osrs/timeseries?id=4151&lookback=6m` | 20,741 | 180 daily points | `92651641d206f9413c4ef3b008dd60e88aee47a0fb43c1436a97135154f1c73d` |

The provider's [route documentation](https://oldschool.runescape.wiki/w/RuneScape:Real-time_Prices)
describes v2 prices, per-route fields, beginning-of-bucket timestamps,
`timeseries` lookbacks and variable resolution. The latest `high` is a
reported instant-buy transaction and `low` a reported instant-sell transaction;
they are **not** OHLC extrema or bids/asks. `avgLowPrice` and
`avgHighPrice` may be decimals. Side `PriceVolume` is the reported total in
that interval, not units exactly at its average or an order's fill capacity.
Null/absent data remain unknown. Multiple retrievals of one bucket do not
add volume; source revisions retain receipt clocks. Backfills are bounded to
12 documented timestamp buckets and labeled as retrieved later.

The inspected [Masterminds fetcher](https://github.com/ca-gray/Flipping-Masterminds/blob/ad8cfb39493925623623a6950bea589782350291/src/main/java/com/flippingmasterminds/PriceDataFetcher.java)
uses v1 mapping and v2 market routes. This release verified v2 mapping itself
on the runtime, so `config/public-source-review.json` records v2 **for each
tested route** while retaining legacy v1 fallback for the local Workbench.
The 2021 [RuneLite announcement](https://runelite.net/blog/show/2021-03-08-Wiki-Ge-Partnership/)
says the API is offered free to developers; it does not establish full-market
or order-book coverage. Any provider denial halts dependent collection.

## Rule scope

[Jagex's 29 May 2025 update](https://secure.runescape.com/m=news/yama-cas--more?oldschool=1)
raised new-offer tax to 2%, preserved 1% on existing grandfathered offers,
kept the 5m GP per-item cap and added exemptions. The
[GE reference](https://oldschool.runescape.wiki/w/Grand_Exchange) documents
per-unit floor rounding and a named exemption list. The public paper site
models **new** offers only. It never applies a new-offer formula to a real
grandfathered offer. `public-rules.json` is a conservative ordinary-tax subset;
other items are visible with `TAX_RULE_UNREVIEWED`. Mapping's public limit is
not the user's remaining personal allowance or shared-group state. Membership,
slot availability, cancellations and exact fills remain personal/unknown.

Jagex guide prices and top-100 HTML are separate context. The previously
supplied `top100?list=3&scale=0` is **Price Falls**, not Most Traded or
volume-at-price. No HTML guide feed enters the automated pick pipeline.

## Evidence limits

The later live manual cycle parsed 4,551 screener items, found seven eligible
and issued four
conditional paper plans. It did not record an actual trade, infer a fill,
calibrate a probability, demonstrate after-tax profit, or prove that its
selection beats the frozen rules baseline. It used nine requests and 2,191,961
raw bytes, wrote a 737,284-byte public state and a 2,142,647-byte site file.
GitHub-hosted access, timing, retention and schedule execution need their own
receipts after deployment. GitHub documents [free standard hosted runners for
public repositories](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job),
[five-minute shortest schedules with possible delays or drops](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows),
and [a custom Pages workflow exception to the ten-builds/hour soft limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
